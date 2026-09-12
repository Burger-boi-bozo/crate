from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


VIDEO_HOSTS = {
    "youtube.com", "www.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com",
    "tiktok.com", "www.tiktok.com", "twitter.com", "x.com", "twitch.tv",
    "www.twitch.tv", "reddit.com", "www.reddit.com",
}
GALLERY_HOSTS = {
    "flickr.com", "www.flickr.com", "imgur.com", "www.imgur.com",
    "deviantart.com", "www.deviantart.com", "pixiv.net", "www.pixiv.net",
}


@dataclass(frozen=True)
class CommandSpec:
    command: list[str]
    progress_kind: str
    expected_name: str | None = None


def safe_segment(value: str, fallback: str = "Other") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", value).strip(" .")
    cleaned = cleaned.replace("..", "-")
    cleaned = re.sub(r"-+", "-", cleaned).strip("- .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:100] or fallback


def detect_tool(url: str) -> str:
    lowered = url.lower().strip()
    if lowered.startswith("magnet:") or lowered.endswith(".torrent"):
        return "aria2"
    host = urlparse(lowered).hostname or ""
    if host in VIDEO_HOSTS:
        return "yt-dlp"
    if host in GALLERY_HOSTS:
        return "gallery-dl"
    return "aria2"


def resolve_tool(requested: str, url: str) -> str:
    return detect_tool(url) if requested == "auto" else requested


def available_tools() -> dict[str, bool]:
    return {name: shutil.which(name) is not None for name in ("aria2c", "yt-dlp", "gallery-dl", "curl")}


def build_command(
    tool: str,
    url: str,
    destination: Path,
    name: str | None,
    options: dict,
) -> CommandSpec:
    destination.mkdir(parents=True, exist_ok=True)
    clean_name = safe_segment(name, "download") if name else None

    if tool == "aria2":
        command = [
            "aria2c", "--dir", str(destination), "--continue=true", "--auto-file-renaming=false",
            "--file-allocation=none", "--summary-interval=1", "--console-log-level=notice",
            "--download-result=full", "--seed-time=0",
        ]
        if clean_name and not url.lower().startswith(("magnet:",)) and not url.lower().endswith(".torrent"):
            command += ["--out", clean_name]
        command += [url]
        return CommandSpec(command, "aria2", clean_name)

    if tool == "yt-dlp":
        output = str(destination / ((clean_name + ".%(ext)s") if clean_name else "%(title).180B [%(id)s].%(ext)s"))
        command = [
            "yt-dlp", "--newline", "--progress", "--no-colors", "--continue", "--no-overwrites",
            "--output", output, "--print", "after_move:__UDM_FILE__:%(filepath)s",
        ]
        if options.get("audio_only"):
            command += ["--extract-audio", "--audio-format", options.get("audio_format", "mp3")]
        else:
            command += ["--format", options.get("format", "bv*+ba/b")]
            command += ["--merge-output-format", options.get("container", "mp4")]
        if options.get("playlist") is False:
            command.append("--no-playlist")
        command.append(url)
        return CommandSpec(command, "ytdlp", clean_name)

    if tool == "gallery-dl":
        command = [
            "gallery-dl", "--dest", str(destination), "--no-mtime",
            "--write-metadata", "--write-info-json", url,
        ]
        return CommandSpec(command, "gallery", clean_name)

    if tool == "curl":
        output = destination / (clean_name or "download")
        command = ["curl", "--location", "--fail", "--continue-at", "-", "--progress-bar", "--output", str(output), url]
        return CommandSpec(command, "curl", output.name)

    raise ValueError(f"Unsupported tool: {tool}")


ARIA_PROGRESS = re.compile(
    r"\((?P<pct>\d+)%\).*?DL:(?P<speed>[^\s\]]+)(?:.*?ETA:(?P<eta>[^\s\]]+))?",
    re.IGNORECASE,
)
YTDLP_PROGRESS = re.compile(
    r"\[download\]\s+(?P<pct>[\d.]+)%.*?of\s+(?:~\s*)?(?P<total>\S+).*?at\s+(?P<speed>\S+).*?ETA\s+(?P<eta>\S+)",
    re.IGNORECASE,
)
CURL_PROGRESS = re.compile(r"(?P<pct>\d+(?:\.\d+)?)%")


def parse_size(value: str | None) -> int:
    if not value or value in {"Unknown", "N/A", "--"}:
        return 0
    match = re.match(r"([\d.]+)\s*([KMGTPE]?i?B)(?:/s)?", value, re.IGNORECASE)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).upper().replace("IB", "B")
    power = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5}.get(unit, 0)
    return int(number * (1024 ** power))


def parse_eta(value: str | None) -> int | None:
    if not value or value in {"Unknown", "--"}:
        return None
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
        return seconds
    except ValueError:
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
        if not match:
            return None
        h, m, s = (int(x or 0) for x in match.groups())
        return h * 3600 + m * 60 + s


def parse_progress(kind: str, line: str) -> dict:
    if kind == "aria2":
        match = ARIA_PROGRESS.search(line)
        if match:
            return {
                "progress": float(match.group("pct")),
                "speed_bytes": parse_size(match.group("speed")),
                "eta_seconds": parse_eta(match.group("eta")),
            }
    elif kind == "ytdlp":
        match = YTDLP_PROGRESS.search(line)
        if match:
            total = parse_size(match.group("total"))
            pct = float(match.group("pct"))
            return {
                "progress": pct,
                "total_bytes": total,
                "downloaded_bytes": int(total * pct / 100),
                "speed_bytes": parse_size(match.group("speed")),
                "eta_seconds": parse_eta(match.group("eta")),
            }
    elif kind == "curl":
        match = CURL_PROGRESS.search(line)
        if match:
            return {"progress": float(match.group("pct"))}
    return {}
