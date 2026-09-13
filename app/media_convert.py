"""FFmpeg conversion with progress events and configurable threading."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MEDIA_DEMUXERS = "mov,mp3,matroska,webm,mpegts,ogg,flac,wav,aac"


def probe(path: Path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", MEDIA_DEMUXERS, "-show_format", "-show_streams",
        "-of", "json", str(path)
    ], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def ffmpeg_threads() -> str:
    raw = os.getenv("CRATE_FFMPEG_THREADS", "0").strip()
    try:
        return str(max(0, min(int(raw), 32)))
    except ValueError:
        return "0"


def build_args(source: Path, target: Path, output_format: str, quality: str, details: dict):
    streams = details.get("streams", [])
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS,
            "-i", str(source)]
    if output_format == "mka":
        args += ["-map", "0:a:0", "-vn", "-c:a", "copy"]
    elif output_format == "mkv":
        args += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
    elif output_format == "mp3":
        if not any(stream.get("codec_type") == "audio" for stream in streams):
            raise ValueError("This clip has no audio track to convert.")
        args += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
                 "-b:a", ("320" if quality == "best" else quality) + "k"]
    else:
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if not video:
            raise ValueError("This link contains audio only. Choose MP3 to save it.")
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        if min(width, height) <= 0:
            raise ValueError("This source has no usable video dimensions.")
        bound = int(quality) if quality != "best" else min(width, height)
        bound_w, bound_h = (bound * 16 / 9, bound) if width >= height else (bound, bound * 16 / 9)
        scale = 1 if quality == "best" else min(1, bound_w / width, bound_h / height)
        out_w, out_h = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
        args += ["-map", "0:v:0", "-map", "0:a:0?"]
        if video.get("codec_name") == "h264" and scale == 1 and video.get("pix_fmt") == "yuv420p":
            args += ["-c:v", "copy"]
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-vf", f"scale={out_w}:{out_h}"]
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        args += ["-c:a", "copy"] if audio.get("codec_name") == "aac" else ["-c:a", "aac", "-b:a", "320k"]
        args += ["-movflags", "+faststart"]
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-threads", ffmpeg_threads(),
             "-progress", "pipe:1", "-nostats", str(target)]
    return args


def convert_file(source: Path, target: Path, output_format: str, emit, max_duration=0, max_bytes=0, quality="best"):
    details = probe(source)
    duration = float(details.get("format", {}).get("duration") or 0)
    if max_duration and duration > max_duration + 1:
        raise ValueError(f"Use a clip up to {max_duration // 60} minutes long.")
    process = subprocess.Popen(build_args(source, target, output_format, quality, details),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    block = {}
    for raw in process.stdout:
        line = raw.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        block[key] = value
        if key == "progress":
            seconds = 0.0
            try:
                seconds = int(block.get("out_time_us", "0")) / 1_000_000
            except ValueError:
                pass
            percent = min(100, int(seconds / duration * 100)) if duration else 0
            emit("progress", status="converting", stage="converting", progress=90 + min(9, percent * 9 // 100),
                 conversion_progress=percent, downloaded_bytes=target.stat().st_size if target.exists() else 0,
                 speed=None, eta=max(0, duration - seconds) if duration else None)
            block = {}
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    if not target.is_file() or target.stat().st_size <= 0 or (max_bytes and target.stat().st_size > max_bytes):
        raise ValueError("No usable converted file was produced within the size limit.")
    output = probe(target)
    video = next((stream for stream in output.get("streams", []) if stream.get("codec_type") == "video"), {})
    return {"width": video.get("width"), "height": video.get("height")}
