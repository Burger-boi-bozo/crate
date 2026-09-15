"""Persistent v6 queue: batches, priorities, adaptive scheduling, cache reuse, retry, and cleanup."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import signal
import statistics
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

from fastapi import HTTPException

from app.job_store import JobStore
from app.models import ACTIVE, RUNNING, Config, MediaOptions, Submission

BLOCKED_CODES = {"host_blocked", "sign_in_required", "source_forbidden"}
TRANSIENT_CODES = {"host_blocked", "source_forbidden", "stalled", "source_error", "network_error"}
ERROR_SUGGESTIONS = {
    "host_blocked": "The provider is throttling this server. Crate will retry transient blocks automatically.",
    "sign_in_required": "This source needs an account or verification and cannot be fetched anonymously.",
    "source_forbidden": "The provider refused the media request. Crate can retry with a compatible fallback stream.",
    "format_unavailable": "Try Maximum available, Original, or a lower resolution.",
    "stalled": "The transfer stopped making progress. Crate will retry before giving up.",
    "unsupported_source": "Use a direct clip, episode, or public media page rather than a profile or protected page.",
}
PRIORITY = {"high": -10, "normal": 0, "low": 10}
RUNTIME_SETTINGS = {
    "workers": (1, 8), "heavy_workers": (1, 8), "per_owner_active": (1, 8),
    "max_load_ratio": (0.5, 8.0), "min_free_bytes": (0, 10 * 1024 ** 4),
    "min_available_memory": (0, 128 * 1024 ** 3), "ttl": (60, 30 * 86400),
    "max_auto_retries": (0, 5), "cache_reuse": (False, True),
}


class Queue:
    def __init__(self, config: Config):
        self.config = config
        self.store = JobStore(config.data_dir)
        self.jobs: dict[str, dict] = {}
        self.batches: dict[str, dict] = {}
        self.pending: asyncio.PriorityQueue[tuple[int, float, str]] = asyncio.PriorityQueue()
        self.daily: deque[float] = deque()
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: list[asyncio.Task] = []
        self.claimed: set[str] = set()
        self.maintenance = False
        self.drain = False
        self.started_at = time.time()
        self.webhook_batches_sent: set[str] = set()

    @staticmethod
    def workload_for(body: MediaOptions) -> str:
        heavy_video = body.format in {"mp4", "webm", "gif", "webp"} and (
            body.quality != "best" or body.video_codec not in {"auto", "copy"} or body.fps or body.crf is not None
        )
        heavy_audio = body.format in {"mp3", "m4a", "opus", "flac", "wav", "aac"}
        return "heavy" if heavy_video or heavy_audio else "light"

    @staticmethod
    def profile_for(job: dict) -> str:
        options = job.get("options") or {}
        return ":".join(str(x) for x in (
            job.get("format", "mp4"), job.get("quality", "best"),
            options.get("video_codec", "auto"), options.get("hardware", "auto"),
        ))

    def apply_saved_settings(self):
        saved = self.store.settings()
        for key, value in saved.items():
            if key not in RUNTIME_SETTINGS or not hasattr(self.config, key):
                continue
            low, high = RUNTIME_SETTINGS[key]
            if isinstance(low, bool):
                setattr(self.config, key, bool(value))
            elif isinstance(low, int) and not isinstance(low, bool):
                try: setattr(self.config, key, max(low, min(int(value), int(high))))
                except (TypeError, ValueError): pass
            else:
                try: setattr(self.config, key, max(float(low), min(float(value), float(high))))
                except (TypeError, ValueError): pass

    def update_setting(self, key: str, value):
        if key not in RUNTIME_SETTINGS:
            raise HTTPException(422, "This setting cannot be changed at runtime.")
        low, high = RUNTIME_SETTINGS[key]
        if isinstance(low, bool):
            value = bool(value)
        elif isinstance(low, int) and not isinstance(low, bool):
            value = max(low, min(int(value), int(high)))
        else:
            value = max(float(low), min(float(value), float(high)))
        setattr(self.config, key, value)
        self.store.set_setting(key, value)
        return value

    def enqueue(self, job: dict, *, delay_rank: float | None = None):
        base = PRIORITY.get(job.get("priority", "normal"), 0)
        admin_rank = int(job.get("admin_rank") or 0)
        created = float(delay_rank if delay_rank is not None else job.get("created_at", time.time()))
        self.pending.put_nowait((base + admin_rank, created, job["id"]))

    def memory_available(self) -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    def load_ratio(self) -> float:
        try:
            return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
        except OSError:
            return 0.0

    def dynamic_heavy_limit(self) -> int:
        configured = min(self.config.heavy_workers, self.config.workers)
        available = self.memory_available()
        if self.load_ratio() > self.config.max_load_ratio * 1.25:
            return 0
        if available is not None and available < self.config.min_available_memory:
            return 0
        if shutil.disk_usage(self.config.data_dir).free < self.config.min_free_bytes:
            return 0
        return configured

    def scheduler_status(self):
        available = self.memory_available()
        return {
            "maintenance": self.maintenance, "drain": self.drain,
            "claimed": len(self.claimed),
            "heavy_claimed": sum(self.jobs.get(job_id, {}).get("workload") == "heavy" for job_id in self.claimed),
            "heavy_limit": self.dynamic_heavy_limit(), "configured_heavy_limit": min(self.config.heavy_workers, self.config.workers),
            "per_owner_active": self.config.per_owner_active, "max_load_ratio": self.config.max_load_ratio,
            "load_ratio": round(self.load_ratio(), 3), "memory_available": available,
            "disk_free": shutil.disk_usage(self.config.data_dir).free,
        }

    def can_start(self, job: dict) -> bool:
        if self.drain:
            return False
        owner = job.get("owner")
        owner_claimed = sum(self.jobs.get(job_id, {}).get("owner") == owner for job_id in self.claimed)
        if owner_claimed >= self.config.per_owner_active:
            return False
        if job.get("workload") == "heavy":
            heavy = sum(self.jobs.get(job_id, {}).get("workload") == "heavy" for job_id in self.claimed)
            if heavy >= self.dynamic_heavy_limit():
                return False
            if self.claimed and self.load_ratio() > self.config.max_load_ratio:
                return False
        return True

    async def start(self):
        self.store.initialize()
        self.apply_saved_settings()
        self.jobs = self.store.load_jobs()
        self.batches = self.store.load_batches()
        now = time.time()
        for job in self.jobs.values():
            job.setdefault("options", {"format": job.get("format", "mp4"), "quality": job.get("quality", "best")})
            try:
                media = MediaOptions(**{key: value for key, value in job["options"].items() if key in MediaOptions.model_fields})
                job.setdefault("workload", self.workload_for(media))
            except Exception:
                job.setdefault("workload", "heavy")
            job.setdefault("batch_ids", [])
            job.setdefault("priority", "normal")
            job.setdefault("attempts", 0)
            job.setdefault("auto_retries", 0)
            job.setdefault("source_kind", "url")
            job.setdefault("checksum", None)
            job.setdefault("cache_hit", False)
            job.setdefault("source_duration", None)
            if job.get("status") in ACTIVE:
                job.update(status="queued", stage="queued", progress=0, phase_progress=0,
                           downloaded_bytes=0, total_bytes=0, speed=None, eta=None, conversion_progress=None,
                           paused_from=None, error=None, error_code=None, diagnostic=None, resume_work=True)
                if float(job.get("next_attempt_at") or 0) > now:
                    asyncio.create_task(self.delayed_enqueue(job, float(job["next_attempt_at"]) - now))
                else:
                    self.enqueue(job)
                self.event(job, "requeued", "Requeued after server restart", {"resume": True})
        self.expire()
        self.save()
        self.tasks = [asyncio.create_task(self.work(), name=f"crate-worker-{n + 1}") for n in range(self.config.workers)]
        self.tasks.append(asyncio.create_task(self.clean(), name="crate-cleaner"))

    def save(self):
        self.store.replace_jobs(self.jobs)
        for batch in self.batches.values():
            self.store.save_batch(batch)

    def event(self, job, kind: str, message: str, payload: dict | None = None):
        record = self.store.add_event(job, kind, message, payload)
        event_name = {"ready": "job.ready", "failed": "job.failed"}.get(kind)
        if event_name:
            try:
                asyncio.get_running_loop().create_task(self._deliver_webhooks(event_name, job))
            except RuntimeError:
                pass
        if kind in {"ready", "failed", "cancelled"}:
            for batch_id in job.get("batch_ids", []):
                if batch_id in self.webhook_batches_sent:
                    continue
                try:
                    batch_jobs = self.batch_jobs(job.get("owner", ""), batch_id)
                except HTTPException:
                    continue
                if batch_jobs and not any(item.get("status") in ACTIVE for item in batch_jobs):
                    self.webhook_batches_sent.add(batch_id)
                    try:
                        asyncio.get_running_loop().create_task(self._deliver_webhooks("batch.complete", job, batch_id=batch_id))
                    except RuntimeError:
                        pass
        return record

    async def _deliver_webhooks(self, event_name: str, job: dict, *, batch_id: str | None = None):
        hooks = [item for item in self.store.list_webhooks() if item.get("enabled") and event_name in item.get("events", [])]
        if not hooks:
            return
        public = self.public(job)
        public.pop("diagnostic", None)
        envelope = {"event": event_name, "created_at": time.time(), "job": public}
        if batch_id:
            envelope["batch"] = self.batch_public(job.get("owner", ""), batch_id)
        for hook in hooks:
            spec = {"url": hook["url"], "timeout": self.config.webhook_timeout, "payload": envelope}
            fd, raw = tempfile.mkstemp(prefix="crate-hook-", suffix=".json", dir=self.config.data_dir)
            os.close(fd); path = Path(raw)
            try:
                path.write_text(json.dumps(spec, separators=(",", ":"))); path.chmod(0o600)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "app.webhook_delivery_v6", str(path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    env={key: value for key, value in os.environ.items() if key in {"PATH","LANG","LC_ALL","SYSTEMROOT","LD_LIBRARY_PATH","SSL_CERT_FILE"}},
                    start_new_session=True,
                )
                stdout, _ = await process.communicate()
                result = json.loads(stdout or b"{}") if stdout else {}
                self.store.record_webhook_delivery(hook["id"], result.get("status"), None if result.get("ok") else result.get("error", "delivery failed"))
            except Exception as exc:
                self.store.record_webhook_delivery(hook["id"], None, f"{type(exc).__name__}: {str(exc)[:240]}")
            finally:
                path.unlink(missing_ok=True)

    def events(self, owner: str, job_id: str | None = None, limit: int = 100):
        return self.store.events(owner, job_id, limit)

    def delete(self, job_id: str):
        job = self.jobs.pop(job_id, None)
        if job:
            shutil.rmtree(self.folder(job_id), ignore_errors=True)
        self.store.delete_job(job_id)
        for batch_id, batch in list(self.batches.items()):
            if job_id in batch.get("job_ids", []):
                batch["job_ids"] = [value for value in batch["job_ids"] if value != job_id]
                batch["updated_at"] = time.time()
                if batch["job_ids"]:
                    self.store.save_batch(batch)
                else:
                    self.batches.pop(batch_id, None)
                    self.store.delete_batch(batch_id)

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
        queued = sorted((job for job in self.jobs.values() if job.get("status") in {"queued", "retry_wait"}),
                        key=lambda job: (PRIORITY.get(job.get("priority", "normal"), 0) + int(job.get("admin_rank") or 0),
                                         job.get("created_at", 0)))
        for position, job in enumerate(queued, 1):
            if job.get("id") == job_id:
                return position
        return None

    def learned_eta(self, job: dict):
        duration = float(job.get("source_duration") or 0)
        if duration <= 0:
            return None
        rows = self.store.performance(self.profile_for(job), 50)
        ratios = [float(row["wall_seconds"]) / float(row["source_duration"])
                  for row in rows if row.get("source_duration") and float(row["source_duration"]) > 0 and row.get("wall_seconds")]
        if not ratios:
            return None
        median = statistics.median(ratios)
        return max(1, round(duration * median))

    def public(self, job):
        hidden = {"owner", "path", "serves", "paused_from", "source_path", "subtitle_path", "resume_work", "fingerprint"}
        data = {key: value for key, value in job.items() if key not in hidden}
        data["queue_position"] = self.queue_position(job["id"])
        data["suggestion"] = ERROR_SUGGESTIONS.get(job.get("error_code"))
        if job.get("status") in {"queued", "retry_wait"}:
            data["learned_eta"] = self.learned_eta(job)
        return data

    @staticmethod
    def fingerprint(body: Submission) -> str:
        payload = {"url": body.url, **body.option_dict()}
        payload.pop("priority", None)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _new_job(self, owner: str, url: str, options: dict, batch_id: str | None = None, source_kind="url"):
        now = time.time()
        job_id = secrets.token_hex(16)
        media = MediaOptions(**options)
        return dict(
            id=job_id, owner=owner, url=url, format=media.format, quality=media.quality, options=media.option_dict(),
            priority=media.priority, title=url, status="queued", stage="queued", progress=0, phase_progress=0,
            downloaded_bytes=0, total_bytes=0, speed=None, eta=None, conversion_progress=None,
            created_at=now, started_at=None, finished_at=None, expires_at=None, error=None, error_code=None,
            diagnostic=None, size=None, width=None, height=None, filename=None, path=None, checksum=None,
            subtitle_path=None, subtitle_filename=None,
            serves=0, paused_from=None, workload=self.workload_for(media), batch_ids=[batch_id] if batch_id else [],
            attempts=0, auto_retries=0, next_attempt_at=None, source_kind=source_kind, source_duration=None,
            input_bytes=None, cache_hit=False, fallback_used=False,
        )

    def _reuse_ready(self, owner: str, body: Submission, batch_id: str | None):
        if not self.config.cache_reuse:
            return None
        fingerprint = self.fingerprint(body)
        source = next((job for job in self.jobs.values() if job.get("fingerprint") == fingerprint
                       and job.get("status") == "ready" and job.get("path") and Path(job["path"]).is_file()), None)
        if not source:
            return None
        job = self._new_job(owner, body.url, body.option_dict(), batch_id)
        folder = self.folder(job["id"])
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = folder / Path(source["path"]).name
        try:
            os.link(source["path"], target)
        except OSError:
            shutil.copy2(source["path"], target)
        job.update(status="ready", stage="ready", progress=100, phase_progress=100,
                   title=source.get("title"), filename=source.get("filename"), path=str(target),
                   size=target.stat().st_size, width=source.get("width"), height=source.get("height"),
                   source_duration=source.get("source_duration"), checksum=source.get("checksum"),
                   finished_at=time.time(), expires_at=time.time() + self.config.ttl, cache_hit=True,
                   fingerprint=fingerprint)
        self.jobs[job["id"]] = job
        self.event(job, "cache_hit", "Reused an identical completed output")
        self.save()
        return job

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
        fingerprint = self.fingerprint(body)
        duplicate = next((job for job in self.jobs.values() if job.get("owner") == owner
                          and job.get("fingerprint") == fingerprint and job.get("status") in ACTIVE), None)
        if duplicate:
            if batch_id and batch_id not in duplicate.setdefault("batch_ids", []):
                duplicate["batch_ids"].append(batch_id); self.save()
            return duplicate
        if reused := self._reuse_ready(owner, body, batch_id):
            return reused
        job = self._new_job(owner, body.url, body.option_dict(), batch_id)
        job["fingerprint"] = fingerprint
        self.jobs[job["id"]] = job
        self.daily.append(now)
        self.enqueue(job)
        self.event(job, "queued", "Added to queue", {"workload": job["workload"], "priority": job["priority"]})
        self.save()
        return job

    def submit_upload(self, owner: str, source: Path, original_name: str, options: MediaOptions):
        if self.maintenance:
            raise HTTPException(503, "Crate is in maintenance mode.")
        job = self._new_job(owner, f"upload:{original_name}", options.option_dict(), source_kind="upload")
        folder = self.folder(job["id"])
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        suffix = Path(original_name).suffix.lower()[:12]
        target = folder / ("source-upload" + suffix)
        shutil.move(str(source), target)
        job.update(title=original_name[:200], source_path=str(target), resume_work=True)
        self.jobs[job["id"]] = job
        self.enqueue(job)
        self.event(job, "queued", "Local media added to queue", {"source": "upload"})
        self.save()
        return job

    def submit_batch(self, owner: str, bodies: list[Submission]):
        batch_id = secrets.token_hex(8)
        now = time.time()
        jobs = [self.submit(owner, body, batch_id=batch_id) for body in bodies]
        batch = {"id": batch_id, "owner": owner, "job_ids": [job["id"] for job in jobs],
                 "created_at": now, "updated_at": now, "name": f"Batch {batch_id[:6]}"}
        self.batches[batch_id] = batch
        self.store.save_batch(batch)
        return batch_id, jobs

    def batch_jobs(self, owner: str, batch_id: str):
        batch = self.batches.get(batch_id)
        if batch and batch.get("owner") == owner:
            jobs = [self.jobs[job_id] for job_id in batch.get("job_ids", []) if job_id in self.jobs]
        else:
            jobs = [job for job in self.jobs.values() if job.get("owner") == owner and batch_id in job.get("batch_ids", [])]
        if not jobs:
            raise HTTPException(404, "Batch not found.")
        return jobs

    def batch_public(self, owner: str, batch_id: str):
        jobs = self.batch_jobs(owner, batch_id)
        ready = sum(job.get("status") == "ready" for job in jobs)
        failed = sum(job.get("status") == "failed" for job in jobs)
        active = sum(job.get("status") in ACTIVE for job in jobs)
        batch = self.batches.get(batch_id, {})
        return {"id": batch_id, "name": batch.get("name", f"Batch {batch_id[:6]}"), "total": len(jobs),
                "ready": ready, "failed": failed, "active": active, "complete": active == 0,
                "created_at": batch.get("created_at"), "jobs": [self.public(job) for job in jobs]}

    def list_batches(self, owner: str):
        return [self.batch_public(owner, batch_id) for batch_id, batch in sorted(self.batches.items(), key=lambda item: item[1].get("created_at", 0), reverse=True)
                if batch.get("owner") == owner]

    def owned(self, job_id: str, owner: str):
        import hmac
        job = self.jobs.get(job_id)
        if not job or not hmac.compare_digest(job["owner"], owner):
            raise HTTPException(404, "This conversion is no longer available.")
        return job

    def set_priority(self, job: dict, priority: str, admin_rank: int | None = None):
        if priority not in PRIORITY:
            raise HTTPException(422, "Priority must be high, normal, or low.")
        job["priority"] = priority
        if admin_rank is not None:
            job["admin_rank"] = max(-100, min(100, int(admin_rank)))
        if job.get("status") == "queued":
            self.enqueue(job)
        self.event(job, "priority", f"Priority changed to {priority}")
        self.save()
        return job

    async def cancel(self, job):
        if job.get("status") in ACTIVE:
            job.update(status="cancelled", stage="cancelled", finished_at=time.time(), speed=None, eta=None, paused_from=None)
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
        if status == "paused": return job
        if status in {"queued", "retry_wait"}:
            job.update(status="paused", stage="paused", paused_from=status, speed=None, eta=None)
        elif status in RUNNING:
            process = self.processes.get(job["id"])
            if not process or process.returncode is not None:
                raise HTTPException(409, "This job is no longer running.")
            os.killpg(process.pid, signal.SIGSTOP)
            job.update(status="paused", stage="paused", paused_from=status, speed=None, eta=None)
        else:
            raise HTTPException(409, "Only queued or running jobs can be paused.")
        self.event(job, "paused", f"Paused during {status}"); self.save(); return job

    async def resume(self, job):
        if job.get("status") != "paused": raise HTTPException(409, "This job is not paused.")
        previous = job.get("paused_from") or "queued"
        process = self.processes.get(job["id"])
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGCONT)
            status = previous if previous in RUNNING else "downloading"
            job.update(status=status, stage=status, paused_from=None)
        else:
            job.update(status="queued", stage="queued", paused_from=None); self.enqueue(job)
        self.event(job, "resumed", "Job resumed"); self.save(); return job

    def retry(self, owner: str, job):
        if job.get("status") in ACTIVE:
            raise HTTPException(409, "Cancel or finish this job before retrying it.")
        body = Submission(url=job["url"], **{k: v for k, v in (job.get("options") or {}).items() if k in MediaOptions.model_fields})
        retried = self.submit(owner, body)
        retried["attempts"] = int(job.get("attempts") or 0) + 1
        self.event(retried, "retry", f"Retried from {job['id'][:8]}", {"attempt": retried["attempts"]}); self.save(); return retried

    async def delayed_enqueue(self, job: dict, delay: float):
        await asyncio.sleep(max(0.1, delay))
        if job.get("status") == "retry_wait":
            job.update(status="queued", stage="queued", next_attempt_at=None)
            self.enqueue(job); self.save()

    def schedule_auto_retry(self, job: dict, code: str) -> bool:
        retries = int(job.get("auto_retries") or 0)
        if code not in TRANSIENT_CODES or retries >= self.config.max_auto_retries:
            return False
        delay = min(60, 2 ** retries * 5)
        job.update(status="retry_wait", stage="retry_wait", auto_retries=retries + 1,
                   next_attempt_at=time.time() + delay, error=None, error_code=None, diagnostic=None,
                   speed=None, eta=delay, resume_work=True)
        self.event(job, "auto_retry", f"Automatic retry {retries + 1} scheduled", {"delay": delay, "reason": code})
        asyncio.create_task(self.delayed_enqueue(job, delay))
        return True

    async def work(self):
        from app.job_worker import run_job
        while True:
            priority, created_at, job_id = await self.pending.get()
            try:
                job = self.jobs.get(job_id)
                if not job or job.get("status") != "queued": continue
                if not self.can_start(job):
                    self.pending.put_nowait((priority, created_at, job_id)); await asyncio.sleep(0.25); continue
                self.claimed.add(job_id)
                try: await run_job(self, job)
                finally: self.claimed.discard(job_id)
            finally:
                self.pending.task_done()

    async def clean(self):
        while True:
            await asyncio.sleep(30)
            self.cleanup_now()
            self.store.prune_events(time.time() - 30 * 86400)
            self.store.prune_shares(time.time())
            self.save()

    def expire(self):
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            if job.get("expires_at") and job["expires_at"] <= now and job.get("status") == "ready":
                shutil.rmtree(self.folder(job_id), ignore_errors=True)
                job.update(status="expired", stage="expired", path=None)
                self.event(job, "expired", "Stored file expired")
            if self.config.ttl and job.get("status") not in ACTIVE and now - job.get("created_at", now) > max(86400, self.config.ttl * 2):
                self.delete(job_id)

    def cleanup_now(self):
        before = set(self.jobs)
        self.expire()
        removed_orphans = 0
        if self.config.data_dir.exists():
            for path in self.config.data_dir.glob("job-*"):
                if path.is_dir() and path.name.removeprefix("job-") not in self.jobs:
                    shutil.rmtree(path, ignore_errors=True); removed_orphans += 1
        return {"expired_or_deleted": len(before - set(self.jobs)), "orphan_directories": removed_orphans}
