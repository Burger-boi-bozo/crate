"""Job routes for Crate."""
import asyncio
import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from app.models import ACTIVE, Submission


def install(app, queue, config, owner):
    @app.get("/api/jobs")
    async def jobs(request: Request):
        identity = owner(request)
        queue.expire()
        return [queue.public(job) for job in queue.jobs.values() if job.get("owner") == identity]

    @app.get("/api/events")
    async def events(request: Request):
        identity = owner(request)
        channel = queue.subscribe(identity)

        async def stream():
            try:
                snapshot = [queue.public(job) for job in queue.jobs.values() if job.get("owner") == identity]
                yield "retry: 3000\n"
                yield "data: " + json.dumps({"type": "sync", "jobs": snapshot}, separators=(",", ":")) + "\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        packet = await asyncio.wait_for(channel.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield "data: " + json.dumps(packet, separators=(",", ":")) + "\n\n"
            finally:
                queue.unsubscribe(identity, channel)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        identity = owner(request)
        queue.owned(job_id, identity)
        return queue.store.events(identity, job_id, 100)

    @app.post("/api/jobs", status_code=202)
    async def submit(body: Submission, request: Request):
        identity = owner(request)
        return queue.public(queue.submit(identity, body))

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
        job = queue.owned(job_id, owner(request))
        await queue.cancel(job)
        queue.remove(job)
        return PlainTextResponse(status_code=204)

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
