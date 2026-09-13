#!/usr/bin/env python3
"""Fail deployment before restart if the checked-out Crate runtime cannot import."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime import app
from app.version import RELEASE_VERSION, RUNTIME_VERSION

missing = [name for name in ("ffmpeg", "ffprobe", "node") if not shutil.which(name)]
if missing:
    raise SystemExit("Missing runtime tools: " + ", ".join(missing))
if app.title != "Crate · Link to file":
    raise SystemExit("Unexpected FastAPI application")
print(f"Crate runtime OK: {RUNTIME_VERSION} v{RELEASE_VERSION}")
