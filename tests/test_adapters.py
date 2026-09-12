from pathlib import Path

from app.adapters import build_command, detect_tool, parse_eta, parse_progress, parse_size, safe_segment


def test_tool_detection():
    assert detect_tool("https://youtube.com/watch?v=abc") == "yt-dlp"
    assert detect_tool("https://imgur.com/gallery/abc") == "gallery-dl"
    assert detect_tool("magnet:?xt=urn:btih:abc") == "aria2"
    assert detect_tool("https://example.com/archive.zip") == "aria2"


def test_safe_segment_prevents_path_traversal():
    value = safe_segment("../../Movies: 2026")
    assert "/" not in value
    assert ".." not in value
    assert value == "Movies- 2026"


def test_size_and_eta_parsers():
    assert parse_size("1.5MiB") == 1_572_864
    assert parse_size("2 MB/s") == 2_097_152
    assert parse_eta("01:02") == 62
    assert parse_eta("1h2m3s") == 3723


def test_ytdlp_progress_parser():
    result = parse_progress("ytdlp", "[download]  50.0% of 10.00MiB at 2.00MiB/s ETA 00:03")
    assert result["progress"] == 50
    assert result["total_bytes"] == 10 * 1024 * 1024
    assert result["eta_seconds"] == 3


def test_commands_do_not_use_shell(tmp_path: Path):
    spec = build_command("aria2", "https://example.com/a.zip", tmp_path, "archive.zip", {})
    assert spec.command[0] == "aria2c"
    assert "--out" in spec.command
    assert spec.command[-1] == "https://example.com/a.zip"

