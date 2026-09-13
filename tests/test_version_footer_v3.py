from pathlib import Path


def test_footer_and_live_assets_match_v4():
    html = Path("app/converter_static/index.html").read_text()
    assert 'id="build-version"' in html
    assert "v4.0.0" in html
    assert "enhancements.js" in html
    assert "preview.js" in html
    assert "live.js" in html
    assert 'id="live-connection"' in html
    assert 'id="server-status"' not in html
