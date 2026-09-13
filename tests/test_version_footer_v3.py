from pathlib import Path


def test_footer_has_live_build_label_and_enhancement_assets():
    html = Path("app/converter_static/index.html").read_text()
    assert 'id="build-version"' in html
    assert "v3.0.0" in html
    assert "enhancements.js" in html
    assert "status.js" in html
    assert 'id="server-status"' in html
