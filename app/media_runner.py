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
        if not video or video.get("codec_name") != "h264" or video.get("height", 0) > 720:
            raise ValueError("This source doesn't offer a compatible MP4 up to 720p. Try MP3 or another clip.")
        args += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac",
                 "-b:a", "128k", "-movflags", "+faststart"]
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-threads", "1", str(target)]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=420, check=True)
    if not target.is_file() or not 0 < target.stat().st_size <= max_bytes:
        raise ValueError(f"The converted file exceeds {max_bytes // (1024 * 1024)} MB. Try a smaller clip.")


def friendly_error(error):
    text = str(error).lower()
    if any(s in text for s in ("sign in", "bot", "captcha", "429", "403", "cookies", "login", "private video")):
        return "This site blocked the hosted downloader or requires sign-in. Try another public clip; private and restricted media aren't supported."
    if "requested format" in text:
        return "This source doesn't offer the selected format within the limits. Try MP3 or another clip."
    if any(s in text for s in ("unavailable", "not available", "removed", "copyright")):
        return "This clip is unavailable to the hosted downloader. Try another public link."
    if isinstance(error, ValueError):
        return str(error)[:250]
    return "The source could not provide this clip. It may be unsupported, restricted, or temporarily unavailable."


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
        "merge_output_format": "mp4", "overwrites": True,
        "postprocessor_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS]},
        "format": ("ba/b" if output_format == "mp3" else
                   "bv[height<=720][vcodec~='^(avc1|h264)']+ba[acodec~='^(mp4a|aac)']/b[height<=720][ext=mp4]/bv[height<=720][vcodec~='^(avc1|h264)']"),
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
    convert_file(sources[0], target, output_format, max_duration, max_bytes)
    title = str(info.get("title") or "Media clip")[:200]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title).strip(" .")[:100] or "Media clip"
    emit("result", file=target.name, title=title, filename=f"{filename}.{output_format}")


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2], Path(sys.argv[3]).resolve(), int(sys.argv[4]), int(sys.argv[5]))
    except Exception as exc:
        emit("error", message=friendly_error(exc))
        sys.exit(1)
