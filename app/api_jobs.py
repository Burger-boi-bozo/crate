"""Job, batch, SSE, and download routes for Crate."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.models import ACTIVE, BatchSubmission, Submission


def owner_jobs(queue, identity: str):
    return [queue.public(job) for job in queue.jobs.values() if job.get("owner") == identity]


def install(app, queue, config, owner):
    @app.get("/api/jobs")
    async def jobs(request: Request):
        identity = owner(request)
        queue.expire()
        return owner_jobs(queue, identity)

    @app.post("/api/jobs", status_code=202)
    async def submit(body: Submission, request: Request):
        identity = owner(request)
        return queue.public(queue.submit(identity, body))

    @app.post("/api/batches", status_code=202)
    async def submit_batch(body: BatchSubmission, request: Request):
        identity = owner(request)
        try:
            submissions = body.submissions(config.max_batch)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        batch_id, created = queue.submit_batch(identity, submissions)
        return {"id": batch_id, "jobs": [queue.public(job) for job in created]}

    @app.get("/api/batches/{batch_id}")
    async def batch(batch_id: str, request: Request):
        return queue.batch_public(owner(request), batch_id)

    @app.post("/api/batches/{batch_id}/{action}")
    async def batch_action(batch_id: str, action: str, request: Request):
        identity = owner(request)
        jobs = queue.batch_jobs(identity, batch_id)
        if action not in {"pause", "resume", "cancel"}:
            raise HTTPException(404, "Unknown batch action.")
        for job in jobs:
            if action == "pause" and job.get("status") in ACTIVE - {"paused"}:
                await queue.pause(job)
            elif action == "resume" and job.get("status") == "paused":
                await queue.resume(job)
            elif action == "cancel" and job.get("status") in ACTIVE:
                await queue.cancel(job)
        return queue.batch_public(identity, batch_id)

    @app.get("/api/events/stream")
    async def event_stream(request: Request):
        identity = owner(request)

        async def generate():
            previous = None
            heartbeat = time.monotonic()
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                snapshot = owner_jobs(queue, identity)
                encoded = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
                now = time.monotonic()
                if encoded != previous:
                    previous = encoded
                    heartbeat = now
                    yield f"event: jobs\ndata: {encoded}\n\n"
                elif now - heartbeat >= 15:
                    heartbeat = now
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/jobs/{job_id}/pause")
    async def pause(job_id: str, request: Request):
        return queue.public(await queue.pause(queue.owned(job_id, owner(request))))

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str, request: Request):
        return queue.public(await queue.resume(queue.owned(job_id, owner(request))))

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: str, request: Request):
        identity = owner(request)
        return queue.public(queue.retry(identity, queue.owned(job_id, identity)))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        job = queue.owned(job_id, owner(request))
        if job.get("status") not in ACTIVE:
            raise HTTPException(409, "This conversion has already finished.")
        return queue.public(await queue.cancel(job))

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete(job_id: str, request: Request):
        identity = owner(request)
        job = queue.owned(job_id, identity)
        await queue.cancel(job)
        queue.delete(job_id)
        return PlainTextResponse(status_code=204)

    @app.get("/api/batches/{batch_id}/zip")
    async def batch_zip(batch_id: str, request: Request):
        identity = owner(request)
        jobs = queue.batch_jobs(identity, batch_id)
        if not jobs or any(job.get("status") != "ready" for job in jobs):
            raise HTTPException(409, "Every job in this batch must be ready before creating a ZIP.")
        temp = tempfile.NamedTemporaryFile(prefix="crate-batch-", suffix=".zip", dir=config.data_dir, delete=False)
        temp.close()
        path = Path(temp.name)
        used = set()
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, job in enumerate(jobs, 1):
                source = Path(job["path"])
                name = job.get("filename") or f"download-{index}.{job['format']}"
                original = name
                counter = 2
                while name in used:
                    stem, suffix = Path(original).stem, Path(original).suffix
                    name = f"{stem}-{counter}{suffix}"
                    counter += 1
                used.add(name)
                archive.write(source, arcname=name)
        return FileResponse(path, filename=f"crate-batch-{batch_id}.zip", media_type="application/zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.api_route("/api/jobs/{job_id}/file", methods=["GET", "HEAD"])
    async def download(job_id: str, request: Request):
        queue.expire()
        job = queue.owned(job_id, owner(request))
        if job.get("status") != "ready" or not job.get("path") or not Path(job["path"]).is_file():
            raise HTTPException(410, "This file has expired. Paste the link again to recreate it.")
        if config.max_downloads and job["serves"] >= config.max_downloads:
            raise HTTPException(429, "This file's download allowance is used. Create it again if needed.")
        job["serves"] += 1
        queue.save()
        return FileResponse(job["path"], filename=job["filename"],
                            media_type={"mp4": "video/mp4", "mp3": "audio/mpeg", "mkv": "video/x-matroska", "mka": "audio/x-matroska"}[job["format"]])
