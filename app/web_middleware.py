"""HTTP protections shared by all Crate routes."""
from collections import defaultdict, deque
import time

from fastapi.responses import JSONResponse


def install(app):
    request_times = defaultdict(deque)

    @app.middleware("http")
    async def protect(request, call_next):
        if request.url.path.startswith("/api/") and request.url.path not in {"/api/health", "/api/version"}:
            now = time.monotonic()
            key = request.client.host if request.client else "unknown"
            if key not in request_times and len(request_times) >= 1000:
                key = "overflow"
            for bucket, limit in ((request_times["*"], 1200), (request_times[key], 240)):
                while bucket and bucket[0] < now - 60:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
                bucket.append(now)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if (request.headers.get("x-crate-request") != "1" or
                        (origin and origin != str(request.base_url).rstrip("/"))):
                    return JSONResponse({"detail": "Please submit from this website."}, status_code=403)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        return JSONResponse({"detail": "Request too large."}, status_code=413)
                request._body = bytes(body)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        if not request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response
