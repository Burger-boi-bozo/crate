"""Private AMD/NVIDIA/QSV transcode worker used by Crate 6.1."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import sys
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.media_convert_v6 import hardware_capabilities
from app.models import MediaOptions
from app.version import RELEASE_VERSION

DATA_DIR = Path(os.getenv("CRATE_GPU_DATA_DIR", "/var/lib/crate-gpu")).resolve()
TOKEN = os.getenv("CRATE_GPU_WORKER_TOKEN", "")
ALLOWED_IP = os.getenv("CRATE_GPU_ALLOWED_IP", "").strip()
MAX_BYTES = int(os.getenv("CRATE_GPU_MAX_BYTES", str(8 * 1024 ** 3)))
WORKER_NAME = os.getenv("CRATE_GPU_WORKER_NAME", "rx6700xt")
WORKER_VERSION = RELEASE_VERSION
WORKER_BUILD = os.getenv("CRATE_GPU_BUILD_SHA", "dev").strip()[:12] or "dev"
app = FastAPI(title="Crate GPU Worker", docs_url=None, redoc_url=None)
jobs: dict[str, dict] = {}
processes: dict[str, asyncio.subprocess.Process] = {}


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if ALLOWED_IP and (not request.client or request.client.host != ALLOWED_IP):
        return JSONResponse({"detail": "GPU worker only accepts the Crate controller."}, status_code=403)
    header = request.headers.get("authorization", "")
    if not TOKEN or not secrets.compare_digest(header, f"Bearer {TOKEN}"):
        return JSONResponse({"detail": "Invalid GPU worker token."}, status_code=401)
    return await call_next(request)


def _caps() -> dict:
    caps = hardware_capabilities()
    encoders = caps.get("encoders") or {}
    usable = [name for codec in ("h264", "hevc") for name in encoders.get(codec, [])]
    return {**caps, "available": bool(usable), "worker": WORKER_NAME, "version": WORKER_VERSION, "build": WORKER_BUILD,
            "video_codecs": [codec for codec in ("h264", "hevc") if encoders.get(codec)]}


def _cleanup_stale(max_age: int = 3600) -> None:
    if not DATA_DIR.exists():
        return
    cutoff = time.time() - max_age
    active = set(jobs)
    for path in DATA_DIR.iterdir():
        if not path.is_dir() or path.name in active:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


@app.get("/health")
async def health():
    _cleanup_stale()
    return _caps()


async def _stop_process(job_id: str) -> None:
    process = processes.get(job_id)
    if not process or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=5)
    except Exception:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def _run(job_id: str, spec_path: Path) -> None:
    job = jobs[job_id]
    job.update(status="converting", started_at=time.time())
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.postprocess_v6", str(spec_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]), start_new_session=True,
    )
    processes[job_id] = process
    try:
        assert process.stdout is not None
        async for raw in process.stdout:
            try:
                event = json.loads(raw.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            kind = event.get("kind")
            if kind == "progress":
                job["progress"] = int(event.get("conversion_progress") or event.get("phase_progress") or 0)
                job["bytes"] = int(event.get("downloaded_bytes") or 0)
                job["eta"] = event.get("eta")
            elif kind == "result":
                job.update({key: event.get(key) for key in (
                    "file", "checksum", "width", "height", "source_duration",
                    "output_duration", "video_encoder")})
            elif kind == "error":
                job["error"] = event.get("message") or "GPU transcode failed."
        code = await process.wait()
        if code == 0 and job.get("file"):
            job.update(status="ready", progress=100, finished_at=time.time())
        elif job.get("status") != "cancelled":
            job.update(status="failed", error=job.get("error") or "GPU transcode failed.", finished_at=time.time())
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        await _stop_process(job_id)
        raise
    finally:
        processes.pop(job_id, None)


@app.post("/jobs", status_code=202)
async def create_job(
    source: UploadFile = File(...),
    format: str = Form(...),
    options: str = Form(...),
    metadata: str = Form("{}"),
):
    _cleanup_stale()
    caps = _caps()
    if not caps["available"]:
        raise HTTPException(503, "GPU encoder is unavailable.")
    if any(item.get("status") in {"queued", "converting"} for item in jobs.values()):
        raise HTTPException(429, "GPU worker is busy.")
    try:
        raw_options = json.loads(options)
        raw_options["format"] = format
        parsed = MediaOptions.model_validate(raw_options)
        meta = json.loads(metadata)
    except Exception as exc:
        raise HTTPException(400, "Invalid transcode request.") from exc
    if parsed.format not in {"mp4", "mkv"} or parsed.video_codec not in {"auto", "h264", "hevc"}:
        raise HTTPException(400, "GPU worker only accepts H.264/HEVC MP4 or MKV jobs.")
    if parsed.thumbnail or parsed.subtitle_mode == "embed" or parsed.hardware == "off":
        raise HTTPException(400, "This job requires local post-processing.")
    parsed.hardware = "auto"
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    job_id = secrets.token_hex(12)
    directory = DATA_DIR / job_id
    directory.mkdir(mode=0o700)
    source_path = directory / "source.bin"
    size = 0
    try:
        with source_path.open("wb") as output:
            while chunk := await source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Source is too large for the GPU worker.")
                output.write(chunk)
    finally:
        await source.close()
    spec = {
        "source": str(source_path), "format": parsed.format,
        "options": parsed.model_dump(), "metadata": meta if isinstance(meta, dict) else {},
        "max_duration": 0, "max_bytes": MAX_BYTES, "thumbnail": None, "subtitle": None,
    }
    spec_path = directory / "postprocess.json"
    spec_path.write_text(json.dumps(spec, separators=(",", ":")))
    spec_path.chmod(0o600)
    jobs[job_id] = {
        "id": job_id, "status": "queued", "progress": 0, "bytes": 0,
        "created_at": time.time(), "worker": WORKER_NAME, "input_bytes": size,
    }
    asyncio.create_task(_run(job_id, spec_path))
    return {"id": job_id, "worker": WORKER_NAME}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "GPU job not found.")
    return {key: value for key, value in job.items() if key != "path"}


@app.get("/jobs/{job_id}/file")
async def job_file(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "GPU job not found.")
    if job.get("status") != "ready" or not job.get("file"):
        raise HTTPException(409, "GPU output is not ready.")
    path = (DATA_DIR / job_id / str(job["file"])).resolve()
    if path.parent != (DATA_DIR / job_id).resolve() or not path.is_file():
        raise HTTPException(404, "GPU output is unavailable.")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@app.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return
    job["status"] = "cancelled"
    await _stop_process(job_id)
    shutil.rmtree(DATA_DIR / job_id, ignore_errors=True)
    jobs.pop(job_id, None)
