from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.gpu_worker_client import eligible
from app.models import Config
from gpu_worker import server


def test_v61_gpu_worker_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CRATE_GPU_WORKER_URL", "http://10.0.0.40:19081/")
    monkeypatch.setenv("CRATE_GPU_WORKER_TOKEN", "secret")
    monkeypatch.setenv("CRATE_GPU_WORKER_TIMEOUT", "900")
    config = Config(data_dir=tmp_path)
    assert config.gpu_worker_url == "http://10.0.0.40:19081"
    assert config.gpu_worker_token == "secret"
    assert config.gpu_worker_timeout == 900


def test_v61_gpu_worker_token_file(monkeypatch, tmp_path):
    token_file = tmp_path / "gpu-token"
    token_file.write_text("file-secret\n")
    monkeypatch.delenv("CRATE_GPU_WORKER_TOKEN", raising=False)
    monkeypatch.setenv("CRATE_GPU_WORKER_TOKEN_FILE", str(token_file))
    config = Config(data_dir=tmp_path / "data")
    assert config.gpu_worker_token == "file-secret"


def test_only_gpu_valuable_jobs_are_eligible():
    base = {"format": "mp4", "options": {"hardware": "auto", "video_codec": "h264"}}
    assert eligible(base)
    assert eligible({"format": "mkv", "options": {"hardware": "auto", "video_codec": "hevc"}})
    assert not eligible({"format": "webm", "options": {"hardware": "auto", "video_codec": "vp9"}})
    assert not eligible({"format": "mp4", "options": {"hardware": "off", "video_codec": "h264"}})
    assert not eligible({"format": "mp4", "options": {"hardware": "auto", "video_codec": "h264", "thumbnail": True}})


def test_gpu_worker_requires_private_token(monkeypatch):
    monkeypatch.setattr(server, "TOKEN", "worker-secret")
    monkeypatch.setattr(server, "ALLOWED_IP", "")
    monkeypatch.setattr(server, "_caps", lambda: {
        "available": True, "worker": "rx6700xt", "video_codecs": ["h264", "hevc"], "encoders": {}
    })
    client = TestClient(server.app)
    assert client.get("/health").status_code == 401
    response = client.get("/health", headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 200
    assert response.json()["worker"] == "rx6700xt"


def test_gpu_worker_rejects_unsupported_container(monkeypatch):
    monkeypatch.setattr(server, "TOKEN", "worker-secret")
    monkeypatch.setattr(server, "ALLOWED_IP", "")
    monkeypatch.setattr(server, "_caps", lambda: {
        "available": True, "worker": "rx6700xt", "video_codecs": ["h264", "hevc"], "encoders": {}
    })
    client = TestClient(server.app)
    response = client.post("/jobs", headers={"Authorization": "Bearer worker-secret"},
        data={"format": "webm", "options": '{"format":"webm","video_codec":"h264"}', "metadata": "{}"},
        files={"source": ("clip.mp4", b"not-media", "video/mp4")})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_controller_uses_available_gpu_worker(monkeypatch, tmp_path):
    from app import job_worker

    async def fake_health(config):
        return {"available": True, "worker": "rx6700xt", "video_codecs": ["h264", "hevc"]}

    async def fake_transcode(config, queue, job, source, target, metadata):
        target.write_bytes(b"gpu-output")
        return {"video_encoder": "h264_vaapi", "width": 1280, "height": 720,
                "source_duration": 5.0, "output_duration": 5.0, "gpu_worker": "rx6700xt"}

    monkeypatch.setattr(job_worker, "gpu_health", fake_health)
    monkeypatch.setattr(job_worker, "gpu_transcode", fake_transcode)
    config = SimpleNamespace(gpu_worker_url="http://worker", gpu_worker_token="secret")
    queue = SimpleNamespace(config=config, event=lambda *args, **kwargs: None)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    job = {"id": "j1", "format": "mp4", "options": {"hardware": "auto", "video_codec": "h264"}}
    result = await job_worker.gpu_postprocess(
        queue, job, tmp_path, source, {"title": "Clip", "source": "upload"}, {"title": "Clip", "creator": "Me"}, None
    )
    assert result is not None
    assert result["video_encoder"] == "h264_vaapi"
    assert result["gpu_worker"] == "rx6700xt"
    assert result["file"] == "final.mp4"
    assert len(result["checksum"]) == 64


@pytest.mark.asyncio
async def test_controller_falls_back_when_gpu_is_offline(monkeypatch, tmp_path):
    from app import job_worker

    async def fake_health(config):
        return {"available": False, "configured": True}

    monkeypatch.setattr(job_worker, "gpu_health", fake_health)
    queue = SimpleNamespace(config=SimpleNamespace(gpu_worker_url="http://worker", gpu_worker_token="secret"),
                            event=lambda *args, **kwargs: None)
    source = tmp_path / "source.mp4"; source.write_bytes(b"source")
    job = {"id": "j1", "format": "mp4", "options": {"hardware": "auto", "video_codec": "h264"}}
    assert await job_worker.gpu_postprocess(queue, job, tmp_path, source, {}, {}, None) is None

def test_gpu_client_uses_shared_progress_path():
    source = __import__('pathlib').Path('app/gpu_worker_client.py').read_text()
    assert 'apply_progress(job,' in source
    assert 'queue.update_progress' not in source

@pytest.mark.asyncio
async def test_controller_skips_incompatible_gpu_worker(monkeypatch, tmp_path):
    from app import job_worker

    async def fake_health(config):
        return {"available": True, "compatible": False, "version": "6.0.0",
                "worker": "rx6700xt", "video_codecs": ["h264", "hevc"]}

    monkeypatch.setattr(job_worker, "gpu_health", fake_health)
    queue = SimpleNamespace(config=SimpleNamespace(gpu_worker_url="http://worker", gpu_worker_token="secret"),
                            event=lambda *args, **kwargs: None)
    source = tmp_path / "source.mp4"; source.write_bytes(b"source")
    job = {"id": "j1", "format": "mp4", "options": {"hardware": "auto", "video_codec": "h264"}}
    assert await job_worker.gpu_postprocess(queue, job, tmp_path, source, {}, {}, None) is None


def test_worker_cleans_stale_directories(monkeypatch, tmp_path):
    stale = tmp_path / "old-job"; stale.mkdir()
    current = tmp_path / "current-job"; current.mkdir()
    import os, time
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "jobs", {"current-job": {"status": "converting"}})
    server._cleanup_stale(max_age=3600)
    assert not stale.exists()
    assert current.exists()
