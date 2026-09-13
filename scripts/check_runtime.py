#!/usr/bin/env python3
"""Fail deployment before restart if the checked-out Crate runtime cannot import."""
import shutil

from app.runtime import app
from app.version import RELEASE_VERSION, RUNTIME_VERSION

missing = [name for name in ("ffmpeg", "ffprobe", "node") if not shutil.which(name)]
if missing:
    raise SystemExit("Missing runtime tools: " + ", ".join(missing))
if app.title != "Crate · Link to file":
    raise SystemExit("Unexpected FastAPI application")
print(f"Crate runtime OK: {RUNTIME_VERSION} v{RELEASE_VERSION}")
