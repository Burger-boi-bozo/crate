"""Runtime configuration and request models for Crate v6."""
from __future__ import annotations
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.media_policy import validate_url

RUNNING = {"resolving", "downloading", "converting", "finalizing"}
ACTIVE = {"queued", *RUNNING, "paused", "retry_wait"}


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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CRATE_DATA_DIR", "converter-data")).resolve())
    secret: str = field(default_factory=lambda: os.getenv("CRATE_SESSION_SECRET", "") or secrets.token_urlsafe(48))
    admin_password: str = field(default_factory=lambda: os.getenv("CRATE_ADMIN_PASSWORD", ""))
    secure_cookie: bool = field(default_factory=lambda: env_bool("CRATE_SECURE_COOKIE", True))
    workers: int = field(default_factory=lambda: env_int("CRATE_WORKERS", 3, 1, 8))
    heavy_workers: int = field(default_factory=lambda: env_int("CRATE_HEAVY_WORKERS", 1, 1, 8))
    per_owner_active: int = field(default_factory=lambda: env_int("CRATE_PER_OWNER_ACTIVE", 2, 1, 8))
    max_load_ratio: float = field(default_factory=lambda: env_float("CRATE_MAX_LOAD_RATIO", 1.25, 0.5, 8.0))
    min_available_memory: int = field(default_factory=lambda: env_int("CRATE_MIN_AVAILABLE_MEMORY", 768 * 1024 ** 2, 0))
    stall_timeout: int = field(default_factory=lambda: env_int("CRATE_STALL_TIMEOUT", 300, 30, 3600))
    preview_timeout: int = field(default_factory=lambda: env_int("CRATE_PREVIEW_TIMEOUT", 20, 5, 60))
    min_free_bytes: int = field(default_factory=lambda: env_int("CRATE_MIN_FREE_BYTES", 1024 ** 3, 0))
    max_batch: int = field(default_factory=lambda: env_int("CRATE_MAX_BATCH", 20, 2, 50))
    max_upload_bytes: int = field(default_factory=lambda: env_int("CRATE_MAX_UPLOAD_BYTES", 8 * 1024 ** 3, 1024 ** 2))
    max_auto_retries: int = field(default_factory=lambda: env_int("CRATE_AUTO_RETRIES", 2, 0, 5))
    share_default_ttl: int = field(default_factory=lambda: env_int("CRATE_SHARE_TTL", 86400, 300, 30 * 86400))
    admin_session_ttl: int = field(default_factory=lambda: env_int("CRATE_ADMIN_SESSION_TTL", 12 * 3600, 300, 30 * 86400))
    admin_trusted_ips: str = field(default_factory=lambda: os.getenv("CRATE_ADMIN_TRUSTED_IPS", ""))
    webhook_timeout: int = field(default_factory=lambda: env_int("CRATE_WEBHOOK_TIMEOUT", 10, 2, 30))
    cache_reuse: bool = field(default_factory=lambda: env_bool("CRATE_CACHE_REUSE", True))
    max_bytes: int = 0
    max_work_bytes: int = 0
    max_duration: int = 0
    timeout: int = 0
    ttl: int = field(default_factory=lambda: env_int("CRATE_TTL", 86400, 60))
    max_queue: int = 0
    daily_jobs: int = 0
    max_downloads: int = 0


FormatName = Literal["mp4", "mp3", "mkv", "mka", "m4a", "opus", "webm", "flac", "wav", "aac", "gif", "webp"]
QualityName = Literal["best", "2160", "1440", "1080", "720", "480", "320", "256", "192", "128"]
PriorityName = Literal["low", "normal", "high"]
VideoCodec = Literal["auto", "copy", "h264", "hevc", "av1", "vp9"]
AudioCodec = Literal["auto", "copy", "aac", "opus", "mp3", "flac", "pcm_s16le"]
HardwareMode = Literal["auto", "off"]
SubtitleMode = Literal["off", "embed", "external"]

VIDEO_FORMATS = {"mp4", "mkv", "webm", "gif", "webp"}
AUDIO_FORMATS = {"mp3", "mka", "m4a", "opus", "flac", "wav", "aac"}
LOSSLESS_FORMATS = {"mkv", "mka", "flac", "wav"}
_FILENAME_TOKEN = re.compile(r"\{(title|creator|source|format|quality|resolution|id)\}")


class MediaOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: FormatName = "mp4"
    quality: QualityName = "best"
    priority: PriorityName = "normal"
    start: float | None = Field(default=None, ge=0, le=86400)
    end: float | None = Field(default=None, ge=0, le=86400)
    fps: int | None = Field(default=None, ge=1, le=120)
    crf: int | None = Field(default=None, ge=0, le=51)
    video_codec: VideoCodec = "auto"
    audio_codec: AudioCodec = "auto"
    audio_bitrate: int | None = Field(default=None, ge=64, le=512)
    hardware: HardwareMode = "auto"
    metadata: bool = False
    thumbnail: bool = False
    subtitles: bool = False
    subtitle_langs: list[str] = Field(default_factory=lambda: ["en"], max_length=10)
    subtitle_mode: SubtitleMode = "off"
    filename_template: str = Field(default="{title}", min_length=1, max_length=160)

    @field_validator("subtitle_langs")
    @classmethod
    def clean_langs(cls, values):
        cleaned = []
        for value in values:
            value = value.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]{1,16}", value):
                raise ValueError("Subtitle languages use short codes such as en or es-419.")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned or ["en"]

    @field_validator("filename_template")
    @classmethod
    def check_template(cls, value):
        stripped = _FILENAME_TOKEN.sub("", value)
        if "{" in stripped or "}" in stripped:
            raise ValueError("Filename template contains an unknown placeholder.")
        return value.strip()

    @model_validator(mode="after")
    def check_options(self):
        if self.end is not None and self.start is not None and self.end <= self.start:
            raise ValueError("Trim end must be after trim start.")
        if self.format in VIDEO_FORMATS:
            if self.quality not in {"best", "2160", "1440", "1080", "720", "480"}:
                raise ValueError("Choose a video quality for this format.")
        else:
            if self.quality not in {"best", "320", "256", "192", "128"}:
                raise ValueError("Choose an audio quality for this format.")
        if self.format in LOSSLESS_FORMATS and self.quality != "best":
            raise ValueError("Original/lossless formats use the best source quality.")
        if self.format in {"gif", "webp"} and self.video_codec not in {"auto"}:
            raise ValueError("GIF/WebP choose their encoder automatically.")
        if self.subtitle_mode != "off" and not self.subtitles:
            raise ValueError("Enable subtitles before choosing a subtitle output mode.")
        if self.format not in {"mp4", "mkv", "webm"} and self.subtitle_mode == "embed":
            raise ValueError("Embedded subtitles are available for MP4, MKV, and WebM outputs.")
        return self

    def option_dict(self) -> dict:
        return self.model_dump(exclude={"url", "urls"}, mode="json")


class Submission(MediaOptions):
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return validate_url(value.strip())


class BatchSubmission(MediaOptions):
    urls: list[str] = Field(min_length=2, max_length=50)

    def submissions(self, max_batch: int) -> list[Submission]:
        cleaned = list(dict.fromkeys(url.strip() for url in self.urls if url.strip()))
        if len(cleaned) < 2:
            raise ValueError("Paste at least two different links for a batch.")
        if len(cleaned) > max_batch:
            raise ValueError(f"A batch can contain up to {max_batch} links.")
        options = self.model_dump(exclude={"urls"})
        return [Submission(url=url, **options) for url in cleaned]
