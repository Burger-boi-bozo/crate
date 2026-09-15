"""Crate v6 FastAPI application assembly."""
from contextlib import asynccontextmanager
from pathlib import Path
import shutil

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import api_admin, api_admin_v6, api_jobs, api_music, api_passkeys_v6, api_preview, api_share, web_middleware
from app.job_queue import Queue
from app.media_policy import MEDIA_HOSTS
from app.models import Config
from app.release_history import releases
from app.version import RELEASE_VERSION, RUNTIME_VERSION, version_payload
from app.web_auth import logout_response, owner, session_response, signing_key

STATIC = Path(__file__).with_name("converter_static")


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    queue = Queue(config)
    key = signing_key(config.secret)

    @asynccontextmanager
    async def lifespan(app):
        await queue.start()
        yield
        await queue.stop()

    app = FastAPI(title="Crate · Link to file", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.queue = queue
    app.state.config = config
    web_middleware.install(app)
    get_owner = lambda request: owner(request, key, queue.store)

    @app.get("/api/health")
    async def health():
        ready = bool(shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("node"))
        return JSONResponse({"status": "ok" if ready else "missing_tools", "runtime": RUNTIME_VERSION,
                             "version": RELEASE_VERSION, "build": version_payload()["build"],
                             "converter": "yt-dlp + FFmpeg", "workers": config.workers,
                             "access_code_required": False}, status_code=200 if ready else 503)

    @app.get("/api/version")
    async def version():
        return version_payload()

    @app.get("/api/session")
    @app.post("/api/session")
    async def session(request: Request):
        return session_response(request, config, key, MEDIA_HOSTS, queue.store)

    @app.delete("/api/session", status_code=204)
    async def logout():
        return logout_response(config)

    api_jobs.install(app, queue, config, get_owner)
    api_music.install(app, queue, get_owner)
    api_preview.install(app, config, get_owner)
    api_admin.install(app, queue, config, key)
    api_admin_v6.install(app, queue, config, key)
    api_passkeys_v6.install(app, queue, config, key)
    api_share.install(app, queue, config, get_owner)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin")
    async def admin():
        return FileResponse(STATIC / "admin.html")

    @app.get("/api/releases")
    async def release_history():
        return releases()

    @app.get("/api/capabilities")
    async def capabilities():
        return {"version": RELEASE_VERSION, "formats": ["mp4","mp3","mkv","mka","m4a","opus","webm","flac","wav","aac","gif","webp"],
                "features": {"batch": True, "uploads": True, "advanced": True, "shares": True, "pwa": True, "api_tokens": True}}

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker():
        response = FileResponse(STATIC / "sw.js", media_type="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/robots.txt")
    async def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    return app


app = create_app()
