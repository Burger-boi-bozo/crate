"""Anonymous session helpers for the Crate web app."""
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse


def signing_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def owner(request: Request, key: bytes) -> str:
    token = request.cookies.get("crate_session", "")
    try:
        identity, expires, signature = token.split(".")
        expected = hmac.new(key, f"{identity}.{expires}".encode(), hashlib.sha256).hexdigest()
        if len(identity) != 32 or not hmac.compare_digest(signature, expected) or int(expires) < time.time():
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Refresh the page to start your download session.")
    return identity


def session_response(request: Request, config, key: bytes, supported_sites):
    try:
        identity = owner(request, key)
    except HTTPException:
        identity = secrets.token_hex(16)
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
