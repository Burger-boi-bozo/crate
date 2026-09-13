from pathlib import Path


def test_footer_has_live_v4_build_label_and_preview_assets():
    html = Path("app/converter_static/index.html").read_text()
    assert 'id="build-version"' in html
    assert "v4.0.0" in html
    assert "enhancements.js" in html
    assert "preview.js" in html
    assert 'id="link-preview"' in html
    assert "status.js" not in html
    assert 'id="server-status"' not in html


def test_admin_panel_is_separate_from_public_app():
    admin = Path("app/converter_static/admin.html").read_text()
    public = Path("app/converter_static/index.html").read_text()
    assert "SERVER OVERVIEW" in admin
    assert "/assets/admin.js" in admin
    assert "SERVER OVERVIEW" not in public
