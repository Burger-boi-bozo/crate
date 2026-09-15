"""Anonymous browser and API-token authentication helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse


def signing_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def _bearer(request: Request, store):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if len(token) < 24 or len(token) > 512:
        raise HTTPException(401, "Invalid API token.")
    record = store.verify_api_token(hashlib.sha256(token.encode()).hexdigest()) if store else None
    if not record:
        raise HTTPException(401, "Invalid or revoked API token.")
    request.state.crate_api_token = record
    return record


def owner(request: Request, key: bytes, store=None) -> str:
    if record := _bearer(request, store):
        return record["owner"]
    token = request.cookies.get("crate_session", "")
    try:
        identity, expires, signature = token.split(".")
        expected = hmac.new(key, f"{identity}.{expires}".encode(), hashlib.sha256).hexdigest()
        if len(identity) != 32 or not hmac.compare_digest(signature, expected) or int(expires) < time.time():
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Refresh the page to start your download session.")
    return identity


def require_scope(request: Request, scope: str) -> None:
    record = getattr(request.state, "crate_api_token", None)
    if not record:
        return
    scopes = set(record.get("scopes") or [])
    if "*" not in scopes and scope not in scopes:
        raise HTTPException(403, f"This API token does not have the {scope} scope.")


def session_response(request: Request, config, key: bytes, supported_sites, store=None):
    try:
        identity = owner(request, key, store)
    except HTTPException:
        identity = secrets.token_hex(16)
    if getattr(request.state, "crate_api_token", None):
        return JSONResponse({"authenticated": True, "api_token": True, "configured": True,
                             "access_code_required": False, "hosting": "proxmox", "workers": config.workers,
                             "max_resolution": None, "max_minutes": config.max_duration // 60,
                             "max_mb": config.max_bytes // (1024 * 1024), "retention_minutes": config.ttl // 60,
                             "supported_sites": supported_sites})
    payload = f"{identity}.{int(time.time()) + 30 * 86400}"
    token = payload + "." + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"authenticated": True, "configured": True, "access_code_required": False,
                             "hosting": "proxmox", "workers": config.workers, "max_resolution": None,
                             "max_minutes": config.max_duration // 60, "max_mb": config.max_bytes // (1024 * 1024),
                             "retention_minutes": config.ttl // 60, "supported_sites": supported_sites})
    response.set_cookie("crate_session", token, max_age=30 * 86400, httponly=True,
                        secure=config.secure_cookie, samesite="strict", path="/")
    return response


def logout_response(config):
    response = PlainTextResponse(status_code=204)
    response.delete_cookie("crate_session", secure=config.secure_cookie, httponly=True, samesite="strict")
    return response
