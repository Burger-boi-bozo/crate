"""Authenticated operator API for Crate v4."""
from __future__ import annotations

import os
import shutil
import time
from collections import defaultdict
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.admin_auth import login_response, logout_response, require_admin
from app.models import ACTIVE
from app.version import version_payload


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=512)


def memory_stats():
    values = {}
    try:
        for line in open("/proc/meminfo"):
            name, raw = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable"}:
                values[name] = int(raw.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return {"total": values.get("MemTotal"), "available": values.get("MemAvailable")}


def public_job(queue, job):
    hidden = {"owner", "path", "serves", "paused_from", "url"}
    data = {key: value for key, value in job.items() if key not in hidden}
    data["queue_position"] = queue.queue_position(job["id"])
    try:
        data["source_host"] = (urlsplit(job.get("url", "")).hostname or "unknown").removeprefix("www.")
    except ValueError:
        data["source_host"] = "unknown"
    return data


def provider_summary(jobs):
    providers = defaultdict(lambda: {"total": 0, "ready": 0, "failed": 0, "blocked": 0})
    blocked_codes = {"host_blocked", "sign_in_required", "source_forbidden"}
    for job in jobs:
        try:
            host = (urlsplit(job.get("url", "")).hostname or "unknown").removeprefix("www.")
        except ValueError:
            host = "unknown"
        row = providers[host]
        row["total"] += 1
        if job.get("status") == "ready": row["ready"] += 1
        if job.get("status") == "failed": row["failed"] += 1
        if job.get("error_code") in blocked_codes: row["blocked"] += 1
    return [{"host": host, **values} for host, values in sorted(providers.items(), key=lambda item: -item[1]["total"])[:20]]


def install(app, queue, config, key):
    @app.post("/api/admin/login")
    async def login(body: Login):
        return login_response(body.password, config, key)

    @app.delete("/api/admin/session", status_code=204)
    async def logout():
        return logout_response(config)

    @app.get("/api/admin/status")
    async def status(request: Request):
        require_admin(request, key)
        usage = shutil.disk_usage(config.data_dir)
        jobs = list(queue.jobs.values())
        states = ("queued", "downloading", "converting", "paused", "ready", "failed", "cancelled", "expired")
        counts = {state: sum(job.get("status") == state for job in jobs) for state in states}
        recent_failures = sum(job.get("status") == "failed" and job.get("finished_at", 0) > time.time() - 86400 for job in jobs)
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            load = []
        return {**version_payload(), "status": "ok", "uptime_seconds": max(0, int(time.time() - queue.started_at)),
                "workers": config.workers, "active": counts["downloading"] + counts["converting"],
                "pending": queue.pending.qsize(), "counts": counts, "recent_failures": recent_failures,
                "disk": {"free": usage.free, "used": usage.used, "total": usage.total},
                "memory": memory_stats(), "load": load, "providers": provider_summary(jobs)}

    @app.get("/api/admin/jobs")
    async def jobs(request: Request):
        require_admin(request, key)
        ordered = sorted(queue.jobs.values(), key=lambda job: job.get("created_at", 0), reverse=True)
        return [public_job(queue, job) for job in ordered[:250]]

    @app.post("/api/admin/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job: raise HTTPException(404, "Job not found.")
        if job.get("status") not in ACTIVE: raise HTTPException(409, "This job is not active.")
        return public_job(queue, await queue.cancel(job))

    @app.post("/api/admin/jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job: raise HTTPException(404, "Job not found.")
        return public_job(queue, queue.retry(job["owner"], job))

    @app.delete("/api/admin/jobs/{job_id}", status_code=204)
    async def delete(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job: raise HTTPException(404, "Job not found.")
        await queue.cancel(job)
        queue.delete(job_id)
