from __future__ import annotations

import hmac
import json
import mimetypes
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, field_validator

from .adapters import available_tools, detect_tool, safe_segment
from .config import get_settings
from .database import Database
from .manager import DownloadManager, now


settings = get_settings()
database = Database(settings.database_path)
manager = DownloadManager(database, settings)
security = HTTPBasic(auto_error=False)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    await database.initialize()
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="Universal Download Manager", version="1.0.0", lifespan=lifespan)


async def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.auth_enabled:
        return
    valid = credentials and hmac.compare_digest(credentials.username, settings.username) and hmac.compare_digest(
        credentials.password, settings.password
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Universal Download Manager"'},
        )


class CreateDownload(BaseModel):
    url: str = Field(min_length=3, max_length=4096)
    name: str | None = Field(default=None, max_length=180)
    tool: Literal["auto", "aria2", "yt-dlp", "gallery-dl", "curl"] = "auto"
    category: str = Field(default="Other", min_length=1, max_length=100)
    priority: int = Field(default=0, ge=-100, le=100)
    options: dict = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def supported_url(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://", "ftp://", "magnet:")):
            raise ValueError("URL must start with http://, https://, ftp://, or magnet:")
        return value.strip()


class BulkDownload(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    tool: Literal["auto", "aria2", "yt-dlp", "gallery-dl", "curl"] = "auto"
    category: str = Field(default="Other", min_length=1, max_length=100)
    priority: int = Field(default=0, ge=-100, le=100)
    options: dict = Field(default_factory=dict)


class PriorityUpdate(BaseModel):
    priority: int = Field(ge=-100, le=100)


def public_row(row: dict) -> dict:
    row["options"] = json.loads(row.pop("options_json", "{}"))
    if row.get("output_path"):
        try:
            relative = Path(row["output_path"]).resolve().relative_to(settings.download_dir)
            row["relative_path"] = str(relative)
        except (ValueError, OSError):
            row["relative_path"] = None
    return row


def require_internal(token: str | None) -> None:
    expected = os.getenv("UDM_INTERNAL_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(404, "Not found")


async def insert_download(payload: CreateDownload) -> dict:
    job_id = str(uuid.uuid4())
    timestamp = now()
    category = safe_segment(payload.category)
    await database.execute(
        "INSERT INTO downloads (id,url,name,tool,category,status,priority,created_at,updated_at,options_json) "
        "VALUES (?,?,?,?,?,'queued',?,?,?,?)",
        (
            job_id, payload.url, payload.name.strip() if payload.name else None, payload.tool, category,
            payload.priority, timestamp, timestamp, json.dumps(payload.options),
        ),
    )
    row = await database.fetch_one("SELECT * FROM downloads WHERE id=?", (job_id,))
    return public_row(row or {})


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/system")
async def system_info(_: None = Depends(require_auth)):
    usage = shutil.disk_usage(settings.download_dir)
    return {
        "tools": available_tools(),
        "max_concurrent": settings.max_concurrent,
        "auth_enabled": settings.auth_enabled,
        "download_dir": str(settings.download_dir),
        "storage": {"total": usage.total, "used": usage.used, "free": usage.free},
    }


@app.get("/api/downloads")
async def list_downloads(
    state: str = Query(default="all"),
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=250, ge=1, le=1000),
    _: None = Depends(require_auth),
):
    clauses: list[str] = []
    params: list = []
    if state != "all":
        groups = {
            "active": ("queued", "downloading", "paused"),
            "finished": ("completed", "failed", "cancelled"),
        }
        states = groups.get(state, (state,))
        placeholders = ",".join("?" for _ in states)
        clauses.append(f"status IN ({placeholders})")
        params.extend(states)
    if search:
        clauses.append("(url LIKE ? OR name LIKE ? OR category LIKE ?)")
        term = f"%{search}%"
        params.extend((term, term, term))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    rows = await database.fetch_all(
        f"SELECT * FROM downloads{where} ORDER BY "
        "CASE WHEN status IN ('downloading','queued','paused') THEN 0 ELSE 1 END, "
        "priority DESC, created_at DESC LIMIT ?",
        params,
    )
    return [public_row(row) for row in rows]


@app.post("/api/downloads", status_code=201)
async def create_download(payload: CreateDownload, _: None = Depends(require_auth)):
    return await insert_download(payload)


@app.post("/api/downloads/bulk", status_code=201)
async def create_bulk(payload: BulkDownload, _: None = Depends(require_auth)):
    created = []
    for url in payload.urls:
        item = CreateDownload(
            url=url, tool=payload.tool, category=payload.category,
            priority=payload.priority, options=payload.options,
        )
        created.append(await insert_download(item))
    return created


@app.get("/api/downloads/{job_id}")
async def get_download(job_id: str, _: None = Depends(require_auth)):
    row = await database.fetch_one("SELECT * FROM downloads WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(404, "Download not found")
    return public_row(row)


@app.post("/api/downloads/{job_id}/pause")
async def pause_download(job_id: str, _: None = Depends(require_auth)):
    if not await manager.pause(job_id):
        raise HTTPException(409, "This download cannot be paused")
    return {"ok": True}


@app.post("/api/downloads/{job_id}/resume")
async def resume_download(job_id: str, _: None = Depends(require_auth)):
    if not await manager.resume(job_id):
        raise HTTPException(409, "This download cannot be resumed")
    return {"ok": True}


@app.post("/api/downloads/{job_id}/cancel")
async def cancel_download(job_id: str, _: None = Depends(require_auth)):
    if not await manager.cancel(job_id):
        raise HTTPException(409, "This download cannot be cancelled")
    return {"ok": True}


@app.patch("/api/downloads/{job_id}/priority")
async def update_priority(job_id: str, payload: PriorityUpdate, _: None = Depends(require_auth)):
    changed = await database.execute(
        "UPDATE downloads SET priority=?, updated_at=? WHERE id=?",
        (payload.priority, now(), job_id),
    )
    if not changed:
        raise HTTPException(404, "Download not found")
    return {"ok": True}


@app.delete("/api/downloads/{job_id}", status_code=204)
async def delete_download(job_id: str, delete_file: bool = False, _: None = Depends(require_auth)):
    row = await database.fetch_one("SELECT * FROM downloads WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(404, "Download not found")
    if row["status"] in {"downloading", "queued"}:
        await manager.cancel(job_id)
    if delete_file and row.get("output_path"):
        path = Path(row["output_path"]).resolve()
        try:
            path.relative_to(settings.download_dir)
        except ValueError:
            raise HTTPException(400, "Unsafe file path")
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    await database.execute("DELETE FROM downloads WHERE id=?", (job_id,))
    return Response(status_code=204)


@app.get("/api/downloads/{job_id}/file")
async def get_file(job_id: str, _: None = Depends(require_auth)):
    row = await database.fetch_one("SELECT output_path,status FROM downloads WHERE id=?", (job_id,))
    if not row or row["status"] != "completed" or not row["output_path"]:
        raise HTTPException(404, "Completed file not found")
    path = Path(row["output_path"]).resolve()
    try:
        path.relative_to(settings.download_dir)
    except ValueError:
        raise HTTPException(400, "Unsafe file path")
    if not path.is_file():
        raise HTTPException(404, "File is missing on disk")
    return FileResponse(path, filename=path.name, media_type=mimetypes.guess_type(path.name)[0])


@app.get("/api/detect")
async def detect(url: str, _: None = Depends(require_auth)):
    return {"tool": detect_tool(url)}


@app.get("/api/internal/state", include_in_schema=False)
async def export_state(x_crate_internal: str | None = Header(default=None)):
    require_internal(x_crate_internal)
    return Response(await database.export_bytes(), media_type="application/vnd.sqlite3")


@app.put("/api/internal/state", include_in_schema=False)
async def import_state(
    content: bytes = Body(media_type="application/vnd.sqlite3"),
    x_crate_internal: str | None = Header(default=None),
):
    require_internal(x_crate_internal)
    if len(content) > 64 * 1024 * 1024:
        raise HTTPException(413, "State snapshot is too large")
    try:
        await database.import_bytes(content)
    except (ValueError, OSError):
        raise HTTPException(400, "Invalid state snapshot")
    return {"ok": True}


app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/{path:path}")
async def web_app(path: str, _: None = Depends(require_auth)):
    return FileResponse(STATIC_DIR / "index.html")
