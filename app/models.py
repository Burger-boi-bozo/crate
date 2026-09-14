"""Runtime configuration and request models."""
from __future__ import annotations
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.media_policy import validate_url

RUNNING = {"downloading", "converting"}
ACTIVE = {"queued", "downloading", "converting", "paused"}


def env_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def env_float(name: str, default: float, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value

@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CRATE_DATA_DIR", "converter-data")).resolve())
    secret: str = field(default_factory=lambda: os.getenv("CRATE_SESSION_SECRET", "") or secrets.token_urlsafe(48))
    admin_password: str = field(default_factory=lambda: os.getenv("CRATE_ADMIN_PASSWORD", ""))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("CRATE_SECURE_COOKIE", "true") != "false")
    workers: int = field(default_factory=lambda: env_int("CRATE_WORKERS", 2, 1, 8))
    heavy_workers: int = field(default_factory=lambda: env_int("CRATE_HEAVY_WORKERS", 1, 1, 8))
    per_owner_active: int = field(default_factory=lambda: env_int("CRATE_PER_OWNER_ACTIVE", 2, 1, 8))
    max_load_ratio: float = field(default_factory=lambda: env_float("CRATE_MAX_LOAD_RATIO", 1.5, 0.5, 8.0))
    stall_timeout: int = field(default_factory=lambda: env_int("CRATE_STALL_TIMEOUT", 300, 30, 3600))
    preview_timeout: int = field(default_factory=lambda: env_int("CRATE_PREVIEW_TIMEOUT", 20, 5, 60))
    min_free_bytes: int = field(default_factory=lambda: env_int("CRATE_MIN_FREE_BYTES", 1024 ** 3, 0))
    max_batch: int = field(default_factory=lambda: env_int("CRATE_MAX_BATCH", 20, 2, 50))
    max_bytes: int = 0
    max_work_bytes: int = 0
    max_duration: int = 0
    timeout: int = 0
    ttl: int = 0
    max_queue: int = 0
    daily_jobs: int = 0
    max_downloads: int = 0


FormatName = Literal["mp4", "mp3", "mkv", "mka"]
QualityName = Literal["best", "2160", "1440", "1080", "720", "480", "320", "256", "192", "128"]

class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    format: FormatName = "mp4"
    quality: QualityName = "best"

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return validate_url(value.strip())

    @model_validator(mode="after")
    def check_quality(self):
        allowed = {"best", "320", "256", "192", "128"} if self.format in {"mp3", "mka"} else {"best", "2160", "1440", "1080", "720", "480"}
        if self.quality not in allowed:
            raise ValueError("Choose a quality available for this format.")
        if self.format in {"mka", "mkv"} and self.quality != "best":
            raise ValueError("Original formats preserve the best source quality.")
        return self


class BatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: list[str] = Field(min_length=2, max_length=50)
    format: FormatName = "mp4"
    quality: QualityName = "best"

    def submissions(self, max_batch: int) -> list[Submission]:
        cleaned = list(dict.fromkeys(url.strip() for url in self.urls if url.strip()))
        if len(cleaned) < 2:
            raise ValueError("Paste at least two different links for a batch.")
        if len(cleaned) > max_batch:
            raise ValueError(f"A batch can contain up to {max_batch} links.")
        return [Submission(url=url, format=self.format, quality=self.quality) for url in cleaned]
