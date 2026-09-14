from pathlib import Path

from app.models import Config


def test_default_retention_is_24_hours(tmp_path, monkeypatch):
    monkeypatch.delenv("CRATE_TTL", raising=False)
    assert Config(data_dir=tmp_path).ttl == 86400


def test_proxmox_enforces_24_hour_retention():
    bootstrap = Path("scripts/proxmox-guest.sh").read_text()
    updater = Path("scripts/proxmox-update-guest.sh").read_text()
    assert "CRATE_TTL=86400" in bootstrap
    assert "CRATE_TTL=86400" in updater


def test_batch_link_arrow_has_reserved_space():
    css = Path("app/converter_static/enhancements.css").read_text()
    assert ".batch-url-field>span" in css
    assert "padding: .85rem .85rem .85rem 3rem" in css
