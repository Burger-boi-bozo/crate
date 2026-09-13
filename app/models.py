"""Runtime configuration and request models."""
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


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CRATE_DATA_DIR", "converter-data")).resolve())
    secret: str = field(default_factory=lambda: os.getenv("CRATE_SESSION_SECRET", "") or secrets.token_urlsafe(48))
    admin_password: str = field(default_factory=lambda: os.getenv("CRATE_ADMIN_PASSWORD", ""))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("CRATE_SECURE_COOKIE", "true") != "false")
    workers: int = field(default_factory=lambda: env_int("CRATE_WORKERS", 2, 1, 8))
    stall_timeout: int = field(default_factory=lambda: env_int("CRATE_STALL_TIMEOUT", 300, 30, 3600))
    preview_timeout: int = field(default_factory=lambda: env_int("CRATE_PREVIEW_TIMEOUT", 20, 5, 60))
    max_bytes: int = 0
    max_work_bytes: int = 0
    max_duration: int = 0
    timeout: int = 0
    ttl: int = 0
    max_queue: int = 0
    daily_jobs: int = 0
    max_downloads: int = 0


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    format: Literal["mp4", "mp3", "mkv", "mka"] = "mp4"
    quality: Literal["best", "2160", "1440", "1080", "720", "480", "320", "256", "192", "128"] = "best"

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return validate_url(value)

    @model_validator(mode="after")
    def check_quality(self):
        allowed = {"best", "320", "256", "192", "128"} if self.format in {"mp3", "mka"} else {"best", "2160", "1440", "1080", "720", "480"}
        if self.quality not in allowed:
            raise ValueError("Choose a quality available for this format.")
        if self.format in {"mka", "mkv"} and self.quality != "best":
            raise ValueError("Original formats preserve the best source quality.")
        return self
