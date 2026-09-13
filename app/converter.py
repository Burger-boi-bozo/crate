"""Small, bounded media converter for a single free web-service instance."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import signal
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.media_policy import MEDIA_HOSTS, validate_url

STATIC = Path(__file__).with_name("converter_static")
ACTIVE = {"queued", "downloading", "converting"}
logger = logging.getLogger("uvicorn.error")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CRATE_DATA_DIR", "converter-data")).resolve())
    secret: str = field(default_factory=lambda: os.getenv("CRATE_SESSION_SECRET", "") or secrets.token_urlsafe(48))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("CRATE_SECURE_COOKIE", "true") != "false")
    max_bytes: int = 100 * 1024 * 1024
    max_work_bytes: int = 400 * 1024 * 1024
    max_duration: int = 600
    timeout: int = 600
    ttl: int = 3600
    max_queue: int = 5
    daily_jobs: int = 10
    max_downloads: int = 3


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    format: Literal["mp4", "mp3"] = "mp4"

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return validate_url(value)


class Queue:
    def __init__(self, config: Config):
        self.config = config
        self.jobs: dict[str, dict] = {}
        self.pending: asyncio.Queue = asyncio.Queue()
        self.daily: deque[float] = deque()
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: list[asyncio.Task] = []

    async def start(self):
        # Files are deliberately temporary; clear leftovers from a prior process.
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        for child in self.config.data_dir.glob("job-*"):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
        self.tasks = [asyncio.create_task(self.work()), asyncio.create_task(self.clean())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for proc in list(self.processes.values()):
            await self.kill(proc)
        await asyncio.gather(*self.tasks, return_exceptions=True)

    @staticmethod
    async def kill(proc):
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()

    def public(self, job):
        return {key: value for key, value in job.items() if key not in {"owner", "path", "serves"}}

    def submit(self, owner, body: Submission):
        now = time.time()
        while self.daily and self.daily[0] < now - 86400:
            self.daily.popleft()
        if len(self.daily) >= self.config.daily_jobs:
            raise HTTPException(429, "Today's conversion allowance is used. Please try again tomorrow.")
        active = [j for j in self.jobs.values() if j["status"] in ACTIVE]
        if len(active) >= self.config.max_queue:
            raise HTTPException(429, "The queue is full. Please try again in a few minutes.")
        if sum(j["owner"] == owner for j in active) >= 2:
            raise HTTPException(429, "Please wait for one of your current conversions to finish.")
        if shutil.disk_usage(self.config.data_dir).free < self.config.max_work_bytes * 2:
            raise HTTPException(503, "Storage is busy. Please try again after older files expire.")
        job_id = secrets.token_hex(16)
        job = dict(id=job_id, owner=owner, url=body.url, format=body.format,
                   title=urlsplit(body.url).hostname, status="queued", progress=0,
                   created_at=now, finished_at=None, expires_at=None, error=None,
                   size=None, width=None, height=None, error_code=None, filename=None, path=None, serves=0)
        self.jobs[job_id] = job
        self.daily.append(now)
        self.pending.put_nowait(job_id)
        return job

    def owned(self, job_id, owner):
        job = self.jobs.get(job_id)
        if not job or not hmac.compare_digest(job["owner"], owner):
            raise HTTPException(404, "This conversion is no longer available.")
        return job

    def folder(self, job_id):
        return self.config.data_dir / ("job-" + job_id)

    async def cancel(self, job):
        if job["status"] in ACTIVE:
            job.update(status="cancelled", finished_at=time.time(), error=None)
            proc = self.processes.get(job["id"])
            if proc:
                await self.kill(proc)
        shutil.rmtree(self.folder(job["id"]), ignore_errors=True)
        job["path"] = None

    async def work(self):
        while True:
            job_id = await self.pending.get()
            job = self.jobs.get(job_id)
            try:
                if job and job["status"] == "queued":
                    await self.run(job)
            finally:
                self.pending.task_done()

    async def run(self, job):
        directory = self.folder(job["id"])
        directory.mkdir(mode=0o700)
        job["status"] = "downloading"
        # Do not pass service credentials or proxy environment variables to extractors.
        env = {k: v for k, v in os.environ.items() if k in {
            "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "LD_LIBRARY_PATH", "SSL_CERT_FILE"
        }}
        env.update(PYTHONUNBUFFERED="1", HOME=str(directory), TMPDIR=str(directory))
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.media_runner", job["url"], job["format"],
                str(directory), str(self.config.max_bytes), str(self.config.max_duration),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=env, start_new_session=True,
            )
            self.processes[job["id"]] = proc
            started = time.monotonic()
            result = None
            readline = asyncio.create_task(proc.stdout.readline())
            try:
                while True:
                    done, _ = await asyncio.wait({readline}, timeout=1)
                    if time.monotonic() - started > self.config.timeout:
                        raise ValueError("This conversion took too long. Try a shorter clip.")
                    used = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
                    if used > self.config.max_work_bytes:
                        raise ValueError("This clip needs too much temporary space. Try a smaller clip.")
                    if not done:
                        continue
                    line = readline.result()
                    if not line:
                        break
                    readline = asyncio.create_task(proc.stdout.readline())
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if event.get("kind") == "progress" and job["status"] in ACTIVE:
                        job["progress"] = max(0, min(99, event.get("progress", 0)))
                        job["status"] = event.get("status", "downloading")
                    elif event.get("kind") == "result":
                        result = event
                    elif event.get("kind") == "error":
                        job["error_code"] = event.get("code", "source_error")
                        logger.warning("Conversion %s source=%s code=%s diagnostic=%s", job["id"],
                                       urlsplit(job["url"]).hostname, job["error_code"],
                                       event.get("diagnostic", "")[:400])
                        raise ValueError(event.get("message", "The source could not provide this media."))
                await proc.wait()
            finally:
                readline.cancel()
                await asyncio.gather(readline, return_exceptions=True)
            if job["status"] == "cancelled":
                return
            if proc.returncode or not result:
                raise ValueError("The source could not provide this media. Try another public clip.")
            path = (directory / result["file"]).resolve()
            if (path.parent != directory.resolve() or path.is_symlink() or not path.is_file()
                    or path.suffix != "." + job["format"] or not 0 < path.stat().st_size <= self.config.max_bytes):
                raise ValueError("No usable file was produced within the size limit.")
            job.update(status="ready", progress=100, title=result["title"][:200],
                       filename=result["filename"], path=str(path), size=path.stat().st_size,
                       width=result.get("width"), height=result.get("height"),
                       finished_at=time.time(), expires_at=time.time() + self.config.ttl)
            for child in directory.iterdir():
                if child != path and child.is_file():
                    child.unlink()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if job["status"] != "cancelled":
                message = str(exc) if isinstance(exc, ValueError) else "The conversion stopped unexpectedly. Please try again."
                job.update(status="failed", error=message[:300], finished_at=time.time())
        finally:
            if proc:
                await self.kill(proc)
            self.processes.pop(job["id"], None)
            if job["status"] != "ready":
                shutil.rmtree(directory, ignore_errors=True)

    async def clean(self):
        while True:
            await asyncio.sleep(30)
            self.expire()

    def expire(self):
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            if job["expires_at"] and job["expires_at"] <= now and job["status"] == "ready":
                shutil.rmtree(self.folder(job_id), ignore_errors=True)
                job.update(status="expired", path=None)
            if job["status"] not in ACTIVE and now - job["created_at"] > 86400:
                self.jobs.pop(job_id)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    queue = Queue(config)
    request_times = defaultdict(deque)
    signing_key = hashlib.sha256(config.secret.encode()).digest()

    @asynccontextmanager
    async def lifespan(app):
        await queue.start()
        yield
        await queue.stop()

    app = FastAPI(title="Crate · Link to file", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.queue = queue
    app.state.config = config

    def owner(request: Request):
        token = request.cookies.get("crate_session", "")
        try:
            identity, expires, sig = token.split(".")
            expected = hmac.new(signing_key, f"{identity}.{expires}".encode(), hashlib.sha256).hexdigest()
            if len(identity) != 32 or not hmac.compare_digest(sig, expected) or int(expires) < time.time():
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(401, "Refresh the page to start your download session.")
        return identity

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            # One global bucket as well as per-client limits bounds memory and
            # prevents spoofed forwarded addresses from creating unlimited state.
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
                # Starlette's cached request middleware replays this small body
                # to FastAPI without accepting an unbounded chunked upload.
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

    @app.get("/api/health")
    async def health():
        ready = bool(shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("node"))
        return JSONResponse({"status": "ok" if ready else "missing_tools", "converter": "yt-dlp + FFmpeg",
                             "version": "public-1080p-1", "max_resolution": 1080,
                             "access_code_required": False}, status_code=200 if ready else 503)

    @app.get("/api/session")
    @app.post("/api/session")
    async def session(request: Request):
        # Anonymous browser identity keeps downloads separate without a password.
        try:
            identity = owner(request)
        except HTTPException:
            identity = secrets.token_hex(16)
        payload = f"{identity}.{int(time.time()) + 30 * 86400}"
        token = payload + "." + hmac.new(signing_key, payload.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"authenticated": True, "configured": True, "access_code_required": False,
                                 "hosting": os.getenv("CRATE_HOSTING", "render"),
                                 "max_resolution": 1080, "max_minutes": config.max_duration // 60,
                                 "max_mb": config.max_bytes // (1024 * 1024),
                                 "retention_minutes": config.ttl // 60, "supported_sites": MEDIA_HOSTS})
        response.set_cookie("crate_session", token, max_age=30 * 86400, httponly=True,
                            secure=config.secure_cookie, samesite="strict", path="/")
        return response

    @app.delete("/api/session", status_code=204)
    async def logout():
        response = PlainTextResponse(status_code=204)
        response.delete_cookie("crate_session", secure=config.secure_cookie, httponly=True, samesite="strict")
        return response

    @app.get("/api/jobs")
    async def jobs(request: Request):
        identity = owner(request)
        queue.expire()
        return [queue.public(job) for job in queue.jobs.values() if job["owner"] == identity]

    @app.post("/api/jobs", status_code=202)
    async def submit(body: Submission, request: Request):
        identity = owner(request)
        return queue.public(queue.submit(identity, body))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        job = queue.owned(job_id, owner(request))
        if job["status"] not in ACTIVE:
            raise HTTPException(409, "This conversion has already finished.")
        await queue.cancel(job)
        return queue.public(job)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete(job_id: str, request: Request):
        job = queue.owned(job_id, owner(request))
        await queue.cancel(job)
        queue.jobs.pop(job_id, None)
        return PlainTextResponse(status_code=204)

    @app.get("/api/jobs/{job_id}/file")
    async def download(job_id: str, request: Request):
        queue.expire()
        job = queue.owned(job_id, owner(request))
        if job["status"] != "ready" or not job["path"] or not Path(job["path"]).is_file():
            raise HTTPException(410, "This file has expired. Paste the link again to recreate it.")
        if job["serves"] >= config.max_downloads:
            raise HTTPException(429, "This file's download allowance is used. Create it again if needed.")
        job["serves"] += 1
        return FileResponse(job["path"], filename=job["filename"],
                            media_type="video/mp4" if job["format"] == "mp4" else "audio/mpeg")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/robots.txt")
    async def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    return app


app = create_app()
