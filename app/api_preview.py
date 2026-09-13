"""Public-link preview endpoint used before a conversion is submitted."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.media_policy import validate_url


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)


def install(app, config, owner):
    cache: dict[str, tuple[float, dict]] = {}

    @app.post("/api/preview")
    async def preview(body: PreviewRequest, request: Request):
        owner(request)
        try:
            url = validate_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = cache.get(key)
        if cached and cached[0] > time.time() - 600:
            return {**cached[1], "cached": True}
        env = {name: value for name, value in os.environ.items() if name in {
            "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "LD_LIBRARY_PATH", "SSL_CERT_FILE"
        }}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.preview_lookup", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=config.preview_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(504, "Preview lookup timed out. You can still try the download.")
        try:
            data = json.loads(stdout or b"{}")
        except (ValueError, UnicodeError):
            raise HTTPException(502, "The source returned an unreadable preview response.")
        if process.returncode or not data.get("ok"):
            raise HTTPException(422, data.get("error", "This link could not be previewed."))
        result = {name: data.get(name) for name in ("url", "title", "creator", "duration", "source", "thumbnail")}
        cache[key] = (time.time(), result)
        if len(cache) > 200:
            oldest = min(cache, key=lambda item: cache[item][0])
            cache.pop(oldest, None)
        return {**result, "cached": False}
