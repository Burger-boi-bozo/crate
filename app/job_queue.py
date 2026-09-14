"""Persistent queue, controls, events, and cleanup for Crate jobs."""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import shutil
import signal
import time
from collections import deque

from fastapi import HTTPException

from app.job_store import JobStore
from app.models import ACTIVE, RUNNING, Config, Submission


class Queue:
    def __init__(self, config: Config):
        self.config = config
        self.store = JobStore(config.data_dir)
        self.jobs: dict[str, dict] = {}
        self.pending: asyncio.Queue[str] = asyncio.Queue()
        self.daily: deque[float] = deque()
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: list[asyncio.Task] = []
        self.started_at = time.time()

    async def start(self):
        self.store.initialize()
        self.jobs = self.store.load_jobs()
        for job in self.jobs.values():
            if job.get("status") in ACTIVE:
                shutil.rmtree(self.folder(job["id"]), ignore_errors=True)
                job.update(status="queued", stage="queued", progress=0, downloaded_bytes=0,
                           total_bytes=0, speed=None, eta=None, conversion_progress=None,
                           paused_from=None, error=None, error_code=None, diagnostic=None)
                self.pending.put_nowait(job["id"])
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
                os_killpg(process.pid, signal.SIGKILL)
        await process.wait()

    def folder(self, job_id: str):
        return self.config.data_dir / ("job-" + job_id)

    def queue_position(self, job_id: str):
        queued = sorted((job for job in self.jobs.values() if job.get("status") == "queued"),
                        key=lambda job: job.get("created_at", 0))
        for position, job in enumerate(queued, 1):
            if job.get("id") == job_id:
                return position
        return None

    def public(self, job):
        hidden = {"owner", "path", "serves", "paused_from"}
        data = {key: value for key, value in job.items() if key not in hidden}
        data["queue_position"] = self.queue_position(job["id"])
        return data

    def submit(self, owner: str, body: Submission):
        now = time.time()
        while self.daily and self.daily[0] < now - 86400:
            self.daily.popleft()
        if self.config.daily_jobs and len(self.daily) >= self.config.daily_jobs:
            raise HTTPException(429, "Today's conversion allowance is used. Please try again tomorrow.")
        if self.config.max_queue and sum(job.get("status") in ACTIVE for job in self.jobs.values()) >= self.config.max_queue:
            raise HTTPException(429, "The queue is full. Please try again in a few minutes.")
        if self.config.max_work_bytes and shutil.disk_usage(self.config.data_dir).free < self.config.max_work_bytes * 2:
            raise HTTPException(503, "Storage is busy. Please try again after older files expire.")
        duplicate = next((job for job in self.jobs.values()
                          if job.get("owner") == owner and job.get("url") == body.url
                          and job.get("format") == body.format and job.get("quality") == body.quality
                          and job.get("status") in ACTIVE), None)
        if duplicate:
            return duplicate
        job_id = secrets.token_hex(16)
        job = dict(id=job_id, owner=owner, url=body.url, format=body.format, quality=body.quality,
                   title=body.url, status="queued", stage="queued", progress=0,
                   downloaded_bytes=0, total_bytes=0, speed=None, eta=None, conversion_progress=None,
                   created_at=now, finished_at=None, expires_at=None, error=None, error_code=None,
                   diagnostic=None, size=None, width=None, height=None, filename=None, path=None,
                   serves=0, paused_from=None)
        self.jobs[job_id] = job
        self.daily.append(now)
        self.pending.put_nowait(job_id)
        self.event(job, "queued", "Added to queue")
        self.save()
        return job

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
            os_killpg(process.pid, signal.SIGSTOP)
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
            os_killpg(process.pid, signal.SIGCONT)
            status = previous if previous in RUNNING else "downloading"
            job.update(status=status, stage=status, paused_from=None)
        else:
            job.update(status="queued", stage="queued", paused_from=None)
            self.pending.put_nowait(job["id"])
        self.event(job, "resumed", "Job resumed")
        self.save()
        return job

    def retry(self, owner: str, job):
        if job.get("status") in ACTIVE:
            raise HTTPException(409, "Cancel or finish this job before retrying it.")
        retried = self.submit(owner, Submission(url=job["url"], format=job["format"], quality=job.get("quality", "best")))
        self.event(retried, "retry", f"Retried from {job['id'][:8]}")
        return retried

    async def work(self):
        from app.job_worker import run_job
        while True:
            job_id = await self.pending.get()
            try:
                job = self.jobs.get(job_id)
                if job and job.get("status") == "queued":
                    await run_job(self, job)
            finally:
                self.pending.task_done()

    async def clean(self):
        while True:
            await asyncio.sleep(30)
            self.expire()
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


def os_killpg(pid: int, sig: signal.Signals):
    import os
    os.killpg(pid, sig)
