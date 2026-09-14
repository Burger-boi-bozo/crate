"""Persistent priority queue, batch controls, scheduling, events, and cleanup."""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shutil
import signal
import time
from collections import deque

from fastapi import HTTPException

from app.job_store import JobStore
from app.models import ACTIVE, RUNNING, Config, Submission

BLOCKED_CODES = {"host_blocked", "sign_in_required", "source_forbidden"}
ERROR_SUGGESTIONS = {
    "host_blocked": "The provider is throttling this server. Retry later or use a creator-provided download.",
    "sign_in_required": "This source needs an account or verification and cannot be fetched anonymously.",
    "source_forbidden": "The provider refused the media request. A fresh public link may work.",
    "format_unavailable": "Try Maximum available, Original, or a lower resolution.",
    "stalled": "Retry the job. If it repeats, the provider may be degraded.",
}


class Queue:
    def __init__(self, config: Config):
        self.config = config
        self.store = JobStore(config.data_dir)
        self.jobs: dict[str, dict] = {}
        self.pending: asyncio.PriorityQueue[tuple[int, float, str]] = asyncio.PriorityQueue()
        self.daily: deque[float] = deque()
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: list[asyncio.Task] = []
        self.claimed: set[str] = set()
        self.maintenance = False
        self.started_at = time.time()

    @staticmethod
    def workload_for(body: Submission) -> str:
        if body.format == "mp3" or (body.format == "mp4" and body.quality != "best"):
            return "heavy"
        return "light"

    def enqueue(self, job: dict):
        priority = 10 if job.get("workload") == "heavy" else 0
        self.pending.put_nowait((priority, float(job.get("created_at", time.time())), job["id"]))

    def scheduler_status(self):
        return {
            "maintenance": self.maintenance,
            "claimed": len(self.claimed),
            "heavy_claimed": sum(self.jobs.get(job_id, {}).get("workload") == "heavy" for job_id in self.claimed),
            "heavy_limit": min(self.config.heavy_workers, self.config.workers),
            "per_owner_active": self.config.per_owner_active,
            "max_load_ratio": self.config.max_load_ratio,
        }

    def load_ratio(self) -> float:
        try:
            return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
        except OSError:
            return 0.0

    def can_start(self, job: dict) -> bool:
        owner = job.get("owner")
        owner_claimed = sum(self.jobs.get(job_id, {}).get("owner") == owner for job_id in self.claimed)
        if owner_claimed >= self.config.per_owner_active:
            return False
        if job.get("workload") == "heavy":
            heavy = sum(self.jobs.get(job_id, {}).get("workload") == "heavy" for job_id in self.claimed)
            if heavy >= min(self.config.heavy_workers, self.config.workers):
                return False
            if self.claimed and self.load_ratio() > self.config.max_load_ratio:
                return False
        return True

    async def start(self):
        self.store.initialize()
        self.jobs = self.store.load_jobs()
        for job in self.jobs.values():
            job.setdefault("workload", self.workload_for(Submission(url=job["url"], format=job["format"], quality=job.get("quality", "best"))))
            job.setdefault("batch_ids", [])
            if job.get("status") in ACTIVE:
                shutil.rmtree(self.folder(job["id"]), ignore_errors=True)
                job.update(status="queued", stage="queued", progress=0, downloaded_bytes=0,
                           total_bytes=0, speed=None, eta=None, conversion_progress=None,
                           paused_from=None, error=None, error_code=None, diagnostic=None)
                self.enqueue(job)
                self.event(job, "requeued", "Requeued after server restart")
        self.expire()
        self.save()
        self.tasks = [asyncio.create_task(self.work(), name=f"crate-worker-{n + 1}") for n in range(self.config.workers)]
        self.tasks.append(asyncio.create_task(self.clean(), name="crate-cleaner"))

    def save(self):
        self.store.replace_jobs(self.jobs)

    def event(self, job, kind: str, message: str, payload: dict | None = None):
        return self.store.add_event(job, kind, message, payload)

    def events(self, owner: str, job_id: str | None = None, limit: int = 100):
        return self.store.events(owner, job_id, limit)

    def delete(self, job_id: str):
        self.jobs.pop(job_id, None)
        self.store.delete_job(job_id)

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for process in list(self.processes.values()):
            await self.kill(process)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.save()

    @staticmethod
    async def kill(process):
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    def folder(self, job_id: str):
        return self.config.data_dir / ("job-" + job_id)

    def queue_position(self, job_id: str):
        queued = sorted((job for job in self.jobs.values() if job.get("status") == "queued"),
                        key=lambda job: (10 if job.get("workload") == "heavy" else 0,
                                         job.get("created_at", 0)))
        for position, job in enumerate(queued, 1):
            if job.get("id") == job_id:
                return position
        return None

    def public(self, job):
        hidden = {"owner", "path", "serves", "paused_from"}
        data = {key: value for key, value in job.items() if key not in hidden}
        data["queue_position"] = self.queue_position(job["id"])
        data["suggestion"] = ERROR_SUGGESTIONS.get(job.get("error_code"))
        return data

    def submit(self, owner: str, body: Submission, batch_id: str | None = None):
        now = time.time()
        if self.maintenance:
            raise HTTPException(503, "Crate is in maintenance mode. Existing jobs can finish, but new jobs are paused.")
        while self.daily and self.daily[0] < now - 86400:
            self.daily.popleft()
        if self.config.daily_jobs and len(self.daily) >= self.config.daily_jobs:
            raise HTTPException(429, "Today's conversion allowance is used. Please try again tomorrow.")
        if self.config.max_queue and sum(job.get("status") in ACTIVE for job in self.jobs.values()) >= self.config.max_queue:
            raise HTTPException(429, "The queue is full. Please try again in a few minutes.")
        if shutil.disk_usage(self.config.data_dir).free < self.config.min_free_bytes:
            raise HTTPException(503, "Storage reserve is low. Please try again after older files are cleaned up.")
        if self.config.max_work_bytes and shutil.disk_usage(self.config.data_dir).free < self.config.max_work_bytes * 2:
            raise HTTPException(503, "Storage is busy. Please try again after older files expire.")
        duplicate = next((job for job in self.jobs.values()
                          if job.get("owner") == owner and job.get("url") == body.url
                          and job.get("format") == body.format and job.get("quality") == body.quality
                          and job.get("status") in ACTIVE), None)
        if duplicate:
            if batch_id and batch_id not in duplicate.setdefault("batch_ids", []):
                duplicate["batch_ids"].append(batch_id)
                self.save()
            return duplicate
        job_id = secrets.token_hex(16)
        job = dict(id=job_id, owner=owner, url=body.url, format=body.format, quality=body.quality,
                   title=body.url, status="queued", stage="queued", progress=0,
                   downloaded_bytes=0, total_bytes=0, speed=None, eta=None, conversion_progress=None,
                   created_at=now, finished_at=None, expires_at=None, error=None, error_code=None,
                   diagnostic=None, size=None, width=None, height=None, filename=None, path=None,
                   serves=0, paused_from=None, workload=self.workload_for(body),
                   batch_ids=[batch_id] if batch_id else [], attempts=0)
        self.jobs[job_id] = job
        self.daily.append(now)
        self.enqueue(job)
        self.event(job, "queued", "Added to queue", {"workload": job["workload"]})
        self.save()
        return job

    def submit_batch(self, owner: str, bodies: list[Submission]):
        batch_id = secrets.token_hex(8)
        jobs = [self.submit(owner, body, batch_id=batch_id) for body in bodies]
        return batch_id, jobs

    def batch_jobs(self, owner: str, batch_id: str):
        jobs = [job for job in self.jobs.values()
                if job.get("owner") == owner and batch_id in job.get("batch_ids", [])]
        if not jobs:
            raise HTTPException(404, "Batch not found.")
        return sorted(jobs, key=lambda job: job.get("created_at", 0))

    def batch_public(self, owner: str, batch_id: str):
        jobs = self.batch_jobs(owner, batch_id)
        ready = sum(job.get("status") == "ready" for job in jobs)
        failed = sum(job.get("status") == "failed" for job in jobs)
        active = sum(job.get("status") in ACTIVE for job in jobs)
        return {"id": batch_id, "total": len(jobs), "ready": ready, "failed": failed,
                "active": active, "complete": active == 0,
                "jobs": [self.public(job) for job in jobs]}

    def owned(self, job_id: str, owner: str):
        import hmac
        job = self.jobs.get(job_id)
        if not job or not hmac.compare_digest(job["owner"], owner):
            raise HTTPException(404, "This conversion is no longer available.")
        return job

    async def cancel(self, job):
        if job.get("status") in ACTIVE:
            job.update(status="cancelled", stage="cancelled", finished_at=time.time(),
                       speed=None, eta=None, paused_from=None)
            process = self.processes.get(job["id"])
            if process:
                await self.kill(process)
        shutil.rmtree(self.folder(job["id"]), ignore_errors=True)
        job["path"] = None
        self.event(job, "cancelled", "Job cancelled")
        self.save()
        return job

    async def pause(self, job):
        status = job.get("status")
        if status == "paused":
            return job
        if status == "queued":
            job.update(status="paused", stage="paused", paused_from="queued", speed=None, eta=None)
        elif status in RUNNING:
            process = self.processes.get(job["id"])
            if not process or process.returncode is not None:
                raise HTTPException(409, "This job is no longer running.")
            os.killpg(process.pid, signal.SIGSTOP)
            job.update(status="paused", stage="paused", paused_from=status, speed=None, eta=None)
        else:
            raise HTTPException(409, "Only queued or running jobs can be paused.")
        self.event(job, "paused", f"Paused during {status}")
        self.save()
        return job

    async def resume(self, job):
        if job.get("status") != "paused":
            raise HTTPException(409, "This job is not paused.")
        previous = job.get("paused_from") or "queued"
        process = self.processes.get(job["id"])
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGCONT)
            status = previous if previous in RUNNING else "downloading"
            job.update(status=status, stage=status, paused_from=None)
        else:
            job.update(status="queued", stage="queued", paused_from=None)
            self.enqueue(job)
        self.event(job, "resumed", "Job resumed")
        self.save()
        return job

    def retry(self, owner: str, job):
        if job.get("status") in ACTIVE:
            raise HTTPException(409, "Cancel or finish this job before retrying it.")
        retried = self.submit(owner, Submission(url=job["url"], format=job["format"], quality=job.get("quality", "best")))
        retried["attempts"] = int(job.get("attempts") or 0) + 1
        self.event(retried, "retry", f"Retried from {job['id'][:8]}", {"attempt": retried["attempts"]})
        self.save()
        return retried

    async def work(self):
        from app.job_worker import run_job
        while True:
            priority, created_at, job_id = await self.pending.get()
            try:
                job = self.jobs.get(job_id)
                if not job or job.get("status") != "queued":
                    continue
                if not self.can_start(job):
                    self.pending.put_nowait((priority, created_at, job_id))
                    await asyncio.sleep(0.2)
                    continue
                self.claimed.add(job_id)
                try:
                    await run_job(self, job)
                finally:
                    self.claimed.discard(job_id)
            finally:
                self.pending.task_done()

    async def clean(self):
        while True:
            await asyncio.sleep(30)
            self.cleanup_now()
            self.store.prune_events(time.time() - 30 * 86400)
            self.save()

    def expire(self):
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            if job.get("expires_at") and job["expires_at"] <= now and job.get("status") == "ready":
                shutil.rmtree(self.folder(job_id), ignore_errors=True)
                job.update(status="expired", stage="expired", path=None)
                self.event(job, "expired", "Stored file expired")
            if self.config.ttl and job.get("status") not in ACTIVE and now - job.get("created_at", now) > max(86400, self.config.ttl):
                self.delete(job_id)

    def cleanup_now(self):
        before = set(self.jobs)
        self.expire()
        removed_orphans = 0
        if self.config.data_dir.exists():
            for path in self.config.data_dir.glob("job-*"):
                if not path.is_dir():
                    continue
                job_id = path.name.removeprefix("job-")
                if job_id not in self.jobs:
                    shutil.rmtree(path, ignore_errors=True)
                    removed_orphans += 1
        return {"expired_or_deleted": len(before - set(self.jobs)), "orphan_directories": removed_orphans}
