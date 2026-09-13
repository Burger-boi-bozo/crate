from fastapi.testclient import TestClient

from app.models import Config
from app.runtime import create_app


def test_status_exposes_only_operational_metrics(tmp_path):
    app = create_app(Config(data_dir=tmp_path, secret="test", secure_cookie=False, workers=2))
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        data = client.get("/api/status").json()
        assert {"status", "uptime_seconds", "workers", "disk_free", "disk_total", "active", "counts", "version", "build", "label"} <= set(data)
        text = str(data).lower()
        assert "secret" not in text and "token" not in text and "cookie" not in text
