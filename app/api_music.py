"""Spotify/Apple Music metadata lookup route."""
import asyncio
import json
import os
import sys

from fastapi import HTTPException

from app.media_policy import validate_url


def install(app, queue, owner):
    @app.post("/api/music/lookup")
    async def music_lookup(request):
        owner(request)
        body = await request.json()
        try:
            url = validate_url(body.get("url", ""), source=False)
        except (ValueError, AttributeError) as exc:
            raise HTTPException(422, str(exc))
        env = {key: value for key, value in os.environ.items() if key in {
            "PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "SSL_CERT_FILE"
        }}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.music_lookup", url,
            env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=90)
            data = json.loads(stdout)
            if process.returncode or data.get("error"):
                raise HTTPException(422, data.get("error", "Song lookup failed."))
            return data
        except (asyncio.TimeoutError, ValueError):
            raise HTTPException(502, "Song lookup could not reach the source. Try a public link from the artist.")
        finally:
            await queue.kill(process)
