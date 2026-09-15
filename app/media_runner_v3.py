"""One isolated Crate v3 media job with structured progress output."""
from __future__ import annotations

import json
import os
import re
import resource
import sys
import time
from pathlib import Path

from yt_dlp.downloader.external import FFmpegFD

from app.media_convert import convert_file
from app.media_policy import install_network_guard, validate_url
from app.media_runner import (PublicYoutubeDL, QuietLogger, deny_external_download,
                              error_code, format_options, friendly_error, safe_diagnostic)


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}), flush=True)


def env_int(name, default, minimum=1, maximum=32):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def run(url, output_format, directory, max_bytes=0, max_duration=0, quality="best", runner_options=None):
    validate_url(url)
    runner_options = runner_options or {}
    if output_format not in {"mp4", "mp3", "mkv", "mka"}:
        raise ValueError("Choose MP4, MP3, MKV, or MKA.")
    if max_bytes:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes * 2, max_bytes * 2))
    install_network_guard()
    FFmpegFD.real_download = deny_external_download
    last_event = 0.0

    def progress(event):
        nonlocal last_event
        downloaded = int(event.get("downloaded_bytes") or 0)
        total = int(event.get("total_bytes") or event.get("total_bytes_estimate") or 0)
        if max_bytes and downloaded > max_bytes:
            raise ValueError("The source file is too large. Try a smaller clip.")
        now = time.monotonic()
        if now - last_event < 0.75 and event.get("status") != "finished":
            return
        last_event = now
        emit("progress", status="downloading", stage="downloading",
             progress=min(89, int(downloaded / total * 89)) if total else 0,
             downloaded_bytes=downloaded, total_bytes=total,
             speed=event.get("speed"), eta=event.get("eta"), conversion_progress=None)

    def check_metadata(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            raise ValueError("Use a finished video; live streams aren't supported.")
        if max_duration and info.get("duration") and info["duration"] > max_duration:
            raise ValueError(f"Use a clip up to {max_duration // 60} minutes long.")
        if info.get("has_drm"):
            raise ValueError("Protected media isn't supported.")
        return None

    emit("progress", status="downloading", stage="resolving", progress=0,
         downloaded_bytes=0, total_bytes=0, speed=None, eta=None)
    retries = env_int("CRATE_DOWNLOAD_RETRIES", 4, 0, 10)
    fragments = env_int("CRATE_FRAGMENT_CONCURRENCY", 4, 1, 16)
    options = {
        "quiet": True, "no_warnings": True, "logger": QuietLogger(), "noprogress": True,
        "noplaylist": True, "playlistend": 1, "lazy_playlist": True,
        "extract_flat": "in_playlist", "match_filter": check_metadata,
        "outtmpl": str(directory / "source.%(ext)s"), "restrictfilenames": True,
        "windowsfilenames": True, "cachedir": False, "proxy": "", "socket_timeout": 20,
        "retries": retries, "fragment_retries": retries, "extractor_retries": max(1, retries // 2),
        "skip_unavailable_fragments": False, "concurrent_fragment_downloads": fragments,
        "max_filesize": max_bytes or None, "progress_hooks": [progress],
        "enable_file_urls": False, "hls_prefer_native": True, "external_downloader": "native",
        "js_runtimes": {"node": {}}, "remote_components": [], "merge_output_format": "mkv",
        "overwrites": True, **format_options(output_format, quality),
    }
    if runner_options.get("subtitles"):
        options["writesubtitles"] = True
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = list(runner_options.get("subtitle_langs") or ["en"])
        options["subtitlesformat"] = "vtt/srt/best"
    with PublicYoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type", "video") != "video" or "entries" in info:
            raise ValueError("Paste a link to one clip, rather than a playlist or profile.")
        check_metadata(info)
        formats = info.get("requested_formats") or [info]
        for media_format in formats:
            if media_format.get("protocol") not in {"http", "https", "m3u8_native", "m3u8", "http_dash_segments"}:
                raise ValueError("This stream uses an unsupported download method.")
            if media_format.get("url"):
                validate_url(media_format["url"], source=False)
        downloader.process_info(info)
    subtitle_suffixes = {".vtt", ".srt", ".ass", ".ssa", ".lrc"}
    subtitles = [path for path in directory.glob("source.*") if path.is_file() and path.suffix.lower() in subtitle_suffixes]
    sources = [path for path in directory.glob("source.*") if path.is_file() and path.suffix.lower() not in subtitle_suffixes | {".part", ".ytdl", ".json"}]
    if len(sources) != 1:
        raise ValueError("No complete media file was returned. Try another clip.")
    emit("progress", status="converting", stage="converting", progress=90,
         downloaded_bytes=sources[0].stat().st_size, total_bytes=sources[0].stat().st_size,
         speed=None, eta=None, conversion_progress=0)
    target = directory / ("download." + output_format)
    dimensions = convert_file(sources[0], target, output_format, emit, max_duration, max_bytes, quality)
    title = str(info.get("title") or "Media clip")[:200]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title).strip(" .")[:100] or "Media clip"
    subtitle = subtitles[0].name if subtitles else None
    emit("result", file=target.name, title=title, filename=f"{filename}.{output_format}", subtitle=subtitle, **dimensions)


if __name__ == "__main__":
    try:
        runner_options = {}
        if len(sys.argv) > 7:
            options_path = Path(sys.argv[7]).resolve()
            if options_path.parent == Path(sys.argv[3]).resolve() and options_path.is_file():
                runner_options = json.loads(options_path.read_text())
        run(sys.argv[1], sys.argv[2], Path(sys.argv[3]).resolve(), int(sys.argv[4]),
            int(sys.argv[5]), sys.argv[6] if len(sys.argv) > 6 else "best", runner_options)
    except Exception as exc:
        emit("error", message=friendly_error(exc), code=error_code(exc), diagnostic=safe_diagnostic(exc))
        sys.exit(1)
