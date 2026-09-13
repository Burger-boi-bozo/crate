"""Spawn and stop isolated media runner processes."""
from __future__ import annotations

import asyncio
import os
import sys


def runner_environment(directory):
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "LD_LIBRARY_PATH", "SSL_CERT_FILE",
        "CRATE_FRAGMENT_CONCURRENCY", "CRATE_DOWNLOAD_RETRIES", "CRATE_FFMPEG_THREADS"
    }}
    env.update(PYTHONUNBUFFERED="1", HOME=str(directory), TMPDIR=str(directory))
    return env


async def spawn(job, directory, config):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.media_runner_v3", job["url"], job["format"], str(directory),
        str(config.max_bytes), str(config.max_duration), job.get("quality", "best"),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=runner_environment(directory), start_new_session=True,
    )
