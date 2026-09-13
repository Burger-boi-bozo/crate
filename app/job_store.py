"""Transactional job persistence for Crate v4."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "crate.db"
        self.legacy_path = data_dir / "jobs.json"
        self._initialized = False

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self):
        if self._initialized:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, owner TEXT NOT NULL, created_at REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")
            db.execute("CREATE INDEX IF NOT EXISTS events_owner_seq ON events(owner, seq)")
            db.execute("CREATE INDEX IF NOT EXISTS events_job_seq ON events(job_id, seq)")
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)
        self._initialized = True
        self.migrate_legacy_json()

    def migrate_legacy_json(self):
        if not self.legacy_path.is_file():
            return False
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
                return False
        try:
            loaded = json.loads(self.legacy_path.read_text())
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(loaded, dict):
            return False
        jobs = {job_id: job for job_id, job in loaded.items() if isinstance(job, dict)}
        self.replace_jobs(jobs)
        backup = self.data_dir / "jobs.json.v3-backup"
        if not backup.exists():
            os.replace(self.legacy_path, backup)
        return True

    def load_jobs(self):
        self.initialize()
        with self.connect() as db:
            rows = db.execute("SELECT id, payload FROM jobs ORDER BY updated_at").fetchall()
        jobs = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                jobs[row["id"]] = payload
        return jobs

    def replace_jobs(self, jobs: dict[str, dict]):
        self.initialize()
        now = time.time()
        rows = [(job_id, str(job.get("owner", "")), json.dumps(job, separators=(",", ":")), now) for job_id, job in jobs.items()]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM jobs")
            db.executemany("INSERT INTO jobs(id, owner, payload, updated_at) VALUES(?,?,?,?)", rows)
            db.commit()

    def delete_job(self, job_id: str):
        self.initialize()
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            db.execute("DELETE FROM events WHERE job_id=?", (job_id,))

    def add_event(self, job: dict, kind: str, message: str, payload: dict | None = None):
        self.initialize()
        created_at = time.time()
        owner = str(job.get("owner", ""))
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events(job_id, owner, created_at, kind, message, payload) VALUES(?,?,?,?,?,?)",
                                (job["id"], owner, created_at, kind, message,
                                 json.dumps(payload or {}, separators=(",", ":"))))
            seq = cursor.lastrowid
        return {"seq": seq, "job_id": job["id"], "created_at": created_at,
                "kind": kind, "message": message, "payload": payload or {}}

    def events(self, owner: str, job_id: str | None = None, limit: int = 100):
        self.initialize()
        limit = max(1, min(int(limit), 250))
        query = "SELECT seq, job_id, created_at, kind, message, payload FROM events WHERE owner=?"
        args: list[object] = [owner]
        if job_id:
            query += " AND job_id=?"
            args.append(job_id)
        query += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        result = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["payload"])
            except (ValueError, TypeError):
                payload = {}
            result.append({"seq": row["seq"], "job_id": row["job_id"], "created_at": row["created_at"],
                           "kind": row["kind"], "message": row["message"], "payload": payload})
        return result

    def prune_events(self, before: float):
        self.initialize()
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE created_at < ?", (before,))
