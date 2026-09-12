import os
from dataclasses import replace
from pathlib import Path

import pytest

os.environ["UDM_DATA_DIR"] = "/tmp/crate-test-data"
os.environ["UDM_DOWNLOAD_DIR"] = "/tmp/crate-test-downloads"
os.environ["UDM_POLL_INTERVAL"] = "10"
os.environ["UDM_INTERNAL_TOKEN"] = "test-internal-token"

from fastapi.testclient import TestClient

from app.main import app
import app.main as main
from app.database import Database
from app.manager import DownloadManager


@pytest.fixture(autouse=True)
def isolated_manager(tmp_path, monkeypatch):
    # Each TestClient opens its own event loop. Never carry asyncio locks or
    # scheduler events from the previous client's loop into another test.
    settings = replace(main.settings, data_dir=tmp_path / "data", download_dir=tmp_path / "downloads")
    database = Database(settings.database_path)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "database", database)
    monkeypatch.setattr(main, "manager", DownloadManager(database, settings))


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


def test_internal_database_snapshot_is_guarded_and_restorable():
    with TestClient(app) as client:
        hidden = client.get("/api/internal/state")
        assert hidden.status_code == 404

        headers = {"x-crate-internal": "test-internal-token"}
        snapshot = client.get("/api/internal/state", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.content.startswith(b"SQLite format 3\x00")

        restored = client.put(
            "/api/internal/state",
            content=snapshot.content,
            headers={**headers, "content-type": "application/vnd.sqlite3"},
        )
        assert restored.status_code == 200
        assert restored.json() == {"ok": True}

        invalid = client.put(
            "/api/internal/state",
            content=b"not a database",
            headers={**headers, "content-type": "application/vnd.sqlite3"},
        )
        assert invalid.status_code == 400
