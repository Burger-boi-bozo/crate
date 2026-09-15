"""Transactional SQLite persistence for Crate v6."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _load(raw, default=None):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {} if default is None else default


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "crate.db"
        self.legacy_path = data_dir / "jobs.json"

    def connect(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, owner TEXT NOT NULL,
          created_at REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS events_owner_seq ON events(owner, seq);
        CREATE INDEX IF NOT EXISTS events_job_seq ON events(job_id, seq);
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS batches_owner_updated ON batches(owner, updated_at DESC);
        CREATE TABLE IF NOT EXISTS shares (
          token_hash TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner TEXT NOT NULL, payload TEXT NOT NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS shares_owner_updated ON shares(owner, updated_at DESC);
        CREATE TABLE IF NOT EXISTS api_tokens (
          id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL, owner TEXT NOT NULL,
          scopes TEXT NOT NULL, created_at REAL NOT NULL, last_used REAL, revoked_at REAL
        );
        CREATE INDEX IF NOT EXISTS api_tokens_owner ON api_tokens(owner, created_at DESC);
        CREATE TABLE IF NOT EXISTS webhooks (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, events TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          last_status INTEGER, last_error TEXT, last_delivery REAL
        );
        CREATE TABLE IF NOT EXISTS audit (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, actor TEXT NOT NULL,
          action TEXT NOT NULL, target TEXT, details TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS audit_created ON audit(created_at DESC);
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS performance (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, profile TEXT NOT NULL,
          source_duration REAL, wall_seconds REAL NOT NULL, input_bytes INTEGER, output_bytes INTEGER
        );
        CREATE INDEX IF NOT EXISTS performance_profile ON performance(profile, created_at DESC);
        CREATE TABLE IF NOT EXISTS passkeys (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, credential_id TEXT UNIQUE NOT NULL,
          public_key TEXT NOT NULL, sign_count INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, last_used REAL
        );
        """)
        return connection

    def initialize(self):
        with self.connect():
            pass
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)
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
        with self.connect() as db:
            rows = db.execute("SELECT id, payload FROM jobs ORDER BY updated_at").fetchall()
        return {row["id"]: payload for row in rows if isinstance((payload := _load(row["payload"], None)), dict)}

    def replace_jobs(self, jobs: dict[str, dict]):
        now = time.time()
        rows = [(job_id, str(job.get("owner", "")), _json(job), now) for job_id, job in jobs.items()]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM jobs")
            db.executemany("INSERT INTO jobs(id, owner, payload, updated_at) VALUES(?,?,?,?)", rows)
            db.commit()

    def delete_job(self, job_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            db.execute("DELETE FROM events WHERE job_id=?", (job_id,))

    def add_event(self, job: dict, kind: str, message: str, payload: dict | None = None):
        created_at = time.time()
        owner = str(job.get("owner", ""))
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events(job_id, owner, created_at, kind, message, payload) VALUES(?,?,?,?,?,?)",
                                (job["id"], owner, created_at, kind, message, _json(payload or {})))
            seq = cursor.lastrowid
        return {"seq": seq, "job_id": job["id"], "created_at": created_at,
                "kind": kind, "message": message, "payload": payload or {}}

    def events(self, owner: str, job_id: str | None = None, limit: int = 100):
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
        return [{"seq": row["seq"], "job_id": row["job_id"], "created_at": row["created_at"],
                 "kind": row["kind"], "message": row["message"], "payload": _load(row["payload"], {})}
                for row in reversed(rows)]

    def prune_events(self, before: float):
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE created_at < ?", (before,))

    # Persistent batches -------------------------------------------------
    def save_batch(self, batch: dict):
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO batches(id, owner, payload, updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,payload=excluded.payload,updated_at=excluded.updated_at",
                       (batch["id"], batch["owner"], _json(batch), now))

    def load_batches(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM batches ORDER BY updated_at").fetchall()
        return {row["id"]: _load(row["payload"], {}) for row in rows}

    def delete_batch(self, batch_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    # Temporary share links ----------------------------------------------
    def create_share(self, token_hash: str, job_id: str, owner: str, payload: dict):
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO shares(token_hash,job_id,owner,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (token_hash, job_id, owner, _json(payload), now, now))

    def get_share(self, token_hash: str):
        with self.connect() as db:
            row = db.execute("SELECT token_hash,job_id,owner,payload,created_at,updated_at FROM shares WHERE token_hash=?", (token_hash,)).fetchone()
        if not row:
            return None
        return {"token_hash": row["token_hash"], "job_id": row["job_id"], "owner": row["owner"],
                "created_at": row["created_at"], "updated_at": row["updated_at"], **_load(row["payload"], {})}

    def update_share(self, token_hash: str, payload: dict):
        with self.connect() as db:
            db.execute("UPDATE shares SET payload=?,updated_at=? WHERE token_hash=?", (_json(payload), time.time(), token_hash))

    def list_shares(self, owner: str):
        with self.connect() as db:
            rows = db.execute("SELECT token_hash,job_id,payload,created_at,updated_at FROM shares WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()
        return [{"token_hash": row["token_hash"], "job_id": row["job_id"], "created_at": row["created_at"],
                 "updated_at": row["updated_at"], **_load(row["payload"], {})} for row in rows]

    def delete_share(self, token_hash: str):
        with self.connect() as db:
            db.execute("DELETE FROM shares WHERE token_hash=?", (token_hash,))

    def prune_shares(self, before: float):
        with self.connect() as db:
            rows = db.execute("SELECT token_hash,payload FROM shares").fetchall()
            for row in rows:
                payload = _load(row["payload"], {})
                if float(payload.get("expires_at") or 0) and float(payload["expires_at"]) < before:
                    db.execute("DELETE FROM shares WHERE token_hash=?", (row["token_hash"],))

    # API tokens ---------------------------------------------------------
    def create_api_token(self, token_id: str, token_hash: str, name: str, owner: str, scopes: list[str]):
        with self.connect() as db:
            db.execute("INSERT INTO api_tokens(id,token_hash,name,owner,scopes,created_at) VALUES(?,?,?,?,?,?)",
                       (token_id, token_hash, name, owner, _json(scopes), time.time()))

    def verify_api_token(self, token_hash: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL", (token_hash,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
        return {"id": row["id"], "name": row["name"], "owner": row["owner"],
                "scopes": _load(row["scopes"], []), "created_at": row["created_at"], "last_used": row["last_used"]}

    def list_api_tokens(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,name,owner,scopes,created_at,last_used,revoked_at FROM api_tokens ORDER BY created_at DESC").fetchall()
        return [{"id": row["id"], "name": row["name"], "owner": row["owner"], "scopes": _load(row["scopes"], []),
                 "created_at": row["created_at"], "last_used": row["last_used"], "revoked_at": row["revoked_at"]} for row in rows]

    def revoke_api_token(self, token_id: str):
        with self.connect() as db:
            return db.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (time.time(), token_id)).rowcount

    # Webhooks -----------------------------------------------------------
    def save_webhook(self, hook: dict):
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO webhooks(id,name,url,events,enabled,created_at,updated_at,last_status,last_error,last_delivery) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,events=excluded.events,enabled=excluded.enabled,updated_at=excluded.updated_at",
                       (hook["id"], hook["name"], hook["url"], _json(hook["events"]), int(hook.get("enabled", True)),
                        hook.get("created_at", now), now, hook.get("last_status"), hook.get("last_error"), hook.get("last_delivery")))

    def list_webhooks(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM webhooks ORDER BY created_at").fetchall()
        return [{"id": row["id"], "name": row["name"], "url": row["url"], "events": _load(row["events"], []),
                 "enabled": bool(row["enabled"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
                 "last_status": row["last_status"], "last_error": row["last_error"], "last_delivery": row["last_delivery"]} for row in rows]

    def delete_webhook(self, hook_id: str):
        with self.connect() as db:
            return db.execute("DELETE FROM webhooks WHERE id=?", (hook_id,)).rowcount

    def record_webhook_delivery(self, hook_id: str, status: int | None, error: str | None):
        with self.connect() as db:
            db.execute("UPDATE webhooks SET last_status=?,last_error=?,last_delivery=? WHERE id=?",
                       (status, error[:300] if error else None, time.time(), hook_id))

    # Audit/settings/performance ----------------------------------------
    def audit(self, actor: str, action: str, target: str | None = None, details: dict | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO audit(created_at,actor,action,target,details) VALUES(?,?,?,?,?)",
                       (time.time(), actor[:80], action[:100], target[:200] if target else None, _json(details or {})))

    def audit_log(self, limit: int = 200):
        limit = max(1, min(limit, 1000))
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": row["seq"], "created_at": row["created_at"], "actor": row["actor"],
                 "action": row["action"], "target": row["target"], "details": _load(row["details"], {})} for row in rows]

    def set_setting(self, key: str, value):
        with self.connect() as db:
            db.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                       (key, _json(value), time.time()))

    def get_setting(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return _load(row["value"], default) if row else default

    def settings(self):
        with self.connect() as db:
            rows = db.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
        return {row["key"]: _load(row["value"], None) for row in rows}

    def record_performance(self, profile: str, source_duration: float | None, wall_seconds: float,
                           input_bytes: int | None, output_bytes: int | None):
        with self.connect() as db:
            db.execute("INSERT INTO performance(created_at,profile,source_duration,wall_seconds,input_bytes,output_bytes) VALUES(?,?,?,?,?,?)",
                       (time.time(), profile, source_duration, wall_seconds, input_bytes, output_bytes))
            db.execute("DELETE FROM performance WHERE seq NOT IN (SELECT seq FROM performance ORDER BY seq DESC LIMIT 2000)")

    def performance(self, profile: str, limit: int = 100):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM performance WHERE profile=? ORDER BY created_at DESC LIMIT ?", (profile, max(1, min(limit, 500)))).fetchall()
        return [dict(row) for row in rows]

    # Passkeys -----------------------------------------------------------
    def save_passkey(self, item: dict):
        with self.connect() as db:
            db.execute("INSERT INTO passkeys(id,name,credential_id,public_key,sign_count,created_at,last_used) VALUES(?,?,?,?,?,?,?)",
                       (item["id"], item["name"], item["credential_id"], item["public_key"], int(item.get("sign_count", 0)),
                        item.get("created_at", time.time()), item.get("last_used")))

    def passkeys(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM passkeys ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def passkey_by_credential(self, credential_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM passkeys WHERE credential_id=?", (credential_id,)).fetchone()
        return dict(row) if row else None

    def update_passkey_use(self, item_id: str, sign_count: int):
        with self.connect() as db:
            db.execute("UPDATE passkeys SET sign_count=?,last_used=? WHERE id=?", (sign_count, time.time(), item_id))

    def delete_passkey(self, item_id: str):
        with self.connect() as db:
            return db.execute("DELETE FROM passkeys WHERE id=?", (item_id,)).rowcount

    def backup_to(self, target: Path):
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o600)
