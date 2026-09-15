from pathlib import Path


def test_updater_verifies_exact_v6_build_and_blue_green_gate():
    script = Path("scripts/proxmox-update-guest.sh").read_text()
    assert "health['runtime'] == 'crate-v6'" in script
    assert "health['build'] == revision[:12]" in script
    assert "/opt/crate-releases" in script
    assert "PORT=18082" in script
    assert "OnUnitActiveSec=2min" in script


def test_auto_updater_still_requires_successful_push_workflow():
    script = Path("scripts/proxmox-auto-update.py").read_text()
    assert 'run.get("event") == "push"' in script
    assert 'run.get("conclusion") == "success"' in script
    assert 'run.get("head_sha") == revision' in script


def test_admin_password_defaults_on_install_and_updates_preserve_environment():
    install = Path("scripts/proxmox-guest.sh").read_text()
    update = Path("scripts/proxmox-update-guest.sh").read_text()
    assert 'crate_admin_password="password"' in install
    assert '.v51-admin-default-applied' in install
    assert 'CRATE_ADMIN_PASSWORD' not in update
