"""Run one v6 job with resumable source acquisition, isolated post-processing, retries, and checksums."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from app.errors import JobError
from app.job_events import apply_progress, source_error
from app.media_convert_v6 import probe, sha256_file
from app.runner_process import runner_environment, spawn

AUDIO_OUTPUTS = {"mp3", "mka", "m4a", "opus", "flac", "wav", "aac"}
RUNNER_OUTPUTS = {"mp4", "mp3", "mkv", "mka"}


def _advanced(job: dict) -> bool:
    options = job.get("options") or {}
    return job.get("format") not in RUNNER_OUTPUTS or any((
        options.get("start") is not None, options.get("end") is not None, options.get("fps") is not None,
        options.get("crf") is not None, options.get("video_codec", "auto") != "auto",
        options.get("audio_codec", "auto") != "auto", options.get("audio_bitrate") is not None,
        options.get("metadata"), options.get("thumbnail"), options.get("subtitles"),
        options.get("filename_template", "{title}") != "{title}",
    ))


def _runner_format(job: dict) -> str:
    if not _advanced(job) and job.get("format") in RUNNER_OUTPUTS:
        return job["format"]
    return "mka" if job.get("format") in AUDIO_OUTPUTS else "mkv"


def _clean_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")
    return re.sub(r"\s+", " ", value)[:160] or "Media clip"


def _render_filename(job: dict, result: dict) -> str:
    options = job.get("options") or {}
    template = options.get("filename_template") or "{title}"
    resolution = f"{result.get('width')}x{result.get('height')}" if result.get("width") and result.get("height") else "audio"
    values = {
        "title": result.get("title") or job.get("title") or "Media clip",
        "creator": result.get("creator") or job.get("creator") or "",
        "source": result.get("source") or "media", "format": job.get("format", "mp4"),
        "quality": job.get("quality", "best"), "resolution": resolution, "id": job["id"][:8],
    }
    for key, value in values.items(): template = template.replace("{" + key + "}", str(value or ""))
    return _clean_filename(template) + "." + job["format"]


async def read_events(queue, job, process, started):
    last_event = time.monotonic()
    result = None
    readline = asyncio.create_task(process.stdout.readline())
    try:
        while True:
            done, _ = await asyncio.wait({readline}, timeout=1)
            now = time.monotonic()
            if job.get("status") == "paused": last_event = now
            if queue.config.timeout and now - started > queue.config.timeout:
                raise JobError("This conversion took too long. Try a shorter clip.", "job_timeout")
            if job.get("status") != "paused" and queue.config.stall_timeout and now - last_event > queue.config.stall_timeout:
                raise JobError("The source stopped making progress. Retry the job or try again later.", "stalled")
            if queue.config.max_work_bytes:
                used = sum(path.stat().st_size for path in queue.folder(job["id"]).rglob("*") if path.is_file())
                if used > queue.config.max_work_bytes: raise JobError("This clip needs too much temporary space.", "work_limit")
            if not done: continue
            line = readline.result()
            if not line: break
            readline = asyncio.create_task(process.stdout.readline()); last_event = time.monotonic()
            try: event = json.loads(line)
            except (ValueError, UnicodeError): continue
            kind = event.get("kind")
            if kind == "progress" and job.get("status") != "paused":
                previous_stage = job.get("stage"); apply_progress(job, event)
                if job.get("stage") != previous_stage:
                    queue.event(job, "stage", f"Stage changed to {job['stage']}", {"stage": job["stage"]})
            elif kind == "result": result = event
            elif kind == "error": raise source_error(job, event)
        await process.wait(); return result
    finally:
        readline.cancel(); await asyncio.gather(readline, return_exceptions=True)


async def preview_metadata(job: dict, directory: Path):
    options = job.get("options") or {}
    if job.get("source_kind") != "url" or not (options.get("metadata") or options.get("thumbnail")):
        return {"title": job.get("title"), "creator": "", "source_url": ""}, None
    env = {name: value for name, value in os.environ.items() if name in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "LD_LIBRARY_PATH", "SSL_CERT_FILE"}}
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.preview_lookup", job["url"],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=25)
        data = json.loads(stdout or b"{}") if process.returncode == 0 else {}
    except Exception:
        if process.returncode is None: process.kill(); await process.wait()
        data = {}
    metadata = {"title": data.get("title") or job.get("title"), "creator": data.get("creator") or "", "source_url": job.get("url", "")}
    cover = None
    if options.get("thumbnail") and isinstance(data.get("thumbnail"), str) and data["thumbnail"].startswith("data:image/"):
        try:
            header, encoded = data["thumbnail"].split(",", 1)
            subtype = header.split("/", 1)[1].split(";", 1)[0]
            subtype = "jpg" if subtype in {"jpeg", "jpg"} else ("png" if subtype == "png" else "webp")
            payload = base64.b64decode(encoded, validate=True)
            if len(payload) <= 1_500_000:
                cover = directory / ("cover." + subtype); cover.write_bytes(payload)
        except Exception:
            cover = None
    return metadata, cover


async def postprocess(queue, job: dict, directory: Path, source: Path, runner_result: dict):
    metadata, cover = await preview_metadata(job, directory)
    subtitle = None
    if runner_result.get("subtitle"):
        candidate = (directory / str(runner_result["subtitle"])).resolve()
        if candidate.parent == directory.resolve() and candidate.is_file() and candidate.suffix.lower() in {".vtt", ".srt", ".ass", ".ssa"}:
            subtitle = candidate
    metadata["title"] = runner_result.get("title") or metadata.get("title")
    spec = {
        "source": str(source), "format": job["format"], "options": job.get("options") or {}, "metadata": metadata,
        "max_duration": queue.config.max_duration, "max_bytes": queue.config.max_bytes,
        "thumbnail": str(cover) if cover else None,
        "subtitle": str(subtitle) if subtitle else None,
    }
    spec_path = directory / "postprocess.json"
    spec_path.write_text(json.dumps(spec, separators=(",", ":"))); spec_path.chmod(0o600)
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.postprocess_v6", str(spec_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=runner_environment(directory), start_new_session=True)
    queue.processes[job["id"]] = process
    result = await read_events(queue, job, process, time.monotonic())
    if process.returncode or not result: raise JobError("The requested output could not be produced.", "postprocess_failed")
    result.update(title=runner_result.get("title") or metadata.get("title") or "Media clip", creator=metadata.get("creator") or "",
                  source=runner_result.get("source") or "media")
    if subtitle and (job.get("options") or {}).get("subtitle_mode") == "external":
        result["subtitle"] = subtitle.name
    return result


def finish_job(queue, job, directory: Path, result: dict, started_wall: float):
    output = (directory / result["file"]).resolve()
    if (output.parent != directory.resolve() or output.is_symlink() or not output.is_file()
            or output.suffix.lower() != "." + job["format"] or output.stat().st_size <= 0
            or (queue.config.max_bytes and output.stat().st_size > queue.config.max_bytes)):
        raise JobError("No usable file was produced within the size limit.", "output_invalid")
    checksum = result.get("checksum") or sha256_file(output)
    try:
        details = probe(output); source_duration = result.get("source_duration") or float(details.get("format", {}).get("duration") or 0)
    except Exception:
        source_duration = result.get("source_duration")
    filename = _render_filename(job, {**result, "width": result.get("width"), "height": result.get("height")})
    subtitle_path = None
    subtitle_filename = None
    if (job.get("options") or {}).get("subtitle_mode") == "external" and result.get("subtitle"):
        candidate = (directory / str(result["subtitle"])).resolve()
        if candidate.parent == directory.resolve() and candidate.is_file() and candidate.suffix.lower() in {".vtt", ".srt", ".ass", ".ssa"}:
            subtitle_path = str(candidate); subtitle_filename = Path(filename).stem + candidate.suffix.lower()
    job.update(status="ready", stage="ready", progress=100, phase_progress=100,
               downloaded_bytes=job.get("total_bytes") or output.stat().st_size, total_bytes=job.get("total_bytes") or output.stat().st_size,
               speed=None, eta=0, conversion_progress=100, title=str(result.get("title") or "Media clip")[:200],
               creator=str(result.get("creator") or "")[:120], filename=filename, path=str(output), size=output.stat().st_size,
               width=result.get("width"), height=result.get("height"), source_duration=source_duration,
               input_bytes=result.get("input_bytes") or job.get("input_bytes") or job.get("total_bytes"),
               checksum=checksum, video_encoder=result.get("video_encoder"), fallback_used=bool(result.get("fallback_used")),
               subtitle_path=subtitle_path, subtitle_filename=subtitle_filename,
               finished_at=time.time(), expires_at=time.time() + queue.config.ttl if queue.config.ttl else None,
               resume_work=False, runner_format=None, runner_quality=None)
    queue.event(job, "ready", "File is ready", {"size": job["size"], "filename": job["filename"], "sha256": checksum})
    wall = max(0.01, time.monotonic() - started_wall)
    queue.store.record_performance(queue.profile_for(job), source_duration, wall, job.get("input_bytes"), job["size"])
    for child in directory.iterdir():
        if child == output or (subtitle_path and child.resolve() == Path(subtitle_path)): continue
        if child.is_dir(): shutil.rmtree(child, ignore_errors=True)
        elif child.is_file(): child.unlink(missing_ok=True)


async def run_job(queue, job):
    directory = queue.folder(job["id"])
    resume = bool(job.pop("resume_work", False))
    if not resume and job.get("source_kind") != "upload": shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    started_wall = time.monotonic(); job["started_at"] = time.time()
    job.update(status="resolving", stage="resolving", error=None, error_code=None, diagnostic=None,
               progress=0, phase_progress=0, downloaded_bytes=0, total_bytes=0, speed=None, eta=None, conversion_progress=None)
    queue.event(job, "started", "Worker started job", {"resume": resume}); queue.save()
    process = None
    try:
        if job.get("source_kind") == "upload":
            source = Path(job.get("source_path") or "").resolve()
            if source.parent != directory.resolve() or not source.is_file(): raise JobError("The uploaded source is unavailable.", "upload_missing")
            runner_result = {"file": source.name, "title": job.get("title") or source.name, "width": None, "height": None}
            result = await postprocess(queue, job, directory, source, runner_result)
        else:
            job["runner_format"] = _runner_format(job)
            process = await spawn(job, directory, queue.config); queue.processes[job["id"]] = process
            runner_result = await read_events(queue, job, process, time.monotonic())
            if job.get("status") == "cancelled": return
            if process.returncode or not runner_result: raise JobError("The source could not provide this media. Try another public clip.", "source_error")
            source = (directory / runner_result["file"]).resolve()
            job["input_bytes"] = source.stat().st_size if source.is_file() else None
            if _advanced(job):
                result = await postprocess(queue, job, directory, source, runner_result)
            else:
                result = dict(runner_result)
                result["checksum"] = await asyncio.to_thread(sha256_file, source)
        finish_job(queue, job, directory, result, started_wall)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if job.get("status") != "cancelled":
            code = exc.code if isinstance(exc, JobError) else "internal_error"
            message = str(exc) if isinstance(exc, ValueError) or isinstance(exc, JobError) else "The conversion stopped unexpectedly. Please try again."
            if code == "format_unavailable" and job.get("source_kind") == "url" and job.get("quality") != "best" and not job.get("fallback_used"):
                job["fallback_used"] = True; job["runner_quality"] = "best"; code = "source_error"
            if queue.schedule_auto_retry(job, code):
                queue.save(); return
            job.update(status="failed", stage="failed", error=message[:300], error_code=code, speed=None, eta=None, finished_at=time.time())
            if not job.get("diagnostic"): job["diagnostic"] = f"{type(exc).__name__}: {message}"[:400]
            queue.event(job, "failed", message[:160], {"error_code": code, "attempts": job.get("auto_retries", 0)})
    finally:
        if process and process.returncode is None: await queue.kill(process)
        queue.processes.pop(job["id"], None)
        if job.get("status") not in {"ready", "retry_wait"} and job.get("source_kind") != "upload":
            shutil.rmtree(directory, ignore_errors=True)
        queue.save()
