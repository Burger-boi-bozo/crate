from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    name TEXT,
    tool TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    speed_bytes INTEGER NOT NULL DEFAULT 0,
    eta_seconds INTEGER,
    output_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_downloads_queue
ON downloads(status, priority DESC, created_at ASC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "UPDATE downloads SET status='queued', error='Recovered after restart' "
                "WHERE status IN ('downloading', 'processing')"
            )

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, sql, tuple(params))

    def _execute_sync(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._fetch_one_sync, sql, tuple(params))

    def _fetch_one_sync(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._fetch_all_sync, sql, tuple(params))

    def _fetch_all_sync(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

