import os
from pathlib import Path

os.environ["UDM_DATA_DIR"] = "/tmp/crate-test-data"
os.environ["UDM_DOWNLOAD_DIR"] = "/tmp/crate-test-downloads"
os.environ["UDM_POLL_INTERVAL"] = "10"

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_download_lifecycle(tmp_path: Path):
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        created = client.post(
            "/api/downloads",
            json={
                "url": "https://example.com/file.zip",
                "tool": "auto",
                "category": "Archives",
                "priority": 10,
            },
        )
        assert created.status_code == 201
        item = created.json()
        assert item["status"] == "queued"
        assert item["category"] == "Archives"

        response = client.get(f"/api/downloads/{item['id']}")
        assert response.status_code == 200
        assert response.json()["url"] == "https://example.com/file.zip"

        priority = client.patch(f"/api/downloads/{item['id']}/priority", json={"priority": -5})
        assert priority.status_code == 200

        deleted = client.delete(f"/api/downloads/{item['id']}")
        assert deleted.status_code == 204


def test_rejects_unsupported_scheme():
    with TestClient(app) as client:
        response = client.post("/api/downloads", json={"url": "file:///etc/passwd"})
        assert response.status_code == 422
