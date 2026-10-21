"""Crate-owned transcription API. Work is performed by a separate systemd worker."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.web_auth import require_scope
from app.models import Submission

ROOT = Path(os.getenv("CRATE_TRANSCRIPTS_DIR", "/var/lib/crate/transcriptions"))
FORMATS = {"mp4", "mp3", "mkv", "mka", "m4a", "opus", "webm", "flac", "wav", "aac",
           "mov", "avi", "ogg", "m4b", "wma", "flv", "mpeg", "mpg", "gif", "webp"}
EXPORTS = {"txt": "text/plain; charset=utf-8", "srt": "application/x-subrip",
           "vtt": "text/vtt", "json": "application/json"}
LANGUAGES = {"auto", "en", "es", "fr", "de", "pt", "it", "ja", "ko", "zh", "ru", "ar", "hi"}
ACTIVE = ("waiting_download", "queued", "extracting", "transcribing")
MAX_PENDING = 20
MAX_OWNER_PENDING = 3


class SourceJob(BaseModel):
    job_id: str = Field(min_length=1, max_length=100)
    language: str = "auto"


class URLSubmission(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    language: str = "auto"


def database():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(ROOT / "jobs.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS transcripts (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL,
        source_path TEXT NOT NULL, uploaded INTEGER NOT NULL,
        language TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
        detected_language TEXT NOT NULL DEFAULT '', segment_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        download_job_id TEXT NOT NULL DEFAULT ''
    )""")
    if "download_job_id" not in {col["name"] for col in db.execute("PRAGMA table_info(transcripts)")}:
        db.execute("ALTER TABLE transcripts ADD COLUMN download_job_id TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS ix_transcripts_owner ON transcripts(owner,created_at)")
    db.commit()
    return db


def public(row):
    if row is None:
        return None
    return {key: row[key] for key in (
        "id", "filename", "language", "status", "progress", "message", "error",
        "detected_language", "segment_count", "created_at", "updated_at", "download_job_id")}


def language_code(value):
    if value not in LANGUAGES:
        raise HTTPException(422, "Unsupported language.")
    return value


def own(db, identity, transcript_id):
    row = db.execute("SELECT * FROM transcripts WHERE id=? AND owner=?",
                     (transcript_id, identity)).fetchone()
    if row is None:
        raise HTTPException(404, "Transcription not found.")
    return row


def add_job(db, identity, filename, source, uploaded, language):
    language_code(language)
    if not source.is_file():
        raise HTTPException(404, "The media file no longer exists.")
    count = db.execute("SELECT COUNT(*) FROM transcripts WHERE status IN ('waiting_download','queued','extracting','transcribing')").fetchone()[0]
    mine = db.execute("SELECT COUNT(*) FROM transcripts WHERE owner=? AND status IN ('waiting_download','queued','extracting','transcribing')",
                      (identity,)).fetchone()[0]
    if count >= MAX_PENDING or mine >= MAX_OWNER_PENDING:
        raise HTTPException(429, "The transcription queue is full. Try again after a job finishes.")
    now = time.time()
    ident = uuid.uuid4().hex
    db.execute("""INSERT INTO transcripts(id,owner,filename,source_path,uploaded,language,status,progress,message,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,'queued',0,'Waiting for transcription worker',?,?)""",
               (ident, identity, filename[:180], str(source), int(uploaded), language, now, now))
    db.commit()
    return own(db, identity, ident)


