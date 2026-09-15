from pathlib import Path

from app.models import Config


def test_default_retention_is_24_hours(tmp_path, monkeypatch):
    monkeypatch.delenv("CRATE_TTL", raising=False)
    assert Config(data_dir=tmp_path).ttl == 86400


def test_proxmox_enforces_24_hour_retention():
    bootstrap = Path("scripts/proxmox-guest.sh").read_text()
    updater = Path("scripts/proxmox-update-guest.sh").read_text()
    assert "CRATE_TTL=86400" in bootstrap
    assert "set_env CRATE_TTL 86400" in updater


def test_batch_link_arrow_has_reserved_space():
    css = Path("app/converter_static/enhancements.css").read_text()
    assert ".batch-url-field>span" in css
    assert "padding: .85rem .85rem .85rem 3rem" in css
    assert "top: .88rem" in css
    assert "font-size: 1.05rem" in css


def test_v51_ui_has_cache_bust_and_visible_changelog():
    html = Path("app/converter_static/index.html").read_text()
    assert "enhancements.css?v=60" in html
    assert 'id="changelog-heading">Change log<' in html
    assert "v5.1.0" in html
    assert "24 hours" in html
