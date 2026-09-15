"""Separate, rate-limited authentication for the Crate operator panel."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

COOKIE = "crate_admin"
_DEFAULT_TTL = 12 * 60 * 60
_failures: dict[str, deque[float]] = defaultdict(deque)


def _signature(key: bytes, expires: str) -> str:
    return hmac.new(key, f"admin.{expires}".encode(), hashlib.sha256).hexdigest()


def _source(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


def _config(request: Request, supplied=None):
    return supplied or getattr(request.app.state, "config", None)


def _ip_allowed(request: Request, config) -> bool:
    raw = str(getattr(config, "admin_trusted_ips", "") or "").strip()
    if not raw:
        return True
    try:
        source = ipaddress.ip_address(_source(request))
    except ValueError:
        return False
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        if source in network:
            return True
    return False


def _check_ip(request: Request, config=None) -> None:
    config = _config(request, config)
    if config is not None and not _ip_allowed(request, config):
        raise HTTPException(403, "Admin access is not allowed from this address.")


def _check_rate(request: Request | None) -> deque[float]:
    key = _source(request)
    bucket = _failures[key]
    now = time.monotonic()
    while bucket and bucket[0] < now - 300:
        bucket.popleft()
    if len(bucket) >= 5:
        raise HTTPException(429, "Too many admin sign-in attempts. Try again later.")
    return bucket

def check_admin_ip(request: Request, config=None) -> None:
    """Apply the configured admin IP allowlist without requiring a session."""
    _check_ip(request, config)


def require_admin(request: Request, key: bytes, config=None) -> None:
    _check_ip(request, config)
    token = request.cookies.get(COOKIE, "")
    try:
        marker, expires, signature = token.split(".")
        expected = _signature(key, expires)
        if marker != "admin" or int(expires) < time.time() or not hmac.compare_digest(signature, expected):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Admin sign-in required.")


def session_response(config, key: bytes):
    ttl = int(getattr(config, "admin_session_ttl", _DEFAULT_TTL) or _DEFAULT_TTL)
    expires = str(int(time.time()) + ttl)
    token = f"admin.{expires}.{_signature(key, expires)}"
    response = JSONResponse({"authenticated": True, "expires_at": int(expires)})
    response.set_cookie(COOKIE, token, max_age=ttl, httponly=True,
                        secure=config.secure_cookie, samesite="strict", path="/")
    return response


def login_response(password: str, config, key: bytes, request: Request | None = None):
    if request is not None:
        _check_ip(request, config)
    bucket = _check_rate(request)
    configured = config.admin_password or ""
    if not configured:
        raise HTTPException(503, "Admin access has not been configured on this server.")
    if not hmac.compare_digest(password.encode(), configured.encode()):
        bucket.append(time.monotonic())
        raise HTTPException(401, "Incorrect admin password.")
    bucket.clear()
    return session_response(config, key)


def auth_status() -> dict:
    now = time.monotonic()
    active = {}
    for source, bucket in list(_failures.items()):
        while bucket and bucket[0] < now - 300:
            bucket.popleft()
        if bucket:
            active[source] = len(bucket)
    return {"window_seconds": 300, "max_failures": 5, "sources": active, "blocked_sources": sum(count >= 5 for count in active.values())}


def logout_response(config):
    response = PlainTextResponse(status_code=204)
    response.delete_cookie(COOKIE, secure=config.secure_cookie, httponly=True,
                           samesite="strict", path="/")
    return response