def install(app, queue, config, owner):
    with database() as db:
        pass

    @app.get("/api/transcriptions/capabilities")
    async def capabilities(request: Request):
        owner(request); require_scope(request, "jobs:read")
        with database() as db:
            row = db.execute("SELECT updated_at FROM worker_status WHERE id=1").fetchone() if _has_worker_table(db) else None
        online = bool(row and time.time() - row["updated_at"] < 35)
        return {"available": online, "engine": "faster-whisper (local CPU)",
                "languages": sorted(LANGUAGES), "formats": sorted(FORMATS),
                "outputs": sorted(EXPORTS)}

    @app.get("/api/transcriptions")
    async def list_transcriptions(request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        with database() as db:
            return [public(row) for row in db.execute(
                "SELECT * FROM transcripts WHERE owner=? ORDER BY created_at DESC LIMIT 100", (identity,))]

    @app.post("/api/transcriptions/from-job", status_code=202)
    async def from_job(body: SourceJob, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        language_code(body.language)
        job = queue.owned(body.job_id, identity)
        if job.get("status") != "ready":
            raise HTTPException(409, "The Crate download must finish first.")
        source = Path(job.get("path") or "")
        with database() as db:
            return public(add_job(db, identity, job.get("filename") or source.name,
                                  source, False, body.language))

    @app.post("/api/transcriptions/from-url", status_code=202)
    async def from_url(body: URLSubmission, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        language_code(body.language)
        # Submission uses the same link validation and SSRF protections as Crate.
        conversion = Submission(url=body.url, format="mp3", quality="best", priority="low")
        with database() as db:
            count = db.execute("SELECT COUNT(*) FROM transcripts WHERE status IN ('waiting_download','queued','extracting','transcribing')").fetchone()[0]
            mine = db.execute("SELECT COUNT(*) FROM transcripts WHERE owner=? AND status IN ('waiting_download','queued','extracting','transcribing')",
                              (identity,)).fetchone()[0]
            if count >= MAX_PENDING or mine >= MAX_OWNER_PENDING:
                raise HTTPException(429, "The transcription queue is full. Try again after a job finishes.")
            job = queue.submit(identity, conversion)
            now = time.time()
            ident = uuid.uuid4().hex
            host = (urlsplit(conversion.url).hostname or "media")
            label = "Link from " + host
            db.execute("""INSERT INTO transcripts
                (id,owner,filename,source_path,uploaded,language,status,progress,message,created_at,updated_at,download_job_id)
                VALUES (?,?,?,'',1,?,'waiting_download',0,'Preparing media download',?,?,?)""",
                (ident, identity, label[:180], body.language, now, now, job["id"]))
            db.commit()
            return public(own(db, identity, ident))

    @app.post("/api/transcriptions/upload", status_code=202)
    async def upload(request: Request, file: UploadFile = File(...), language: str = Form("auto")):
        identity = owner(request); require_scope(request, "jobs:write")
        language_code(language)
        filename = Path(file.filename or "media").name[:180]
        if filename.rsplit(".", 1)[-1].lower() not in FORMATS or "." not in filename:
            raise HTTPException(422, "Unsupported media format.")
        with database() as db:
            counts = db.execute("SELECT COUNT(*) FROM transcripts WHERE status IN ('waiting_download','queued','extracting','transcribing')").fetchone()[0]
            mine = db.execute("SELECT COUNT(*) FROM transcripts WHERE owner=? AND status IN ('queued','extracting','transcribing')", (identity,)).fetchone()[0]
        if counts >= MAX_PENDING or mine >= MAX_OWNER_PENDING:
            raise HTTPException(429, "The transcription queue is full.")
        ident = uuid.uuid4().hex
        folder = ROOT / "uploads"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = folder / (ident + "." + filename.rsplit(".", 1)[-1].lower())
        total = 0
        try:
            with source.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if shutil.disk_usage(ROOT).free < 2 * 1024 ** 3:
                        raise HTTPException(507, "Insufficient disk space for transcription upload.")
                    if total > min(config.max_upload_bytes, 4 * 1024 ** 3):
                        raise HTTPException(413, "Transcription upload exceeds the 4 GB limit.")
                    output.write(chunk)
            if not total:
                raise HTTPException(422, "Media file is empty.")
            with database() as db:
                return public(add_job(db, identity, filename, source, True, language))
        except Exception:
            source.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/transcriptions/{transcript_id}")
    async def get_transcription(transcript_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        with database() as db:
            return public(own(db, identity, transcript_id))

    @app.get("/api/transcriptions/{transcript_id}/file")
    async def get_export(transcript_id: str, request: Request, format: str = "txt"):
        identity = owner(request); require_scope(request, "jobs:read")
        if format not in EXPORTS:
            raise HTTPException(422, "Expected txt, srt, vtt, or json.")
        with database() as db:
            row = own(db, identity, transcript_id)
        if row["status"] != "completed":
            raise HTTPException(409, "The transcript is not complete.")
        path = ROOT / transcript_id / ("transcript." + format)
        if not path.is_file():
            raise HTTPException(404, "The export was not found.")
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(row["filename"]).stem)[:90] or "transcript"
        return FileResponse(path, media_type=EXPORTS[format],
                            filename=stem + "." + format)

    @app.delete("/api/transcriptions/{transcript_id}", status_code=204)
    async def delete_transcription(transcript_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        with database() as db:
            row = own(db, identity, transcript_id)
            if row["status"] in ("extracting", "transcribing"):
                raise HTTPException(409, "A running transcription cannot be deleted.")
            if row["uploaded"] and row["source_path"]:
                path = Path(row["source_path"])
                if path.resolve().is_relative_to(ROOT.resolve()):
                    path.unlink(missing_ok=True)
            for ext in EXPORTS:
                (ROOT / transcript_id / ("transcript." + ext)).unlink(missing_ok=True)
            try: (ROOT / transcript_id).rmdir()
            except OSError: pass
            db.execute("DELETE FROM transcripts WHERE id=?", (transcript_id,))
            db.commit()


def _has_worker_table(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_status'").fetchone())
