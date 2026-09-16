"""Authenticated client for the optional Crate 6.1 GPU transcode worker."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from app.job_events import apply_progress
from app.version import RELEASE_VERSION


class GpuWorkerError(RuntimeError):
    pass


def configured(config) -> bool:
    return bool(config.gpu_worker_url and config.gpu_worker_token)


def eligible(job: dict) -> bool:
    options = job.get("options") or {}
    codec = options.get("video_codec", "auto")
    return (
        options.get("hardware", "auto") != "off"
        and job.get("format") in {"mp4", "mkv"}
        and codec in {"auto", "h264", "hevc"}
        and not options.get("thumbnail")
        and options.get("subtitle_mode", "off") != "embed"
    )


def _headers(config) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.gpu_worker_token}", "X-Crate-GPU": "1"}


async def health(config, timeout: float = 2.0) -> dict:
    if not configured(config):
        return {"configured": False, "available": False}
    try:
        async with httpx.AsyncClient(base_url=config.gpu_worker_url, headers=_headers(config), timeout=timeout) as client:
            response = await client.get("/health")
            response.raise_for_status()
            data = response.json()
        data["configured"] = True
        data["compatible"] = data.get("version") == RELEASE_VERSION
        return data
    except Exception as exc:
        return {"configured": True, "available": False, "error": type(exc).__name__}


async def _cancel(client: httpx.AsyncClient, worker_id: str) -> None:
    try:
        await client.delete(f"/jobs/{worker_id}")
    except Exception:
        pass


async def transcode(config, queue, job: dict, source: Path, target: Path, metadata: dict) -> dict:
    timeout = httpx.Timeout(config.gpu_worker_timeout, connect=5.0)
    async with httpx.AsyncClient(base_url=config.gpu_worker_url, headers=_headers(config), timeout=timeout) as client:
        with source.open("rb") as stream:
            response = await client.post(
                "/jobs",
                data={
                    "format": job["format"],
                    "options": json.dumps(job.get("options") or {}, separators=(",", ":")),
                    "metadata": json.dumps(metadata or {}, separators=(",", ":")),
                },
                files={"source": (source.name, stream, "application/octet-stream")},
            )
        response.raise_for_status()
        worker_id = response.json()["id"]
        started = time.monotonic()
        try:
            while True:
                if job.get("status") == "cancelled":
                    await _cancel(client, worker_id)
                    raise asyncio.CancelledError()
                response = await client.get(f"/jobs/{worker_id}")
                response.raise_for_status()
                state = response.json()
                status = state.get("status")
                progress = int(state.get("progress") or 0)
                previous_stage = job.get("stage")
                apply_progress(job, {
                    "status": "converting", "stage": "converting",
                    "progress": 90 + min(8, progress * 8 // 100),
                    "phase_progress": progress, "conversion_progress": progress,
                    "downloaded_bytes": int(state.get("bytes") or 0),
                    "speed": None, "eta": state.get("eta"),
                })
                if job.get("stage") != previous_stage:
                    queue.event(job, "stage", f"Stage changed to {job['stage']}", {"stage": job["stage"]})
                if status == "ready":
                    break
                if status == "failed":
                    raise GpuWorkerError(state.get("error") or "GPU worker conversion failed.")
                if time.monotonic() - started > config.gpu_worker_timeout:
                    raise GpuWorkerError("GPU worker timed out.")
                await asyncio.sleep(0.5)
            async with client.stream("GET", f"/jobs/{worker_id}/file") as output:
                output.raise_for_status()
                with target.open("wb") as stream:
                    async for chunk in output.aiter_bytes(1024 * 1024):
                        stream.write(chunk)
            return {
                "video_encoder": state.get("video_encoder"),
                "width": state.get("width"), "height": state.get("height"),
                "source_duration": state.get("source_duration"), "output_duration": state.get("output_duration"),
                "gpu_worker": state.get("worker") or "remote",
            }
        finally:
            await _cancel(client, worker_id)
