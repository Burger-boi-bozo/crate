"""Separate authentication for the Crate operator panel."""
import hashlib
import hmac
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

COOKIE = "crate_admin"
TTL = 12 * 60 * 60


def _signature(key: bytes, expires: str) -> str:
    return hmac.new(key, f"admin.{expires}".encode(), hashlib.sha256).hexdigest()


def require_admin(request: Request, key: bytes) -> None:
    token = request.cookies.get(COOKIE, "")
    try:
        marker, expires, signature = token.split(".")
        expected = _signature(key, expires)
        if marker != "admin" or int(expires) < time.time() or not hmac.compare_digest(signature, expected):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Admin sign-in required.")


def login_response(password: str, config, key: bytes):
    configured = config.admin_password or ""
    if not configured:
        raise HTTPException(503, "Admin access has not been configured on this server.")
    if not hmac.compare_digest(password.encode(), configured.encode()):
        raise HTTPException(401, "Incorrect admin password.")
    expires = str(int(time.time()) + TTL)
    token = f"admin.{expires}.{_signature(key, expires)}"
    response = JSONResponse({"authenticated": True})
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True,
                        secure=config.secure_cookie, samesite="strict", path="/")
    return response


def logout_response(config):
    response = PlainTextResponse(status_code=204)
    response.delete_cookie(COOKIE, secure=config.secure_cookie, httponly=True,
                           samesite="strict", path="/")
    return response
