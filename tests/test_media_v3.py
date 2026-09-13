import os
import subprocess
from pathlib import Path

from app.media_convert import build_args, convert_file, probe


def test_ffmpeg_threads_default_to_auto(tmp_path, monkeypatch):
    monkeypatch.delenv("CRATE_FFMPEG_THREADS", raising=False)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"x")
    details = {"streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720}]}
    args = build_args(source, tmp_path / "out.mp4", "mp4", "best", details)
    assert args[args.index("-threads") + 1] == "0"


def test_conversion_emits_progress(tmp_path):
    source = tmp_path / "sample.mp4"
    target = tmp_path / "result.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:a", "aac", str(source)], check=True)
    events = []
    convert_file(source, target, "mp3", lambda kind, **values: events.append({"kind": kind, **values}),
                 max_duration=60, max_bytes=10 * 1024 * 1024)
    assert target.is_file() and target.stat().st_size > 0
    assert any(event.get("stage") == "converting" for event in events)
    assert any((event.get("conversion_progress") or 0) >= 0 for event in events)
    assert any(stream.get("codec_name") == "mp3" for stream in probe(target)["streams"])


def test_fragment_concurrency_is_configurable(monkeypatch):
    monkeypatch.setenv("CRATE_FRAGMENT_CONCURRENCY", "6")
    from app.media_runner_v3 import env_int
    assert env_int("CRATE_FRAGMENT_CONCURRENCY", 4, 1, 16) == 6
