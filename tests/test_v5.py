import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api_admin import metrics, provider_summary
from app.job_queue import Queue
from app.models import BatchSubmission, Config, Submission
from app.preview_lookup import source_profile
from app.runtime import create_app

HEADERS = {"X-Crate-Request": "1"}

def test_v5_batch_model_deduplicates_and_validates():
    batch = BatchSubmission(
        urls=["https://example.com/a", "https://example.com/a", "https://example.com/b"],
        format="mp4", quality="720",
    )
    items = batch.submissions(20)
    assert [item.url for item in items] == ["https://example.com/a", "https://example.com/b"]
    assert all(item.quality == "720" for item in items)


def test_v5_scheduler_classifies_heavy_and_light(tmp_path):
    queue = Queue(Config(data_dir=tmp_path, workers=2, heavy_workers=1))
    assert queue.workload_for(Submission(url="https://example.com/a", format="mkv", quality="best")) == "light"
    assert queue.workload_for(Submission(url="https://example.com/a", format="mp4", quality="1080")) == "heavy"
    assert queue.workload_for(Submission(url="https://example.com/a", format="mp3", quality="320")) == "heavy"

def test_v5_preview_recommendation_and_estimates():
    profile = source_profile({
        "duration": 60,
        "formats": [
            {"height": 1080, "vcodec": "avc1", "acodec": "none", "tbr": 4000},
            {"height": 720, "vcodec": "avc1", "acodec": "none", "tbr": 2200},
            {"vcodec": "none", "acodec": "mp4a", "abr": 128, "tbr": 128},
        ],
    })
    assert profile["max_height"] == 1080
    assert profile["recommendation"]["format"] == "mp4"
    assert profile["recommendation"]["quality"] == "1080"
    assert profile["recommendation"]["estimated_size"] > 0
    assert profile["estimates"]["mp3_320"] == 2_400_000

def test_v5_provider_health_and_metrics():
    now = time.time()
    jobs = []
    jobs.append({"url": "https://video.example/a", "status": "ready", "created_at": now - 20, "finished_at": now - 10, "size": 1000})
    jobs.append({"url": "https://video.example/b", "status": "ready", "created_at": now - 40, "finished_at": now - 30, "size": 2000})
    jobs.append({"url": "https://limited.example/a", "status": "failed", "error_code": "host_blocked", "created_at": now - 20, "finished_at": now - 10})
    jobs.append({"url": "https://limited.example/b", "status": "failed", "error_code": "host_blocked", "created_at": now - 30, "finished_at": now - 20})
    providers = {row["host"]: row for row in provider_summary(jobs, now)}
    assert providers["video.example"]["state"] == "healthy"
    assert providers["limited.example"]["state"] == "blocked"
    data = metrics(jobs, now)
    assert data["completed"] == 2 and data["failed"] == 2
    assert data["output_bytes"] == 3000

def test_v5_batch_api_and_maintenance(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)
    config = Config(data_dir=tmp_path, secret="test-secret", admin_password="operator-test", secure_cookie=False, workers=2)
    app = create_app(config)
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        response = client.post("/api/batches", json={
            "urls": ["https://example.com/a", "https://example.com/b"], "format": "mp4", "quality": "720"
        }, headers=HEADERS)
        assert response.status_code == 202
        batch = response.json()
        assert len(batch["jobs"]) == 2
        status = client.get(f"/api/batches/{batch['id']}").json()
        assert status["total"] == 2 and status["active"] == 2

def test_v5_static_ui_wires_sse_and_batch():
    app_js = Path("app/converter_static/app.js").read_text()
    html = Path("app/converter_static/index.html").read_text()
    assert "EventSource('/api/events/stream')" in app_js
    assert "'/api/batches'" in app_js
    assert "one per line" in html
