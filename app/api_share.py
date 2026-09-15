"""Expiring, revocable share links for completed Crate files."""
from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from pathlib import Path

from fastapi import Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api_jobs import MIME
from app.web_auth import require_scope


class ShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_in: int | None = Field(default=None, ge=300, le=30 * 86400)
    max_downloads: int = Field(default=0, ge=0, le=1000)
    password: str | None = Field(default=None, max_length=128)


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def _share_cookie(config, token_hash: str) -> str:
    return hmac.new(hashlib.sha256(config.secret.encode()).digest(), ("share:" + token_hash).encode(), hashlib.sha256).hexdigest()


def install(app, queue, config, owner):
    @app.post("/api/jobs/{job_id}/share", status_code=201)
    async def create_share(job_id: str, body: ShareCreate, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        job = queue.owned(job_id, identity)
        if job.get("status") != "ready" or not job.get("path") or not Path(job["path"]).is_file():
            raise HTTPException(409, "Only a ready file can be shared.")
        token = secrets.token_urlsafe(32); token_hash = hashlib.sha256(token.encode()).hexdigest(); share_id = secrets.token_hex(8)
        salt = secrets.token_bytes(16) if body.password else b""
        payload = {"id": share_id, "expires_at": time.time() + (body.expires_in or config.share_default_ttl),
                   "max_downloads": body.max_downloads, "downloads": 0,
                   "password_salt": salt.hex() if salt else None,
                   "password_hash": _password_hash(body.password, salt) if body.password else None,
                   "filename": job.get("filename"), "title": job.get("title"), "size": job.get("size"), "format": job.get("format")}
        queue.store.create_share(token_hash, job_id, identity, payload)
        queue.store.audit("user", "share.create", job_id, {"share_id": share_id, "expires_at": payload["expires_at"], "max_downloads": body.max_downloads})
        return {"id": share_id, "url": str(request.base_url).rstrip("/") + "/share/" + token,
                "expires_at": payload["expires_at"], "max_downloads": body.max_downloads, "password_required": bool(body.password)}

    @app.get("/api/shares")
    async def list_shares(request: Request):
        identity = owner(request); require_scope(request, "jobs:read")
        result = []
        for share in queue.store.list_shares(identity):
            result.append({"id": share.get("id"), "job_id": share["job_id"], "expires_at": share.get("expires_at"),
                           "max_downloads": share.get("max_downloads", 0), "downloads": share.get("downloads", 0),
                           "password_required": bool(share.get("password_hash")), "filename": share.get("filename"),
                           "expired": float(share.get("expires_at") or 0) <= time.time()})
        return result

    @app.delete("/api/shares/{share_id}", status_code=204)
    async def revoke_share(share_id: str, request: Request):
        identity = owner(request); require_scope(request, "jobs:write")
        match = next((item for item in queue.store.list_shares(identity) if item.get("id") == share_id), None)
        if not match: raise HTTPException(404, "Share link not found.")
        queue.store.delete_share(match["token_hash"]); queue.store.audit("user", "share.revoke", match["job_id"], {"share_id": share_id})
        return PlainTextResponse(status_code=204)

    def resolve(token: str):
        if len(token) < 24 or len(token) > 128: raise HTTPException(404, "Share link not found.")
        token_hash = hashlib.sha256(token.encode()).hexdigest(); share = queue.store.get_share(token_hash)
        if not share or float(share.get("expires_at") or 0) <= time.time(): raise HTTPException(410, "This share link has expired.")
        job = queue.jobs.get(share["job_id"])
        if not job or job.get("status") != "ready" or not job.get("path") or not Path(job["path"]).is_file():
            raise HTTPException(410, "The shared file is no longer available.")
        maximum = int(share.get("max_downloads") or 0)
        if maximum and int(share.get("downloads") or 0) >= maximum: raise HTTPException(410, "This share link has reached its download limit.")
        return token_hash, share, job

    @app.get("/share/{token}")
    async def share_page(token: str):
        _, share, _ = resolve(token)
        title = html.escape(str(share.get("title") or share.get("filename") or "Shared file")); filename = html.escape(str(share.get("filename") or "download"))
        if share.get("password_hash"):
            body = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Crate share</title><link rel="stylesheet" href="/assets/styles.css?v=60"></head><body><main class="shell"><section class="intro"><p class="eyebrow">CRATE SHARE</p><h2>{title}</h2><p>{filename}</p><form method="post" action="/share/{html.escape(token)}/unlock"><label>Password<input type="password" name="password" required autocomplete="current-password"></label><button class="primary" type="submit">Unlock</button></form></section></main></body></html>'''
        else:
            body = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Crate share</title><link rel="stylesheet" href="/assets/styles.css?v=60"></head><body><main class="shell"><section class="intro"><p class="eyebrow">CRATE SHARE</p><h2>{title}</h2><p>{filename}</p><a class="download-link" href="/api/share/{html.escape(token)}/file">Download ↓</a></section></main></body></html>'''
        return HTMLResponse(body)

    @app.post("/share/{token}/unlock")
    async def unlock(token: str, password: str = Form(...)):
        token_hash, share, _ = resolve(token)
        salt = bytes.fromhex(share.get("password_salt") or "")
        if not salt or not hmac.compare_digest(_password_hash(password, salt), str(share.get("password_hash") or "")):
            raise HTTPException(401, "Incorrect share password.")
        response = JSONResponse({"unlocked": True, "download": f"/api/share/{token}/file"})
        response.set_cookie("crate_share", _share_cookie(config, token_hash), max_age=3600, httponly=True,
                            secure=config.secure_cookie, samesite="strict", path=f"/api/share/{token}")
        return response

    @app.api_route("/api/share/{token}/file", methods=["GET", "HEAD"])
    async def share_file(token: str, request: Request):
        token_hash, share, job = resolve(token)
        if share.get("password_hash") and not hmac.compare_digest(request.cookies.get("crate_share", ""), _share_cookie(config, token_hash)):
            raise HTTPException(401, "Unlock this share link first.")
        if request.method == "GET":
            share["downloads"] = int(share.get("downloads") or 0) + 1
            payload = {key: value for key, value in share.items() if key not in {"token_hash", "job_id", "owner", "created_at", "updated_at"}}
            queue.store.update_share(token_hash, payload)
        return FileResponse(job["path"], filename=job["filename"], media_type=MIME.get(job["format"], "application/octet-stream"))
