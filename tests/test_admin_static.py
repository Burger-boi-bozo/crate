from pathlib import Path


def test_public_page_has_no_admin_or_server_telemetry_controls():
    public = Path("app/converter_static/index.html").read_text()
    assert "server-status" not in public
    assert "/api/admin/" not in public
    assert "Admin panel" not in public


def test_admin_page_has_operator_assets():
    admin = Path("app/converter_static/admin.html").read_text()
    assert "OPERATOR ACCESS" in admin
    assert "/assets/admin.js?v=4" in admin
    assert "/assets/admin.css?v=4" in admin
