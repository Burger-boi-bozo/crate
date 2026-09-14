"""Resolve public media metadata, thumbnail, and conservative output estimates."""
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

def estimated_bytes(fmt: dict, duration: int) -> int | None:
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    if value:
        return max(0, int(value))
    bitrate = fmt.get("tbr")
    if duration and bitrate:
        return max(0, int(float(bitrate) * 1000 / 8 * duration))
    return None


def source_profile(info: dict) -> dict:
    duration = int(info.get("duration") or 0)
    formats = [item for item in info.get("formats") or [] if isinstance(item, dict)]
    videos = [item for item in formats if item.get("vcodec") not in {None, "none"}]
    audios = [item for item in formats if item.get("acodec") not in {None, "none"} and item.get("vcodec") in {None, "none"}]
    max_height = max((int(item.get("height") or 0) for item in videos), default=int(info.get("height") or 0))
    best_audio = max(audios, key=lambda item: float(item.get("abr") or item.get("tbr") or 0), default=None)
    audio_bytes = estimated_bytes(best_audio, duration) if best_audio else None

    def video_estimate(cap: int | None):
        candidates = videos if cap is None else [item for item in videos if int(item.get("height") or 0) <= cap]
        if not candidates:
            return None
        candidate = max(candidates, key=lambda item: (int(item.get("height") or 0), float(item.get("tbr") or 0)))
        size = estimated_bytes(candidate, duration)
        if size is not None and candidate.get("acodec") in {None, "none"} and audio_bytes:
            size += audio_bytes
        return size

    recommended_height = min(max_height or 1080, 1080)
    if recommended_height >= 1080:
        recommended_height = 1080
    elif recommended_height >= 720:
        recommended_height = 720
    elif recommended_height >= 480:
        recommended_height = 480
    recommendation = {
        "format": "mp4",
        "quality": str(recommended_height) if max_height else "best",
        "estimated_size": video_estimate(recommended_height if max_height else None),
        "reason": "Balanced compatibility, quality, and processing cost.",
    }
    estimates = {
        "best_video": video_estimate(None),
        "video_1080": video_estimate(1080),
        "video_720": video_estimate(720),
        "mp3_320": int(duration * 320000 / 8) if duration else None,
        "mp3_192": int(duration * 192000 / 8) if duration else None,
    }
    return {"max_height": max_height or None, "estimates": estimates, "recommendation": recommendation}


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
            **source_profile(info),
        }


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **lookup(sys.argv[1])}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": friendly_error(exc),
                          "error_code": error_code(exc), "diagnostic": safe_diagnostic(exc)}))
        raise SystemExit(1)
