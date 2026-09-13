import asyncio

import pytest
from fastapi.testclient import TestClient

from app.models import Config, Submission
from app.runtime import create_app
from app.version import version_payload


def test_version_uses_deployed_sha(monkeypatch):
    monkeypatch.setenv("CRATE_BUILD_SHA", "abcdef1234567890")
    assert version_payload()["label"] == "v3.0.0 · abcdef123456"


def test_status_reports_disk_workers_and_version(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr("app.job_queue.Queue.work", idle)
    app = create_app(Config(data_dir=tmp_path, secret="test", secure_cookie=False, workers=2))
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        status = client.get("/api/status").json()
        assert status["status"] == "ok"
        assert status["workers"] == 2
        assert status["disk_free"] > 0
        assert "version" in status and "build" in status


@pytest.mark.asyncio
async def test_pause_resume_and_queue_position(tmp_path):
    from app.job_queue import Queue
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    first = queue.submit("owner", Submission(url="https://example.com/a.mp4"))
    second = queue.submit("owner", Submission(url="https://example.com/b.mp4"))
    assert queue.public(first)["queue_position"] == 1
    assert queue.public(second)["queue_position"] == 2
    await queue.pause(first)
    assert first["status"] == "paused"
    assert queue.public(second)["queue_position"] == 1
    await queue.resume(first)
    assert first["status"] == "queued"


def test_retry_creates_new_job(tmp_path):
    from app.job_queue import Queue
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    old = queue.submit("owner", Submission(url="https://example.com/a.mp4"))
    old["status"] = "failed"
    new = queue.retry("owner", old)
    assert new["id"] != old["id"]
    assert new["url"] == old["url"]
    assert new["status"] == "queued"
