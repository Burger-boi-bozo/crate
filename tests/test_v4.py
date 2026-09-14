import asyncio
import json

from fastapi.testclient import TestClient

from app.job_queue import Queue
from app.models import Config, Submission
from app.runtime import create_app

HEADERS = {"X-Crate-Request": "1"}


def test_admin_auth_is_separate_and_http_only(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)
    app = create_app(Config(data_dir=tmp_path, secret="session-secret", admin_password="operator",
                            secure_cookie=False, workers=2))
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/status").status_code == 404
        assert client.get("/api/admin/status").status_code == 401
        assert client.post("/api/admin/login", json={"password": "wrong"}, headers=HEADERS).status_code == 401
        response = client.post("/api/admin/login", json={"password": "operator"}, headers=HEADERS)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "crate_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        status = client.get("/api/admin/status")
        assert status.status_code == 200
        assert status.json()["version"] == "5.1.0"


def test_admin_can_inspect_jobs_without_owner_secret(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)
    app = create_app(Config(data_dir=tmp_path, secret="session-secret", admin_password="operator",
                            secure_cookie=False, workers=1))
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        created = client.post("/api/jobs", json={"url": "https://example.com/media.mp4"}, headers=HEADERS)
        assert created.status_code == 202
        client.post("/api/admin/login", json={"password": "operator"}, headers=HEADERS)
        jobs = client.get("/api/admin/jobs").json()
        assert jobs[0]["url"] == "https://example.com/media.mp4"
        assert jobs[0]["source_host"] == "example.com"
        assert "owner" not in jobs[0] and "path" not in jobs[0]


def test_preview_endpoint_caches_metadata(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)
    calls = []

    class Process:
        returncode = 0
        async def communicate(self):
            payload = {"ok": True, "url": "https://youtu.be/example", "title": "Example video",
                       "creator": "Creator", "duration": 61, "source": "Youtube",
                       "thumbnail": "data:image/jpeg;base64,AA=="}
            return json.dumps(payload).encode(), b""

    async def spawn(*args, **kwargs):
        calls.append(args)
        return Process()

    monkeypatch.setattr("app.api_preview.asyncio.create_subprocess_exec", spawn)
    app = create_app(Config(data_dir=tmp_path, secret="preview", secure_cookie=False, workers=1))
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        body = {"url": "https://youtu.be/example"}
        first = client.post("/api/preview", json=body, headers=HEADERS)
        second = client.post("/api/preview", json=body, headers=HEADERS)
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["title"] == "Example video"
        assert first.json()["cached"] is False and second.json()["cached"] is True
        assert len(calls) == 1


def test_sqlite_wal_state_and_events_survive_new_queue(tmp_path):
    config = Config(data_dir=tmp_path, secret="test", workers=1)
    queue = Queue(config)
    job = queue.submit("owner", Submission(url="https://example.com/a.mp4"))
    queue.save()
    assert (tmp_path / "crate.db").is_file()
    assert queue.events("owner")[-1]["kind"] == "queued"
    restored = Queue(config)
    restored.store.initialize()
    restored.jobs = restored.store.load_jobs()
    assert restored.jobs[job["id"]]["url"] == job["url"]
    assert restored.events("owner")[-1]["message"] == "Added to queue"
