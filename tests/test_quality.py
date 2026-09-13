import asyncio
import io
import json
import subprocess
from pathlib import Path

import pytest

from app.converter import Config, Queue, Submission
from app.media_runner import convert_file, probe
from app.music_lookup import song_metadata


def test_no_default_download_caps():
    config = Config()
    assert not any((config.max_bytes, config.max_work_bytes, config.max_duration, config.timeout,
                    config.ttl, config.max_queue, config.daily_jobs, config.max_downloads))


@pytest.mark.asyncio
async def test_completed_files_and_pending_jobs_survive_restart(tmp_path, monkeypatch):
    async def wait(self, job):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "run", wait)
    queue = Queue(Config(data_dir=tmp_path))
    ready = queue.submit("owner", Submission(url="https://media.example/a.mp4"))
    pending = queue.submit("owner", Submission(url="https://media.example/b.mp4", quality="2160"))
    folder = queue.folder(ready["id"])
    folder.mkdir()
    path = folder / "download.mp4"
    path.write_bytes(b"retained")
    ready.update(status="ready", path=str(path), filename="sample.mp4")
    await queue.stop()
    restored = Queue(queue.config)
    await restored.start()
    try:
        assert restored.jobs[ready["id"]]["status"] == "ready"
        assert path.read_bytes() == b"retained"
        assert restored.jobs[pending["id"]]["quality"] == "2160"
        assert restored.jobs[pending["id"]]["status"] == "queued"
    finally:
        await restored.stop()


def test_original_codecs_and_selected_quality(tmp_path):
    source = tmp_path / "source.mkv"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=s=2560x1440:r=2:d=0.5", "-f", "lavfi", "-i",
                    "sine=duration=0.5", "-c:v", "libvpx-vp9", "-threads", "1",
                    "-c:a", "libopus", "-shortest", str(source)], check=True)
    for fmt in ("mkv", "mka", "mp4", "mp3"):
        target = tmp_path / ("result." + fmt)
        quality = "720" if fmt == "mp4" else "best"
        convert_file(source, target, fmt, quality=quality)
        streams = probe(target)["streams"]
        audio = next(s for s in streams if s["codec_type"] == "audio")
        if fmt in ("mkv", "mka"):
            assert audio["codec_name"] == "opus"
            if fmt == "mkv":
                video = next(s for s in streams if s["codec_type"] == "video")
                assert video["codec_name"] == "vp9" and video["height"] == 1440
        elif fmt == "mp4":
            video = next(s for s in streams if s["codec_type"] == "video")
            assert video["height"] == 720
        else:
            assert audio["codec_name"] == "mp3" and int(audio["bit_rate"]) == 320000


def test_music_metadata_never_uses_preview():
    class Downloader:
        def urlopen(self, url):
            if "spotify" in url:
                entity = {"title": "Sample Song", "artists": [{"name": "Sample Artist"}],
                          "audioPreview": {"url": "https://example.com/preview.mp3"}}
                data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
                return io.BytesIO(('<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>").encode())
            return io.BytesIO(json.dumps({"results": [{"trackId": 456, "trackName": "Sample Song",
                "artistName": "Sample Artist", "previewUrl": "https://example.com/preview.mp3"}]}).encode())
    for url in ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
                "https://music.apple.com/us/album/sample/123?i=456"):
        result = song_metadata(url, Downloader())
        assert result["title"] == "Sample Song" and result["artist"] == "Sample Artist"
        assert "preview" not in json.dumps(result).lower()
    with pytest.raises(ValueError):
        song_metadata("https://open.spotify.com/playlist/example", Downloader())
    with pytest.raises(ValueError):
        song_metadata("https://127.0.0.1/track/example", Downloader())
