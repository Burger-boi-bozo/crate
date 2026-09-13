from fastapi.testclient import TestClient

from app.models import Config
from app.runtime import create_app


def test_server_telemetry_is_not_public(tmp_path):
    app = create_app(Config(data_dir=tmp_path, secret="test", admin_password="operator",
                            secure_cookie=False, workers=2))
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/session")
        assert client.get("/api/status").status_code == 404
        assert client.get("/api/admin/status").status_code == 401
        client.post("/api/admin/login", json={"password": "operator"}, headers={"X-Crate-Request": "1"})
        data = client.get("/api/admin/status").json()
        assert {"status", "uptime_seconds", "workers", "disk", "memory", "active", "counts", "version", "build", "label"} <= set(data)
        text = str(data).lower()
        assert "secret" not in text and "token" not in text and "cookie" not in text
