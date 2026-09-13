from fastapi.testclient import TestClient

from app.models import Config
from app.runtime import create_app


def test_server_metrics_require_admin_auth_and_hide_secrets(tmp_path):
    app = create_app(Config(data_dir=tmp_path, secret="test", admin_password="admin-test",
                            secure_cookie=False, workers=2))
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/status").status_code == 404
        assert client.get("/api/admin/status").status_code == 401
        login = client.post("/api/admin/login", json={"password": "admin-test"},
                            headers={"X-Crate-Request": "1"})
        assert login.status_code == 200
        data = client.get("/api/admin/status").json()
        assert {"status", "uptime_seconds", "workers", "disk", "memory", "active", "counts", "version", "build", "label"} <= set(data)
        text = str(data).lower()
        assert "admin-test" not in text and "session-secret" not in text
