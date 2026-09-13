"""Crate v3 FastAPI application."""
from contextlib import asynccontextmanager
from pathlib import Path
import shutil

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import api_jobs, api_music, web_middleware
from app.job_queue import Queue
from app.media_policy import MEDIA_HOSTS
from app.models import Config
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
    get_owner = lambda request: owner(request, key)

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
    async def session(request):
        return session_response(request, config, key, MEDIA_HOSTS)

    @app.delete("/api/session", status_code=204)
    async def logout():
        return logout_response(config)

    api_jobs.install(app, queue, config, get_owner)
    api_music.install(app, queue, get_owner)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/robots.txt")
    async def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    return app


app = create_app()
