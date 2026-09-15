"""Isolated, network-free FFmpeg post-processing for Crate v6."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.media_convert_v6 import convert_file, sha256_file


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, separators=(",", ":")), flush=True)


def main(spec_path: Path):
    spec = json.loads(spec_path.read_text())
    directory = spec_path.parent.resolve()
    source = Path(spec["source"]).resolve()
    if source.parent != directory or source.is_symlink() or not source.is_file():
        raise ValueError("The intermediate media file is unavailable.")
    target = directory / ("final." + spec["format"])
    thumbnail = Path(spec["thumbnail"]).resolve() if spec.get("thumbnail") else None
    if thumbnail and (thumbnail.parent != directory or thumbnail.is_symlink() or not thumbnail.is_file()):
        thumbnail = None
    subtitle = Path(spec["subtitle"]).resolve() if spec.get("subtitle") else None
    if subtitle and (subtitle.parent != directory or subtitle.is_symlink() or not subtitle.is_file()):
        subtitle = None
    result = convert_file(
        source, target, spec["format"], emit, spec["options"], spec.get("metadata") or {},
        int(spec.get("max_duration") or 0), int(spec.get("max_bytes") or 0), subtitle, thumbnail,
    )
    emit("progress", status="finalizing", stage="finalizing", progress=99, phase_progress=50,
         downloaded_bytes=target.stat().st_size, total_bytes=target.stat().st_size, speed=None, eta=None)
    checksum = sha256_file(target)
    emit("result", file=target.name, checksum=checksum, width=result.get("width"), height=result.get("height"),
         source_duration=result.get("source_duration"), output_duration=result.get("output_duration"),
         video_encoder=result.get("video_encoder"))


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]).resolve())
    except Exception as exc:
        emit("error", message=str(exc)[:300], code="postprocess_failed", diagnostic=f"{type(exc).__name__}: {str(exc)[:350]}")
        raise SystemExit(1)
