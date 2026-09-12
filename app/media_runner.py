"""One job per process; stdout is a small JSON event stream for the queue."""
from __future__ import annotations

import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import yt_dlp
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.networking._urllib import UrllibRH

from app.media_policy import install_network_guard, validate_url

MEDIA_DEMUXERS = "mov,mp3,matroska,webm,mpegts,ogg,flac,wav,aac"


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}), flush=True)


class QuietLogger:
    def debug(self, message):
        pass

    info = warning = error = debug


class PublicYoutubeDL(yt_dlp.YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        return super().build_request_director([UrllibRH])

    def urlopen(self, request):
        url = request if isinstance(request, str) else request.url
        validate_url(url, source=False)
        if not isinstance(request, str) and getattr(request, "proxies", None):
            raise ValueError("Proxies are not supported.")
        return super().urlopen(request)


def deny_external_download(self, *args, **kwargs):
    # HLS can silently fall back to FFmpeg. Its network calls would not pass
    # through the guarded urllib transport, so this fallback must fail closed.
    raise ValueError("This stream needs an unsupported download method. Try another clip.")


def probe(path: Path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS, "-show_format",
         "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(result.stdout)


def convert_file(source: Path, target: Path, output_format: str, max_duration: int, max_bytes: int):
    details = probe(source)
    duration = float(details.get("format", {}).get("duration") or 0)
    if not 0 < duration <= max_duration + 1:
        raise ValueError(f"Use a clip up to {max_duration // 60} minutes long.")
    streams = details.get("streams", [])
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS, "-threads", "1", "-i", str(source)]
    if output_format == "mp3":
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("This clip has no audio track to convert.")
        args += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "128k"]
    else:
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video:
            raise ValueError("This link contains audio only. Choose MP3 to save it.")
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        if min(width, height) <= 0:
            raise ValueError("This source has no usable video dimensions.")
        bound_w, bound_h = (1920, 1080) if width >= height else (1080, 1920)
        scale = min(1, bound_w / width, bound_h / height)
        out_w, out_h = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
        args += ["-map", "0:v:0", "-map", "0:a:0?"]
        if video.get("codec_name") == "h264" and scale == 1 and video.get("pix_fmt") == "yuv420p":
            args += ["-c:v", "copy"]
        else:
            # Convert VP9/AV1/other public media to a broadly playable MP4.
            # Never upscale lower-resolution clips; bound encoding and bitrate.
            max_rate = max(128, min(3500, int(max_bytes * 8 / duration / 1000 * 0.9) - 128))
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                     "-vf", f"scale={out_w}:{out_h}", "-maxrate", f"{max_rate}k", "-bufsize", f"{max_rate * 2}k"]
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        args += ["-c:a", "copy"] if audio.get("codec_name") == "aac" else ["-c:a", "aac", "-b:a", "128k"]
        args += ["-movflags", "+faststart"]
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-threads", "1", str(target)]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=420, check=True)
    if not target.is_file() or not 0 < target.stat().st_size <= max_bytes:
        raise ValueError(f"The converted file exceeds {max_bytes // (1024 * 1024)} MB. Try a smaller clip.")
    output = probe(target)
    video = next((s for s in output.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"width": video.get("width"), "height": video.get("height")}


def error_code(error):
    text = str(error).lower()
    if any(s in text for s in ("not a bot", "captcha", "429", "too many requests", "confirm you’re not", "confirm you're not")):
        return "host_blocked"
    if any(s in text for s in ("sign in", "sign-in", "cookies", "login", "private video", "age-restricted")):
        return "sign_in_required"
    if "403" in text or "forbidden" in text:
        return "source_forbidden"
    if "requested format" in text:
        return "format_unavailable"
    if "unsupported url" in text:
        return "unsupported_source"
    if any(s in text for s in ("unavailable", "not available", "removed", "copyright")):
        return "source_unavailable"
    return "conversion_limit" if isinstance(error, ValueError) else "source_error"


def friendly_error(error):
    messages = {
        "host_blocked": "The source blocked requests from this cloud server. This link cannot be downloaded here right now. Use a creator-provided download if available.",
        "sign_in_required": "This source requires sign-in or age verification. This public service can only download media available without an account.",
        "source_forbidden": "The source refused access to its media file (403). Try a fresh public link or a download provided by the creator.",
        "format_unavailable": "This source doesn't offer the selected format up to 1080p. Try MP3 or another public clip.",
        "unsupported_source": "This page has no supported public media. Try the link to an individual video, podcast episode, or a direct audio/video file.",
        "source_unavailable": "This clip is unavailable to the hosted downloader. Try another public link.",
    }
    if error_code(error) in messages:
        return messages[error_code(error)]
    if isinstance(error, ValueError):
        return str(error)[:250]
    if isinstance(error, subprocess.TimeoutExpired):
        return "This conversion took too long. Try a shorter clip."
    return "The source could not provide this clip. It may be unsupported, restricted, or temporarily unavailable."


def safe_diagnostic(error):
    # Keep enough evidence to distinguish a provider block from a format error,
    # without putting signed URLs, submitted links, or control codes in logs.
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    message = re.sub(r"https?://\S+", "[URL]", message)
    message = re.sub(r"(?i)(token|cookie|authorization|key|signature)\s*[:=]\s*\S+", r"\1=[redacted]", message)
    return (type(error).__name__ + ": " + " ".join(message.split()))[:400]


def format_options(output_format):
    return {"format": "ba/b" if output_format == "mp3" else
            "bv[height<=?1080]+ba/b[height<=?1080]/bv[height<=?1080]",
            "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"]}


