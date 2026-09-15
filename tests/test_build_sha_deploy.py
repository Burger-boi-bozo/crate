from pathlib import Path


def test_proxmox_updater_injects_and_rolls_back_build_sha():
    script = Path('scripts/proxmox-update-guest.sh').read_text()
    assert 'set_env CRATE_BUILD_SHA' in script
    assert 'old_ref' in script
    assert "health['build'] == revision[:12]" in script
