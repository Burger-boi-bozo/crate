"""User job, upload, batch, search, priority, SSE, and download routes for Crate v6."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.models import ACTIVE, BatchSubmission, MediaOptions, Submission
from app.web_auth import require_scope

MIME = {
    "mp4": "video/mp4", "mp3": "audio/mpeg", "mkv": "video/x-matroska", "mka": "audio/x-matroska",
    "m4a": "audio/mp4", "opus": "audio/ogg", "webm": "video/webm", "flac": "audio/flac",
    "wav": "audio/wav", "aac": "audio/aac", "gif": "image/gif", "webp": "image/webp",
}


class PriorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: str = Field(pattern="^(low|normal|high)$")


class BatchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(pause|resume|cancel|retry_failed)$")
    ids: list[str] | None = Field(default=None, max_length=50)


class ZipSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] | None = Field(default=None, max_length=50)


def source_host(job):
    if str(job.get("url", "")).startswith("upload:"):
        return "local upload"
    try: return (urlsplit(job.get("url", "")).hostname or "unknown").removeprefix("www.")
    except ValueError: return "unknown"


def owner_jobs(queue, identity: str, *, query="", status="", fmt="", provider=""):
    values = [queue.public(job) for job in queue.jobs.values() if job.get("owner") == identity]
    query = query.strip().lower(); status = status.strip().lower(); fmt = fmt.strip().lower(); provider = provider.strip().lower()
    if query:
        values = [job for job in values if query in " ".join((str(job.get("title", "")), str(job.get("url", "")), str(job.get("filename", "")))).lower()]
    if status: values = [job for job in values if job.get("status") == status]
    if fmt: values = [job for job in values if job.get("format") == fmt]
    if provider: values = [job for job in values if provider in source_host(job).lower()]
    return sorted(values, key=lambda job: job.get("created_at", 0), reverse=True)


def _zip_response(config, jobs, name: str):
    if not jobs or any(job.get("status") != "ready" or not job.get("path") or not Path(job["path"]).is_file() for job in jobs):
        raise HTTPException(409, "Every selected job must be ready before creating a ZIP.")
    fd, raw = tempfile.mkstemp(prefix="crate-batch-", suffix=".zip", dir=config.data_dir)
    os.close(fd); path = Path(raw)
    used = set()
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, job in enumerate(jobs, 1):
                source = Path(job["path"])
                filename = job.get("filename") or f"download-{index}.{job['format']}"
                original = filename; counter = 2
                while filename in used:
                    filename = f"{Path(original).stem}-{counter}{Path(original).suffix}"; counter += 1
                used.add(filename); archive.write(source, arcname=filename)
        return FileResponse(path, filename=name, media_type="application/zip", background=BackgroundTask(path.unlink, missing_ok=True))
    except Exception:
        path.unlink(missing_ok=True); raise


def install(app, queue, config, owner):
    @app.get("/api/jobs")
    async def jobs(request: Request, q: str = "", status: str = "", format: str = "", provider: str = ""):
        identity = owner(request); require_scope(request, "jobs:read"); queue.expire()
        return owner_jobs(queue, identity, query=q[:200], status=status[:30], fmt=format[:20], provider=provider[:120])

    @app.post("/api/jobs", status_code=202)
    async def submit(body: Submission, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        return queue.public(queue.submit(identity, body))

    @app.post("/api/uploads", status_code=202)
    async def upload(request: Request, file: UploadFile = File(...), options: str = Form("{}")):
        identity = owner(request); require_scope(request, "jobs:write")
        try:
            parsed = json.loads(options or "{}")
            media = MediaOptions(**parsed)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Upload options are invalid.") from exc
        original = Path(file.filename or "local-media").name[:180]
        suffix = Path(original).suffix[:12]
        fd, raw = tempfile.mkstemp(prefix="crate-upload-", suffix=suffix, dir=config.data_dir); os.close(fd)
        path = Path(raw); total = 0
        try:
            with path.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > config.max_upload_bytes:
                        raise HTTPException(413, f"Local uploads are limited to {config.max_upload_bytes // (1024 ** 3)} GB on this server.")
                    stream.write(chunk)
            if total <= 0: raise HTTPException(422, "The uploaded file is empty.")
            return queue.public(queue.submit_upload(identity, path, original, media))
        except Exception:
            path.unlink(missing_ok=True); raise
        finally:
            await file.close()

    @app.get("/api/batches")
    async def batches(request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        return queue.list_batches(identity)

    @app.post("/api/batches", status_code=202)
    async def submit_batch(body: BatchSubmission, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        try: submissions = body.submissions(config.max_batch)
        except ValueError as exc: raise HTTPException(422, str(exc))
        batch_id, created = queue.submit_batch(identity, submissions)
        return {"id": batch_id, "jobs": [queue.public(job) for job in created]}

    @app.get("/api/batches/{batch_id}")
    async def batch(batch_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        return queue.batch_public(identity, batch_id)

    @app.post("/api/batches/{batch_id}/action")
    async def batch_action(batch_id: str, body: BatchAction, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        jobs = queue.batch_jobs(identity, batch_id)
        selected = set(body.ids or [job["id"] for job in jobs]); jobs = [job for job in jobs if job["id"] in selected]
        for job in jobs:
            if body.action == "pause" and job.get("status") in ACTIVE - {"paused"}: await queue.pause(job)
            elif body.action == "resume" and job.get("status") == "paused": await queue.resume(job)
            elif body.action == "cancel" and job.get("status") in ACTIVE: await queue.cancel(job)
            elif body.action == "retry_failed" and job.get("status") == "failed": queue.retry(identity, job)
        return queue.batch_public(identity, batch_id)

    @app.api_route("/api/batches/{batch_id}/zip", methods=["GET", "POST"])
    async def batch_zip(batch_id: str, request: Request, selection: ZipSelection | None = None):
        identity = owner(request); require_scope(request, "jobs:read")
        jobs = queue.batch_jobs(identity, batch_id)
        ids = set(selection.ids) if selection and selection.ids else None
        if ids is not None: jobs = [job for job in jobs if job["id"] in ids]
        return _zip_response(config, jobs, f"crate-batch-{batch_id}.zip")

    @app.get("/api/events/stream")
    async def event_stream(request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        async def generate():
            previous = None; heartbeat = time.monotonic(); yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected(): return
                snapshot = owner_jobs(queue, identity); encoded = json.dumps(snapshot, separators=(",", ":"), sort_keys=True); now = time.monotonic()
                if encoded != previous:
                    previous = encoded; heartbeat = now; yield f"event: jobs\ndata: {encoded}\n\n"
                elif now - heartbeat >= 15:
                    heartbeat = now; yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/jobs/{job_id}/priority")
    async def priority(job_id: str, body: PriorityRequest, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        return queue.public(queue.set_priority(queue.owned(job_id, identity), body.priority))

    @app.post("/api/jobs/{job_id}/pause")
    async def pause(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write"); return queue.public(await queue.pause(queue.owned(job_id, identity)))

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write"); return queue.public(await queue.resume(queue.owned(job_id, identity)))

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write"); return queue.public(queue.retry(identity, queue.owned(job_id, identity)))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write"); job = queue.owned(job_id, identity)
        if job.get("status") not in ACTIVE: raise HTTPException(409, "This conversion has already finished.")
        return queue.public(await queue.cancel(job))

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write"); job = queue.owned(job_id, identity)
        if job.get("status") in ACTIVE: await queue.cancel(job)
        queue.delete(job_id); return PlainTextResponse(status_code=204)

    @app.api_route("/api/jobs/{job_id}/subtitle", methods=["GET", "HEAD"])
    async def subtitle(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:read"); job = queue.owned(job_id, identity)
        path = Path(job.get("subtitle_path") or "")
        if job.get("status") != "ready" or not path.is_file():
            raise HTTPException(404, "No external subtitle file is available for this job.")
        return FileResponse(path, filename=job.get("subtitle_filename") or path.name, media_type="text/vtt" if path.suffix.lower() == ".vtt" else "text/plain")

    @app.api_route("/api/jobs/{job_id}/file", methods=["GET", "HEAD"])
    async def download(job_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:read"); queue.expire(); job = queue.owned(job_id, identity)
        if job.get("status") != "ready" or not job.get("path") or not Path(job["path"]).is_file():
            raise HTTPException(410, "This file has expired. Paste the link again to recreate it.")
        if config.max_downloads and job["serves"] >= config.max_downloads: raise HTTPException(429, "This file's download allowance is used.")
        job["serves"] += 1; queue.save()
        return FileResponse(job["path"], filename=job["filename"], media_type=MIME.get(job["format"], "application/octet-stream"))
