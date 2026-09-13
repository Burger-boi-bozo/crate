"""Run one queued conversion and persist its result."""
from __future__ import annotations

import asyncio
import json
import shutil
import time

from app.errors import JobError
from app.job_events import apply_progress, source_error
from app.runner_process import spawn


async def read_events(queue, job, process, started):
    last_event = time.monotonic()
    last_saved = last_event
    result = None
    readline = asyncio.create_task(process.stdout.readline())
    try:
        while True:
            done, _ = await asyncio.wait({readline}, timeout=1)
            now = time.monotonic()
            if job.get("status") == "paused":
                last_event = now
            if queue.config.timeout and now - started > queue.config.timeout:
                raise JobError("This conversion took too long. Try a shorter clip.", "job_timeout")
            if job.get("status") != "paused" and queue.config.stall_timeout and now - last_event > queue.config.stall_timeout:
                raise JobError("The source stopped making progress. Retry the job or try again later.", "stalled")
            if queue.config.max_work_bytes:
                used = sum(path.stat().st_size for path in queue.folder(job["id"]).rglob("*") if path.is_file())
                if used > queue.config.max_work_bytes:
                    raise JobError("This clip needs too much temporary space.", "work_limit")
            if not done:
                continue
            line = readline.result()
            if not line:
                break
            readline = asyncio.create_task(process.stdout.readline())
            last_event = time.monotonic()
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            kind = event.get("kind")
            if kind == "progress" and job.get("status") != "paused":
                previous_stage = job.get("stage")
                apply_progress(job, event)
                queue.progress_changed(job)
                if job.get("stage") != previous_stage:
                    queue.event(job, "stage", f"Stage changed to {job['stage']}", {"stage": job["stage"]})
                if last_event - last_saved >= 5:
                    queue.save()
                    last_saved = last_event
            elif kind == "result":
                result = event
            elif kind == "error":
                raise source_error(job, event)
        await process.wait()
        return result
    finally:
        readline.cancel()
        await asyncio.gather(readline, return_exceptions=True)


def finish_job(queue, job, directory, result):
    output = (directory / result["file"]).resolve()
    if (output.parent != directory.resolve() or output.is_symlink() or not output.is_file()
            or output.suffix != "." + job["format"] or output.stat().st_size <= 0
            or (queue.config.max_bytes and output.stat().st_size > queue.config.max_bytes)):
        raise JobError("No usable file was produced within the size limit.", "output_invalid")
    job.update(status="ready", stage="ready", progress=100,
               downloaded_bytes=job.get("total_bytes") or output.stat().st_size,
               total_bytes=job.get("total_bytes") or output.stat().st_size,
               speed=None, eta=0, conversion_progress=100, title=result["title"][:200],
               filename=result["filename"], path=str(output), size=output.stat().st_size,
               width=result.get("width"), height=result.get("height"), finished_at=time.time(),
               expires_at=time.time() + queue.config.ttl if queue.config.ttl else None)
    queue.event(job, "ready", "File is ready", {"size": job["size"], "filename": job["filename"]})
    for child in directory.iterdir():
        if child == output:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        elif child.is_file():
            child.unlink(missing_ok=True)


async def run_job(queue, job):
    directory = queue.folder(job["id"])
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(mode=0o700)
    job.update(status="downloading", stage="downloading", error=None, error_code=None,
               diagnostic=None, progress=0, downloaded_bytes=0, total_bytes=0,
               speed=None, eta=None, conversion_progress=None)
    queue.event(job, "started", "Worker started job")
    queue.save()
    process = None
    try:
        process = await spawn(job, directory, queue.config)
        queue.processes[job["id"]] = process
        result = await read_events(queue, job, process, time.monotonic())
        if job.get("status") == "cancelled":
            return
        if process.returncode or not result:
            raise JobError("The source could not provide this media. Try another public clip.", "source_error")
        finish_job(queue, job, directory, result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if job.get("status") != "cancelled":
            code = exc.code if isinstance(exc, JobError) else "internal_error"
            message = str(exc) if isinstance(exc, ValueError) else "The conversion stopped unexpectedly. Please try again."
            job.update(status="failed", stage="failed", error=message[:300], error_code=code,
                       speed=None, eta=None, finished_at=time.time())
            if not job.get("diagnostic"):
                job["diagnostic"] = f"{type(exc).__name__}: {message}"[:400]
            queue.event(job, "failed", message[:160], {"error_code": code})
    finally:
        if process:
            await queue.kill(process)
        queue.processes.pop(job["id"], None)
        if job.get("status") != "ready":
            shutil.rmtree(directory, ignore_errors=True)
        queue.save()
