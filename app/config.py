from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _integer(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    download_dir: Path
    max_concurrent: int
    poll_interval: float
    username: str
    password: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "downloads.db"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username and self.password)


def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("UDM_DATA_DIR", "/data")).expanduser().resolve(),
        download_dir=Path(os.getenv("UDM_DOWNLOAD_DIR", "/downloads")).expanduser().resolve(),
        max_concurrent=_integer("UDM_MAX_CONCURRENT", 2),
        poll_interval=float(os.getenv("UDM_POLL_INTERVAL", "0.7")),
        username=os.getenv("UDM_USERNAME", ""),
        password=os.getenv("UDM_PASSWORD", ""),
    )

