"""Authenticated operator API for Crate v5."""
from __future__ import annotations

import os
import shutil
import statistics
import time
from collections import defaultdict
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.admin_auth import login_response, logout_response, require_admin
from app.models import ACTIVE
from app.version import version_payload

BLOCKED_CODES = {"host_blocked", "sign_in_required", "source_forbidden"}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=512)


class Toggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class BulkAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(cancel|retry|delete)$")
    ids: list[str] = Field(min_length=1, max_length=100)

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


def source_host(job):
    try:
        return (urlsplit(job.get("url", "")).hostname or "unknown").removeprefix("www.")
    except ValueError:
        return "unknown"


def public_job(queue, job):
    data = {key: value for key, value in job.items() if key not in {"owner", "path", "serves", "paused_from"}}
    data["queue_position"] = queue.queue_position(job["id"])
    data["source_host"] = source_host(job)
    return data


def provider_summary(jobs, now=None):
    now = now or time.time()
    providers = defaultdict(lambda: {"total": 0, "ready": 0, "failed": 0, "blocked": 0,
                                     "recent_total": 0, "recent_ready": 0, "recent_failed": 0,
                                     "last_success": None, "last_failure": None})
    for job in jobs:
        host = source_host(job)
        row = providers[host]
        row["total"] += 1
        finished = float(job.get("finished_at") or job.get("created_at") or 0)
        recent = finished > now - 86400
        if recent:
            row["recent_total"] += 1
        if job.get("status") == "ready":
            row["ready"] += 1
            row["last_success"] = max(row["last_success"] or 0, finished)
            if recent:
                row["recent_ready"] += 1
        if job.get("status") == "failed":
            row["failed"] += 1
            row["last_failure"] = max(row["last_failure"] or 0, finished)
            if recent:
                row["recent_failed"] += 1
        if job.get("error_code") in BLOCKED_CODES:
            row["blocked"] += 1
    result = []
    for host, values in providers.items():
        total = values["recent_total"] or values["total"]
        ready = values["recent_ready"] if values["recent_total"] else values["ready"]
        failed = values["recent_failed"] if values["recent_total"] else values["failed"]
        if total == 0:
            state = "unknown"
        elif values["blocked"] >= 2 and values["blocked"] / max(1, values["total"]) >= 0.5:
            state = "blocked"
        elif failed / total >= 0.5:
            state = "degraded"
        elif ready:
            state = "healthy"
        else:
            state = "unknown"
        success_rate = round(ready / max(1, ready + failed), 3) if ready + failed else None
        result.append({"host": host, **values, "state": state, "success_rate": success_rate})
    return sorted(result, key=lambda row: (-row["recent_total"], -row["total"], row["host"]))[:30]


def metrics(jobs, now=None):
    now = now or time.time()
    recent = [job for job in jobs if float(job.get("created_at") or 0) >= now - 86400]
    finished = [job for job in recent if job.get("status") in {"ready", "failed", "cancelled", "expired"}]
    ready = [job for job in finished if job.get("status") == "ready"]
    failed = [job for job in finished if job.get("status") == "failed"]
    durations = [float(job["finished_at"]) - float(job["created_at"])
                 for job in finished if job.get("finished_at") and job.get("created_at")
                 and float(job["finished_at"]) >= float(job["created_at"])]
    durations.sort()
    p95 = durations[min(len(durations) - 1, int(len(durations) * .95))] if durations else None
    return {
        "window_seconds": 86400,
        "submitted": len(recent),
        "completed": len(ready),
        "failed": len(failed),
        "failure_rate": round(len(failed) / max(1, len(ready) + len(failed)), 3),
        "output_bytes": sum(int(job.get("size") or 0) for job in ready),
        "average_duration": round(statistics.mean(durations), 2) if durations else None,
        "p95_duration": round(p95, 2) if p95 is not None else None,
    }

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
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            load = []
        return {**version_payload(), "status": "ok", "uptime_seconds": max(0, int(time.time() - queue.started_at)),
                "workers": config.workers, "active": counts["downloading"] + counts["converting"],
                "pending": queue.pending.qsize(), "counts": counts,
                "recent_failures": sum(job.get("status") == "failed" and job.get("finished_at", 0) > time.time() - 86400 for job in jobs),
                "disk": {"free": usage.free, "used": usage.used, "total": usage.total},
                "memory": memory_stats(), "load": load, "providers": provider_summary(jobs),
                "scheduler": queue.scheduler_status(), "metrics": metrics(jobs)}

    @app.get("/api/admin/jobs")
    async def jobs(request: Request):
        require_admin(request, key)
        ordered = sorted(queue.jobs.values(), key=lambda job: job.get("created_at", 0), reverse=True)
        return [public_job(queue, job) for job in ordered[:250]]

    @app.post("/api/admin/maintenance")
    async def maintenance(body: Toggle, request: Request):
        require_admin(request, key)
        queue.maintenance = body.enabled
        return {"maintenance": queue.maintenance, "active": len(queue.claimed), "pending": queue.pending.qsize()}

    @app.post("/api/admin/cleanup")
    async def cleanup(request: Request):
        require_admin(request, key)
        result = queue.cleanup_now()
        queue.save()
        return result

    @app.post("/api/admin/bulk")
    async def bulk(body: BulkAction, request: Request):
        require_admin(request, key)
        changed = []
        for job_id in dict.fromkeys(body.ids):
            job = queue.jobs.get(job_id)
            if not job:
                continue
            if body.action == "cancel" and job.get("status") in ACTIVE:
                changed.append(public_job(queue, await queue.cancel(job)))
            elif body.action == "retry" and job.get("status") not in ACTIVE:
                changed.append(public_job(queue, queue.retry(job["owner"], job)))
            elif body.action == "delete" and job.get("status") not in ACTIVE:
                queue.delete(job_id)
        return {"action": body.action, "changed": changed, "count": len(changed)}

    @app.post("/api/admin/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        if job.get("status") not in ACTIVE:
            raise HTTPException(409, "This job is not active.")
        return public_job(queue, await queue.cancel(job))

    @app.post("/api/admin/jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        return public_job(queue, queue.retry(job["owner"], job))

    @app.delete("/api/admin/jobs/{job_id}", status_code=204)
    async def delete(job_id: str, request: Request):
        require_admin(request, key)
        job = queue.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        if job.get("status") in ACTIVE:
            await queue.cancel(job)
        queue.delete(job_id)
