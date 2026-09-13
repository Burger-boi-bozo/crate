from pathlib import Path


def test_proxmox_updater_injects_build_sha():
    script = Path("scripts/proxmox-update-guest.sh").read_text()
    assert "CRATE_BUILD_SHA=" in script
    assert 'set_build_ref "$ref"' in script
    assert 'set_build_ref "$old_ref"' in script