def run(url, output_format, directory, max_bytes, max_duration):
    validate_url(url)
    if output_format not in {"mp4", "mp3"}:
        raise ValueError("Choose MP4 or MP3.")
    # Bound individual files and CPU even if a native library hangs. The parent
    # also enforces a wall-clock deadline and total working-directory size.
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes * 2, max_bytes * 2))
    resource.setrlimit(resource.RLIMIT_CPU, (480, 490))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    install_network_guard()
    FFmpegFD.real_download = deny_external_download
    last_event = 0.0

    def progress(event):
        nonlocal last_event
        downloaded = event.get("downloaded_bytes") or 0
        if downloaded > max_bytes:
            raise ValueError("The source file is too large. Try a smaller clip.")
        now = time.monotonic()
        if now - last_event < 1 and event.get("status") != "finished":
            return
        last_event = now
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        emit("progress", status="downloading", progress=min(90, int(downloaded / total * 90)) if total else 0)

    def check_metadata(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            raise ValueError("Use a finished video; live streams aren't supported.")
        if info.get("duration") and info["duration"] > max_duration:
            raise ValueError(f"Use a clip up to {max_duration // 60} minutes long.")
        if info.get("has_drm"):
            raise ValueError("Protected media isn't supported.")
        return None

    options = {
        "quiet": True, "no_warnings": True, "logger": QuietLogger(), "noprogress": True,
        "noplaylist": True, "playlistend": 1, "lazy_playlist": True,
        "extract_flat": "in_playlist", "match_filter": check_metadata,
        "outtmpl": str(directory / "source.%(ext)s"), "restrictfilenames": True,
        "windowsfilenames": True, "cachedir": False, "proxy": "", "socket_timeout": 20,
        "retries": 2, "fragment_retries": 2, "extractor_retries": 1,
        "skip_unavailable_fragments": False, "concurrent_fragment_downloads": 1,
        "max_filesize": max_bytes, "progress_hooks": [progress],
        "enable_file_urls": False, "hls_prefer_native": True, "external_downloader": "native",
        "js_runtimes": {"node": {}}, "remote_components": [],
        "merge_output_format": "mkv", "overwrites": True,
        "postprocessor_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS]},
        **format_options(output_format),
    }
    with PublicYoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type", "video") != "video" or "entries" in info:
            raise ValueError("Paste a link to one clip, rather than a playlist or profile.")
        check_metadata(info)
        formats = info.get("requested_formats") or [info]
        for fmt in formats:
            if fmt.get("protocol") not in {"http", "https", "m3u8_native", "m3u8", "http_dash_segments"}:
                raise ValueError("This stream uses an unsupported download method.")
            if fmt.get("url"):
                validate_url(fmt["url"], source=False)
        downloader.process_info(info)
    sources = [p for p in directory.glob("source.*") if p.is_file() and p.suffix not in {".part", ".ytdl"}]
    if len(sources) != 1:
        raise ValueError("No complete media file was returned. Try another clip.")
    emit("progress", status="converting", progress=95)
    target = directory / ("download." + output_format)
    dimensions = convert_file(sources[0], target, output_format, max_duration, max_bytes)
    title = str(info.get("title") or "Media clip")[:200]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title).strip(" .")[:100] or "Media clip"
    emit("result", file=target.name, title=title, filename=f"{filename}.{output_format}", **dimensions)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2], Path(sys.argv[3]).resolve(), int(sys.argv[4]), int(sys.argv[5]))
    except Exception as exc:
        emit("error", message=friendly_error(exc), code=error_code(exc), diagnostic=safe_diagnostic(exc))
        sys.exit(1)
