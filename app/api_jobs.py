"""Job and status routes for Crate."""
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.models import ACTIVE, Submission
from app.version import version_payload


def install(app, queue, config, owner):
    @app.get("/api/status")
    async def status(request: Request):
        owner(request)
        usage = shutil.disk_usage(config.data_dir)
        states = ("queued", "downloading", "converting", "paused", "ready", "failed")
        counts = {state: sum(job.get("status") == state for job in queue.jobs.values()) for state in states}
        return {"status": "ok", "uptime_seconds": max(0, int(time.time() - queue.started_at)),
                "workers": config.workers, "disk_free": usage.free, "disk_total": usage.total,
                "active": counts["downloading"] + counts["converting"], "counts": counts, **version_payload()}

    @app.get("/api/jobs")
    async def jobs(request: Request):
        identity = owner(request)
        queue.expire()
        return [queue.public(job) for job in queue.jobs.values() if job.get("owner") == identity]

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
        queue.jobs.pop(job_id, None)
        queue.save()
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
