"""Helpers for applying downloader events to persistent job state."""

from app.errors import JobError


def apply_progress(job: dict, event: dict) -> None:
    for key in ("downloaded_bytes", "total_bytes", "speed", "eta", "conversion_progress"):
        if key in event:
            job[key] = event[key]
    job["progress"] = max(0, min(99, int(event.get("progress", job.get("progress", 0)) or 0)))
    job["status"] = event.get("status", "downloading")
    job["stage"] = event.get("stage", job["status"])


def source_error(job: dict, event: dict) -> JobError:
    job["error_code"] = event.get("code", "source_error")
    job["diagnostic"] = event.get("diagnostic", "")[:400]
    return JobError(event.get("message", "The source could not provide this media."), job["error_code"])
