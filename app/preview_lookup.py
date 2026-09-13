"""Resolve public media metadata and a small thumbnail without downloading the media."""
from __future__ import annotations

import base64
import json
import sys

import yt_dlp

from app.media_policy import install_network_guard, validate_url
from app.media_runner import PublicYoutubeDL, QuietLogger, error_code, friendly_error, safe_diagnostic

THUMBNAIL_LIMIT = 1_500_000


def thumbnail_data(downloader, url: str | None) -> str | None:
    if not url:
        return None
    try:
        validate_url(url, source=False)
        response = downloader.urlopen(url)
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            return None
        payload = response.read(THUMBNAIL_LIMIT + 1)
        if len(payload) > THUMBNAIL_LIMIT:
            return None
        return f"data:{content_type};base64,{base64.b64encode(payload).decode()}"
    except Exception:
        return None


def lookup(url: str) -> dict:
    url = validate_url(url)
    install_network_guard()
    options = {
        "quiet": True, "no_warnings": True, "logger": QuietLogger(),
        "noplaylist": True, "playlistend": 1, "skip_download": True,
        "cachedir": False, "proxy": "", "socket_timeout": 12,
        "retries": 1, "extractor_retries": 1,
        "js_runtimes": {"node": {}}, "remote_components": [],
    }
    with PublicYoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type", "video") != "video" or "entries" in info:
            raise ValueError("Paste a link to one video, song, or media file.")
        if info.get("has_drm"):
            raise ValueError("Protected media isn't supported.")
        return {
            "url": url,
            "title": str(info.get("title") or "Media clip")[:200],
            "creator": str(info.get("uploader") or info.get("channel") or info.get("artist") or "")[:120],
            "duration": int(info.get("duration") or 0),
            "source": str(info.get("extractor_key") or info.get("extractor") or "Media")[:80],
            "thumbnail": thumbnail_data(downloader, info.get("thumbnail")),
        }


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **lookup(sys.argv[1])}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": friendly_error(exc),
                          "error_code": error_code(exc), "diagnostic": safe_diagnostic(exc)}))
        raise SystemExit(1)
