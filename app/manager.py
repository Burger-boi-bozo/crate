from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import build_command, resolve_tool, safe_segment, parse_progress
from .config import Settings
from .database import Database


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DownloadManager:
    def __init__(self, database: Database, settings: Settings):
        self.db = database
        self.settings = settings
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._scheduler: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.settings.download_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._scheduler = asyncio.create_task(self._schedule_loop(), name="download-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._scheduler:
            self._scheduler.cancel()
        for process in self.processes.values():
            if process.returncode is None:
                process.terminate()
        tasks = list(self.tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _schedule_loop(self) -> None:
        while not self._stop.is_set():
            try:
                active = sum(1 for task in self.tasks.values() if not task.done())
                slots = self.settings.max_concurrent - active
                if slots > 0:
                    jobs = await self.db.fetch_all(
                        "SELECT * FROM downloads WHERE status='queued' "
                        "ORDER BY priority DESC, created_at ASC LIMIT ?",
                        (slots,),
                    )
                    for job in jobs:
                        if job["id"] not in self.tasks:
                            task = asyncio.create_task(self._run_job(job), name=f"download-{job['id']}")
                            self.tasks[job["id"]] = task
                            task.add_done_callback(lambda _, job_id=job["id"]: self.tasks.pop(job_id, None))
            except asyncio.CancelledError:
                break
            except Exception:
                # Keep the scheduler alive; individual jobs record actionable errors.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        tool = resolve_tool(job["tool"], job["url"])
        destination = self.settings.download_dir / safe_segment(job["category"])
        options = json.loads(job["options_json"] or "{}")
        try:
            spec = build_command(tool, job["url"], destination, job["name"], options)
            await self.db.execute(
                "UPDATE downloads SET status='downloading', tool=?, started_at=?, updated_at=?, error=NULL WHERE id=?",
                (tool, now(), now(), job_id),
            )
            process = await asyncio.create_subprocess_exec(
                *spec.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            self.processes[job_id] = process
            recent_lines: list[str] = []
            discovered_path: str | None = None
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                recent_lines.append(line)
                recent_lines = recent_lines[-12:]
                if line.startswith("__UDM_FILE__:"):
                    discovered_path = line.split(":", 1)[1]
                progress = parse_progress(spec.progress_kind, line)
                if progress:
                    columns = ", ".join(f"{key}=?" for key in progress)
                    await self.db.execute(
                        f"UPDATE downloads SET {columns}, updated_at=? WHERE id=?",
                        (*progress.values(), now(), job_id),
                    )
            return_code = await process.wait()
            current = await self.db.fetch_one("SELECT status FROM downloads WHERE id=?", (job_id,))
            if current and current["status"] in {"paused", "cancelled"}:
                return
            if return_code == 0:
                output_path = discovered_path or self._newest_path(destination, job["started_at"])
                await self.db.execute(
                    "UPDATE downloads SET status='completed', progress=100, speed_bytes=0, eta_seconds=NULL, "
                    "output_path=?, finished_at=?, updated_at=? WHERE id=?",
                    (output_path, now(), now(), job_id),
                )
            else:
                error = "\n".join(recent_lines)[-2000:] or f"Downloader exited with code {return_code}"
                await self.db.execute(
                    "UPDATE downloads SET status='failed', speed_bytes=0, error=?, finished_at=?, updated_at=? WHERE id=?",
                    (error, now(), now(), job_id),
                )
        except FileNotFoundError as exc:
            await self._fail(job_id, f"Download tool is not installed: {exc.filename}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(job_id, str(exc))
        finally:
            self.processes.pop(job_id, None)

    @staticmethod
    def _newest_path(destination: Path, started_at: str | None) -> str | None:
        files = [path for path in destination.rglob("*") if path.is_file()]
        if not files:
            return None
        return str(max(files, key=lambda path: path.stat().st_mtime))

    async def _fail(self, job_id: str, error: str) -> None:
        await self.db.execute(
            "UPDATE downloads SET status='failed', speed_bytes=0, error=?, finished_at=?, updated_at=? WHERE id=?",
            (error[-2000:], now(), now(), job_id),
        )

    async def pause(self, job_id: str) -> bool:
        row = await self.db.fetch_one("SELECT status FROM downloads WHERE id=?", (job_id,))
        if not row or row["status"] not in {"queued", "downloading"}:
            return False
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        await self.db.execute(
            "UPDATE downloads SET status='paused', speed_bytes=0, eta_seconds=NULL, updated_at=? WHERE id=?",
            (now(), job_id),
        )
        return True

    async def resume(self, job_id: str) -> bool:
        changed = await self.db.execute(
            "UPDATE downloads SET status='queued', error=NULL, finished_at=NULL, updated_at=? "
            "WHERE id=? AND status IN ('paused', 'failed', 'cancelled')",
            (now(), job_id),
        )
        return bool(changed)

    async def cancel(self, job_id: str) -> bool:
        row = await self.db.fetch_one("SELECT status FROM downloads WHERE id=?", (job_id,))
        if not row or row["status"] in {"completed", "cancelled"}:
            return False
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        await self.db.execute(
            "UPDATE downloads SET status='cancelled', speed_bytes=0, eta_seconds=NULL, finished_at=?, updated_at=? WHERE id=?",
            (now(), now(), job_id),
        )
        return True

