"""Crate v6 FFmpeg conversion, advanced controls, hardware detection, subtitles, and metadata."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

MEDIA_DEMUXERS = "mov,mp3,matroska,webm,mpegts,ogg,flac,wav,aac,image2,gif,webp_pipe,vtt,srt"
AUDIO_FORMATS = {"mp3", "mka", "m4a", "opus", "flac", "wav", "aac"}
VIDEO_FORMATS = {"mp4", "mkv", "webm", "gif", "webp"}


def probe(path: Path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", MEDIA_DEMUXERS, "-show_format", "-show_streams", "-of", "json", str(path)
    ], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def available_encoders() -> set[str]:
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10, check=True)
    except Exception:
        return set()
    encoders = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) >= 6:
            encoders.add(parts[1])
    return encoders


def hardware_capabilities() -> dict:
    encoders = available_encoders()
    has_dri = Path("/dev/dri/renderD128").exists()
    has_nvidia = Path("/dev/nvidia0").exists() or shutil.which("nvidia-smi") is not None
    candidates = {
        "h264": [name for name, ok in (("h264_nvenc", has_nvidia), ("h264_vaapi", has_dri), ("h264_qsv", has_dri)) if ok and name in encoders],
        "hevc": [name for name, ok in (("hevc_nvenc", has_nvidia), ("hevc_vaapi", has_dri), ("hevc_qsv", has_dri)) if ok and name in encoders],
        "av1": [name for name, ok in (("av1_nvenc", has_nvidia), ("av1_vaapi", has_dri), ("av1_qsv", has_dri)) if ok and name in encoders],
        "vp9": [name for name, ok in (("vp9_vaapi", has_dri), ("vp9_qsv", has_dri)) if ok and name in encoders],
    }
    return {"available": any(candidates.values()), "encoders": candidates, "dri": has_dri, "nvidia": has_nvidia}


def ffmpeg_threads() -> str:
    raw = os.getenv("CRATE_FFMPEG_THREADS", "0").strip()
    try: return str(max(0, min(int(raw), 32)))
    except ValueError: return "0"


def _software_encoder(codec: str) -> str:
    encoders = available_encoders()
    choices = {
        "h264": ["libx264"], "hevc": ["libx265"], "av1": ["libsvtav1", "libaom-av1"], "vp9": ["libvpx-vp9"]
    }
    for candidate in choices.get(codec, ["libx264"]):
        if candidate in encoders:
            return candidate
    return "libx264"


def choose_video_encoder(codec: str, hardware: str) -> tuple[str, bool]:
    codec = "h264" if codec in {"auto", "copy"} else codec
    if hardware != "off":
        candidates = hardware_capabilities()["encoders"].get(codec, [])
        if candidates:
            return candidates[0], True
    return _software_encoder(codec), False


def _duration(details: dict) -> float:
    try: return float(details.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError): return 0.0


def _trim_duration(duration: float, options: dict) -> float:
    start = float(options.get("start") or 0)
    end = float(options.get("end") or duration or 0)
    if end and end > start:
        return min(duration - start, end - start) if duration else end - start
    return max(0, duration - start) if duration else 0


def _scale_filter(video: dict, quality: str, fps: int | None):
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    filters = []
    out_w, out_h = width, height
    if width > 0 and height > 0 and quality != "best":
        bound = int(quality)
        bound_w, bound_h = (bound * 16 / 9, bound) if width >= height else (bound, bound * 16 / 9)
        scale = min(1, bound_w / width, bound_h / height)
        out_w, out_h = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
        filters.append(f"scale={out_w}:{out_h}")
    if fps:
        filters.append(f"fps={fps}")
    return filters, out_w, out_h


def _codec_args(encoder: str, crf: int | None, hardware: bool) -> list[str]:
    crf = 20 if crf is None else crf
    if encoder == "libx264": return ["-c:v", encoder, "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if encoder == "libx265": return ["-c:v", encoder, "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if encoder == "libsvtav1": return ["-c:v", encoder, "-preset", "10", "-crf", str(crf), "-b:v", "0"]
    if encoder == "libaom-av1": return ["-c:v", encoder, "-cpu-used", "6", "-crf", str(crf), "-b:v", "0"]
    if encoder == "libvpx-vp9": return ["-c:v", encoder, "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-crf", str(crf), "-b:v", "0"]
    if hardware:
        # Hardware encoders use quality-oriented defaults rather than pretending CRF maps 1:1.
        if "nvenc" in encoder: return ["-c:v", encoder, "-preset", "p4", "-cq", str(max(1, min(crf, 51))), "-b:v", "0"]
        if "vaapi" in encoder: return ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload", "-c:v", encoder, "-qp", str(max(1, min(crf, 51)))]
        if "qsv" in encoder: return ["-c:v", encoder, "-global_quality", str(max(1, min(crf, 51)))]
    return ["-c:v", encoder]


def build_args(source: Path, target: Path, output_format: str, quality: str, details: dict,
               options: dict, metadata: dict, subtitle: Path | None = None, thumbnail: Path | None = None):
    streams = details.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    start = options.get("start")
    end = options.get("end")
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", MEDIA_DEMUXERS]
    if start is not None: args += ["-ss", str(float(start))]
    args += ["-i", str(source)]
    subtitle_index = None
    thumbnail_index = None
    next_index = 1
    if subtitle and options.get("subtitle_mode") == "embed":
        args += ["-i", str(subtitle)]; subtitle_index = next_index; next_index += 1
    if thumbnail and options.get("thumbnail") and output_format in {"mp3", "m4a", "mp4"}:
        args += ["-i", str(thumbnail)]; thumbnail_index = next_index; next_index += 1
    if end is not None:
        start_value = float(start or 0)
        args += ["-t", str(max(0.01, float(end) - start_value))]

    if output_format in AUDIO_FORMATS:
        if not audio: raise ValueError("This clip has no audio track to convert.")
        args += ["-map", "0:a:0", "-vn"]
        bitrate = int(options.get("audio_bitrate") or (320 if quality == "best" else quality if str(quality).isdigit() else 320))
        requested = options.get("audio_codec", "auto")
        if output_format == "mka" and requested in {"auto", "copy"}: args += ["-c:a", "copy"]
        elif output_format in {"m4a", "aac"}: args += ["-c:a", "aac", "-b:a", f"{bitrate}k"]
        elif output_format == "opus": args += ["-c:a", "libopus", "-b:a", f"{min(512, bitrate)}k"]
        elif output_format == "flac": args += ["-c:a", "flac"]
        elif output_format == "wav": args += ["-c:a", "pcm_s16le"]
        else: args += ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
        if thumbnail_index is not None:
            args += ["-map", f"{thumbnail_index}:v:0", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    elif output_format in {"gif", "webp"}:
        if not video: raise ValueError("This source has no video track.")
        filters, out_w, out_h = _scale_filter(video, quality, options.get("fps") or (15 if output_format == "gif" else None))
        if output_format == "gif":
            base = ",".join(filters or ["fps=15"])
            graph = f"[0:v]{base},split[a][b];[a]palettegen[p];[b][p]paletteuse[v]"
            args += ["-filter_complex", graph, "-map", "[v]", "-an", "-loop", "0"]
        else:
            args += ["-map", "0:v:0", "-an", "-c:v", "libwebp_anim", "-loop", "0"]
            if filters: args += ["-vf", ",".join(filters)]
    else:
        if not video: raise ValueError("This link contains audio only. Choose an audio output.")
        filters, _, _ = _scale_filter(video, quality, options.get("fps"))
        args += ["-map", "0:v:0", "-map", "0:a:0?"]
        copy_ok = options.get("video_codec") == "copy" and not filters and output_format in {"mkv", "webm"}
        if copy_ok:
            args += ["-c:v", "copy"]
        else:
            codec = options.get("video_codec", "auto")
            if output_format == "webm" and codec in {"auto", "copy", "h264", "hevc"}: codec = "vp9"
            encoder, hw = choose_video_encoder(codec, options.get("hardware", "auto"))
            codec_args = _codec_args(encoder, options.get("crf"), hw)
            # VAAPI owns its filter chain; otherwise prepend scaling/fps filters.
            if "vaapi" in encoder:
                extra = ",".join(filters)
                if extra:
                    codec_args = [item.replace("format=nv12,hwupload", f"{extra},format=nv12,hwupload") if item == "format=nv12,hwupload" else item for item in codec_args]
            elif filters:
                args += ["-vf", ",".join(filters)]
            args += codec_args
        if audio:
            requested_audio = options.get("audio_codec", "auto")
            if requested_audio == "copy" or (requested_audio == "auto" and ((output_format == "mp4" and audio.get("codec_name") == "aac") or (output_format == "webm" and audio.get("codec_name") == "opus"))):
                args += ["-c:a", "copy"]
            elif output_format == "webm": args += ["-c:a", "libopus", "-b:a", f"{int(options.get('audio_bitrate') or 192)}k"]
            else: args += ["-c:a", "aac", "-b:a", f"{int(options.get('audio_bitrate') or 256)}k"]
        if subtitle_index is not None:
            args += ["-map", f"{subtitle_index}:0"]
            args += ["-c:s", "mov_text" if output_format == "mp4" else ("webvtt" if output_format == "webm" else "srt")]
        if thumbnail_index is not None and output_format == "mp4":
            args += ["-map", f"{thumbnail_index}:v:0", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
        if output_format == "mp4": args += ["-movflags", "+faststart"]

    if options.get("metadata", True):
        for key, value in (("title", metadata.get("title")), ("artist", metadata.get("creator")), ("comment", metadata.get("source_url"))):
            if value: args += ["-metadata", f"{key}={str(value)[:500]}"]
    else:
        args += ["-map_metadata", "-1", "-map_chapters", "-1"]
    args += ["-threads", ffmpeg_threads(), "-progress", "pipe:1", "-nostats", str(target)]
    return args


def convert_file(source: Path, target: Path, output_format: str, emit, options: dict, metadata: dict,
                 max_duration=0, max_bytes=0, subtitle: Path | None = None, thumbnail: Path | None = None):
    details = probe(source)
    duration = _duration(details)
    if max_duration and duration > max_duration + 1: raise ValueError(f"Use a clip up to {max_duration // 60} minutes long.")
    output_duration = _trim_duration(duration, options)
    args = build_args(source, target, output_format, options.get("quality", "best"), details, options, metadata, subtitle, thumbnail)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    block = {}
    for raw in process.stdout:
        line = raw.strip()
        if "=" not in line: continue
        key, value = line.split("=", 1); block[key] = value
        if key == "progress":
            seconds = 0.0
            try: seconds = int(block.get("out_time_us", "0")) / 1_000_000
            except ValueError: pass
            percent = min(100, int(seconds / output_duration * 100)) if output_duration else 0
            emit("progress", status="converting", stage="converting", progress=90 + min(8, percent * 8 // 100),
                 phase_progress=percent, conversion_progress=percent,
                 downloaded_bytes=target.stat().st_size if target.exists() else 0, speed=None,
                 eta=max(0, output_duration - seconds) if output_duration else None)
            block = {}
    stderr = process.stderr.read() if process.stderr else ""
    if process.wait() != 0:
        raise RuntimeError("FFmpeg failed: " + " ".join(stderr.split())[-400:])
    if not target.is_file() or target.stat().st_size <= 0 or (max_bytes and target.stat().st_size > max_bytes):
        raise ValueError("No usable converted file was produced within the size limit.")
    output = probe(target)
    video = next((stream for stream in output.get("streams", []) if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")), {})
    encoder = None
    try:
        encoder = args[args.index("-c:v") + 1]
    except (ValueError, IndexError):
        pass
    return {"width": video.get("width"), "height": video.get("height"), "source_duration": duration,
            "output_duration": output_duration, "video_encoder": encoder}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
