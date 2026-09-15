"""Authenticated v6 operator APIs: config, storage, integrations, backup, and diagnostics."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from app.admin_auth import auth_status, require_admin
from app.media_convert_v6 import hardware_capabilities
from app.media_policy import validate_url
from app.models import ACTIVE
from app.release_history import releases
from app.version import version_payload

ALLOWED_SCOPES = {"jobs:read", "jobs:write"}
ALLOWED_HOOK_EVENTS = {"job.ready", "job.failed", "batch.complete"}

class SettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, bool | int | float] = Field(min_length=1, max_length=20)


class RetentionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(ge=1, le=30 * 24)


class AdminPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: str = Field(pattern="^(low|normal|high)$")
    rank: int = Field(default=0, ge=-100, le=100)


class BoolToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["jobs:read", "jobs:write"], min_length=1, max_length=8)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values):
        cleaned = list(dict.fromkeys(values))
        if any(value not in ALLOWED_SCOPES for value in cleaned):
            raise ValueError("Unsupported API-token scope.")
        return cleaned


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=2048)
    events: list[str] = Field(default_factory=lambda: ["job.ready", "job.failed"], min_length=1, max_length=6)

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return validate_url(value, source=False)

    @field_validator("events")
    @classmethod
    def check_events(cls, values):
        cleaned = list(dict.fromkeys(values))
        if any(value not in ALLOWED_HOOK_EVENTS for value in cleaned):
            raise ValueError("Unsupported webhook event.")
        return cleaned

def _admin(request: Request, key: bytes) -> None:
    require_admin(request, key)


def _safe_settings(queue):
    keys = sorted(queue.__class__.__dict__.get("RUNTIME_SETTINGS", {}))
    if not keys:
        from app.job_queue import RUNTIME_SETTINGS
        keys = sorted(RUNTIME_SETTINGS)
    return {key: getattr(queue.config, key) for key in keys}


def _timeseries(jobs, now=None):
    now = float(now or time.time())
    start = now - 24 * 3600
    buckets = []
    for index in range(24):
        left = start + index * 3600
        right = left + 3600
        created = [job for job in jobs if left <= float(job.get("created_at") or 0) < right]
        finished = [job for job in jobs if left <= float(job.get("finished_at") or 0) < right]
        buckets.append({"start": left, "submitted": len(created),
                        "ready": sum(job.get("status") == "ready" for job in finished),
                        "failed": sum(job.get("status") == "failed" for job in finished),
                        "output_bytes": sum(int(job.get("size") or 0) for job in finished if job.get("status") == "ready")})
    return buckets


def _temp_zip(config, prefix: str) -> Path:
    fd, raw = tempfile.mkstemp(prefix=prefix, suffix=".zip", dir=config.data_dir)
    os.close(fd)
    return Path(raw)

def install(app, queue, config, key):
    @app.get("/api/admin/settings")
    async def settings(request: Request):
        _admin(request, key)
        return {"values": _safe_settings(queue)}

    @app.patch("/api/admin/settings")
    async def update_settings(body: SettingPatch, request: Request):
        _admin(request, key)
        changed = {}
        for name, value in body.values.items():
            changed[name] = queue.update_setting(name, value)
        queue.store.audit("admin", "settings.update", details={"keys": sorted(changed)})
        return {"values": _safe_settings(queue), "changed": changed}

    @app.get("/api/admin/metrics/timeseries")
    async def metric_series(request: Request):
        _admin(request, key)
        return {"window_seconds": 86400, "buckets": _timeseries(list(queue.jobs.values()))}

    @app.get("/api/admin/audit")
    async def audit(request: Request, limit: int = 200):
        _admin(request, key)
        return queue.store.audit_log(limit)

    @app.post("/api/admin/drain")
    async def drain(body: BoolToggle, request: Request):
        _admin(request, key)
        queue.drain = body.enabled
        queue.store.audit("admin", "scheduler.drain", details={"enabled": body.enabled})
        return queue.scheduler_status()

    @app.get("/api/admin/storage")
    async def storage(request: Request):
        _admin(request, key)
        usage = shutil.disk_usage(config.data_dir)
        files = []
        for job in queue.jobs.values():
            path = Path(job.get("path") or "")
            if job.get("status") == "ready" and path.is_file():
                files.append({"id": job["id"], "title": job.get("title"), "filename": job.get("filename"),
                              "size": path.stat().st_size, "expires_at": job.get("expires_at"),
                              "format": job.get("format"), "created_at": job.get("created_at")})
        files.sort(key=lambda item: item["size"], reverse=True)
        return {"disk": {"total": usage.total, "used": usage.used, "free": usage.free},
                "stored_bytes": sum(item["size"] for item in files), "files": files[:500]}

    @app.post("/api/admin/jobs/{job_id}/retention")
    async def retention(job_id: str, body: RetentionRequest, request: Request):
        _admin(request, key)
        job = queue.jobs.get(job_id)
        if not job or job.get("status") != "ready":
            raise HTTPException(404, "Ready job not found.")
        job["expires_at"] = time.time() + body.hours * 3600
        queue.event(job, "retention", f"Retention extended to {body.hours} hours")
        queue.store.audit("admin", "job.retention", job_id, {"hours": body.hours})
        queue.save()
        return queue.public(job)

    @app.post("/api/admin/jobs/{job_id}/priority")
    async def admin_priority(job_id: str, body: AdminPriority, request: Request):
        _admin(request, key)
        job = queue.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        queue.store.audit("admin", "job.priority", job_id, {"priority": body.priority, "rank": body.rank})
        return queue.public(queue.set_priority(job, body.priority, body.rank))

    @app.get("/api/admin/security")
    async def security(request: Request):
        _admin(request, key)
        return {"session_ttl": config.admin_session_ttl, "trusted_ips_configured": bool(config.admin_trusted_ips.strip()),
                "trusted_ips": config.admin_trusted_ips, "passkeys": len(queue.store.passkeys()), "rate_limit": auth_status()}

    @app.get("/api/admin/deployment")
    async def deployment(request: Request):
        _admin(request, key)
        history_path = Path("/var/lib/crate/deploy-history.json")
        history = {}
        try:
            if history_path.is_file(): history = json.loads(history_path.read_text())
        except (OSError, ValueError, TypeError):
            history = {}
        return {"current": version_payload(), "history": history, "releases": releases(),
                "rollback_target": history.get("previous") or next((r.get("commit") for r in releases() if not r.get("current")), None)}

    @app.get("/api/admin/capabilities")
    async def capabilities(request: Request):
        _admin(request, key)
        try:
            ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5, check=True).stdout.splitlines()[0]
        except Exception:
            ffmpeg = "unavailable"
        try:
            ytdlp = subprocess.run([str(Path(os.sys.executable)), "-m", "yt_dlp", "--version"], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        except Exception:
            ytdlp = "unavailable"
        return {"hardware": hardware_capabilities(), "ffmpeg": ffmpeg[:160], "yt_dlp": ytdlp[:80],
                "formats": ["mp4", "mp3", "mkv", "mka", "m4a", "opus", "webm", "flac", "wav", "aac", "gif", "webp"]}

    @app.post("/api/admin/tokens", status_code=201)
    async def create_token(body: TokenCreate, request: Request):
        _admin(request, key)
        token_id = secrets.token_hex(8)
        raw = "crate_" + secrets.token_urlsafe(32)
        import hashlib
        queue.store.create_api_token(token_id, hashlib.sha256(raw.encode()).hexdigest(), body.name.strip(), "api:" + token_id, body.scopes)
        queue.store.audit("admin", "token.create", token_id, {"name": body.name.strip(), "scopes": body.scopes})
        return {"id": token_id, "name": body.name.strip(), "scopes": body.scopes, "token": raw}

    @app.get("/api/admin/tokens")
    async def tokens(request: Request):
        _admin(request, key)
        return queue.store.list_api_tokens()

    @app.delete("/api/admin/tokens/{token_id}", status_code=204)
    async def revoke_token(token_id: str, request: Request):
        _admin(request, key)
        if not queue.store.revoke_api_token(token_id):
            raise HTTPException(404, "API token not found.")
        queue.store.audit("admin", "token.revoke", token_id)
        return PlainTextResponse(status_code=204)

    @app.post("/api/admin/webhooks", status_code=201)
    async def create_webhook(body: WebhookCreate, request: Request):
        _admin(request, key)
        hook = {"id": secrets.token_hex(8), "name": body.name.strip(), "url": body.url,
                "events": body.events, "enabled": True, "created_at": time.time(),
                "last_status": None, "last_error": None, "last_delivery": None}
        queue.store.save_webhook(hook)
        queue.store.audit("admin", "webhook.create", hook["id"], {"name": hook["name"], "events": hook["events"]})
        return hook

    @app.get("/api/admin/webhooks")
    async def webhooks(request: Request):
        _admin(request, key)
        return queue.store.list_webhooks()

    @app.patch("/api/admin/webhooks/{hook_id}")
    async def toggle_webhook(hook_id: str, body: BoolToggle, request: Request):
        _admin(request, key)
        hook = next((item for item in queue.store.list_webhooks() if item["id"] == hook_id), None)
        if not hook:
            raise HTTPException(404, "Webhook not found.")
        hook["enabled"] = body.enabled
        queue.store.save_webhook(hook)
        queue.store.audit("admin", "webhook.toggle", hook_id, {"enabled": body.enabled})
        return hook

    @app.delete("/api/admin/webhooks/{hook_id}", status_code=204)
    async def delete_webhook(hook_id: str, request: Request):
        _admin(request, key)
        if not queue.store.delete_webhook(hook_id):
            raise HTTPException(404, "Webhook not found.")
        queue.store.audit("admin", "webhook.delete", hook_id)
        return PlainTextResponse(status_code=204)

    @app.get("/api/admin/diagnostics")
    async def diagnostics(request: Request):
        _admin(request, key)
        path = _temp_zip(config, "crate-diagnostics-")
        counts = {}
        for job in queue.jobs.values():
            counts[job.get("status", "unknown")] = counts.get(job.get("status", "unknown"), 0) + 1
        payload = {"generated_at": time.time(), "version": version_payload(), "scheduler": queue.scheduler_status(),
                   "settings": _safe_settings(queue), "job_counts": counts,
                   "providers": __import__("app.api_admin", fromlist=["provider_summary"]).provider_summary(list(queue.jobs.values())),
                   "capability_summary": {"hardware": hardware_capabilities()}, "audit_tail": queue.store.audit_log(50)}
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("diagnostics.json", json.dumps(payload, indent=2, sort_keys=True))
                archive.writestr("README.txt", "Sanitized Crate diagnostics. Passwords, session secrets, API tokens, submitted URLs, and media files are intentionally excluded.\n")
            queue.store.audit("admin", "diagnostics.download")
            return FileResponse(path, filename="crate-diagnostics.zip", media_type="application/zip",
                                background=BackgroundTask(path.unlink, missing_ok=True))
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.get("/api/admin/backup")
    async def backup(request: Request, include_media: bool = False):
        _admin(request, key)
        path = _temp_zip(config, "crate-backup-")
        fd, raw_db = tempfile.mkstemp(prefix="crate-db-", suffix=".sqlite", dir=config.data_dir)
        os.close(fd); db_copy = Path(raw_db)
        try:
            queue.save(); queue.store.backup_to(db_copy)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
                archive.write(db_copy, "crate.db")
                archive.writestr("backup.json", json.dumps({"version": version_payload(), "created_at": time.time(),
                                                              "include_media": include_media, "settings": _safe_settings(queue)}, indent=2))
                if include_media:
                    for job in queue.jobs.values():
                        source = Path(job.get("path") or "")
                        if job.get("status") == "ready" and source.is_file():
                            archive.write(source, f"media/{job['id']}/{source.name}")
            queue.store.audit("admin", "backup.download", details={"include_media": include_media})
            return FileResponse(path, filename="crate-backup.zip", media_type="application/zip",
                                background=BackgroundTask(path.unlink, missing_ok=True))
        finally:
            db_copy.unlink(missing_ok=True)

    @app.post("/api/admin/restore")
    async def restore(request: Request, file: UploadFile = File(...)):
        _admin(request, key)
        if not queue.maintenance or queue.claimed:
            raise HTTPException(409, "Enable maintenance and wait for active jobs to finish before restoring.")
        fd, raw = tempfile.mkstemp(prefix="crate-restore-", suffix=".zip", dir=config.data_dir)
        os.close(fd); uploaded = Path(raw)
        try:
            total = 0
            with uploaded.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 20 * 1024 ** 3:
                        raise HTTPException(413, "Backup archive is too large.")
                    stream.write(chunk)
            with zipfile.ZipFile(uploaded) as archive:
                names = archive.namelist()
                if "crate.db" not in names:
                    raise HTTPException(422, "This is not a Crate backup.")
                if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                    raise HTTPException(422, "Backup contains an unsafe path.")
                fd, raw_db = tempfile.mkstemp(prefix="crate-restored-db-", suffix=".sqlite", dir=config.data_dir)
                os.close(fd); restored = Path(raw_db)
                restored.write_bytes(archive.read("crate.db"))
                with sqlite3.connect(restored) as db:
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"jobs", "events"}.issubset(tables):
                        raise HTTPException(422, "Backup database is missing required tables.")
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise HTTPException(422, "Backup database failed its integrity check.")
                before = config.data_dir / f"crate.db.before-restore-{int(time.time())}"
                queue.store.backup_to(before)
                with queue.store.connect() as live:
                    live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    Path(str(queue.store.path) + suffix).unlink(missing_ok=True)
                os.replace(restored, queue.store.path)
                queue.store.path.chmod(0o600)
                for name in names:
                    parts = Path(name).parts
                    if len(parts) == 3 and parts[0] == "media" and len(parts[1]) == 32 and Path(parts[2]).name == parts[2]:
                        target_dir = config.data_dir / ("job-" + parts[1]); target_dir.mkdir(mode=0o700, exist_ok=True)
                        (target_dir / parts[2]).write_bytes(archive.read(name))
            queue.store.initialize(); queue.jobs = queue.store.load_jobs(); queue.batches = queue.store.load_batches(); queue.apply_saved_settings()
            queue.store.audit("admin", "backup.restore", details={"bytes": total})
            return {"restored": True, "jobs": len(queue.jobs), "batches": len(queue.batches), "previous_database": before.name}
        finally:
            uploaded.unlink(missing_ok=True)
            await file.close()
