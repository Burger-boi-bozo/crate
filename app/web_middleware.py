"""HTTP protections shared by all Crate routes."""
from collections import defaultdict, deque
import time

from fastapi.responses import JSONResponse

STREAMING_MUTATIONS = {"/api/uploads", "/api/admin/restore"}


def install(app):
    request_times = defaultdict(deque)

    @app.middleware("http")
    async def protect(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/health", "/api/version", "/api/capabilities"}:
            now = time.monotonic(); key = request.client.host if request.client else "unknown"
            if key not in request_times and len(request_times) >= 1000: key = "overflow"
            for bucket, limit in ((request_times["*"], 1800), (request_times[key], 360)):
                while bucket and bucket[0] < now - 60: bucket.popleft()
                if len(bucket) >= limit:
                    return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
                bucket.append(now)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
                if not bearer:
                    origin = request.headers.get("origin")
                    if (request.headers.get("x-crate-request") != "1" or
                            (origin and origin != str(request.base_url).rstrip("/"))):
                        return JSONResponse({"detail": "Please submit from this website."}, status_code=403)
                if path not in STREAMING_MUTATIONS:
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 256 * 1024:
                            return JSONResponse({"detail": "Request too large."}, status_code=413)
                    request._body = bytes(body)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(self), clipboard-write=(self)",
        })
        if not path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response
