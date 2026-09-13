"""Crate v2 runtime.

Keeps the existing UI/API contract while replacing the serial queue with a
small worker pool. This module is intentionally thin so the browser UI and
security policy remain unchanged while the live Proxmox runtime can evolve
without maintaining a second application.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

from app import converter


class ParallelQueue(converter.Queue):
    """Existing persistent queue with configurable parallel workers."""

    async def start(self):
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        state = self.config.data_dir / "jobs.json"
        if state.is_file():
            try:
                loaded = json.loads(state.read_text())
                self.jobs = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, TypeError):
                # A damaged state file must not prevent Crate from starting.
                self.jobs = {}

            for job in self.jobs.values():
                if job.get("status") in converter.ACTIVE:
                    # A deploy kills the old process. Requeue the job cleanly so
                    # a half-written media file cannot be served as complete.
                    shutil.rmtree(self.folder(job["id"]), ignore_errors=True)
                    job.update(status="queued", progress=0, error=None)
                    self.pending.put_nowait(job["id"])

        self.expire()
        workers = max(1, min(int(os.getenv("CRATE_WORKERS", "2")), 8))
        self.tasks = [
            asyncio.create_task(self.work(), name=f"crate-worker-{number}")
            for number in range(workers)
        ]
        self.tasks.append(asyncio.create_task(self.clean(), name="crate-cleaner"))


# create_app resolves Queue at call time, so replacing the class here keeps all
# existing endpoints, sessions, validation and frontend behavior intact.
converter.Queue = ParallelQueue
app = converter.create_app()
