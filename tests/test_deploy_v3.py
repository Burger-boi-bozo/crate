from pathlib import Path


def test_updater_verifies_exact_v5_build_and_provisions_admin_password():
    script = Path("scripts/proxmox-update-guest.sh").read_text()
    assert 'health["runtime"] == "crate-v5"' in script
    assert 'health["build"] == revision[:12]' in script
    assert "pip install -r requirements.txt" in script
    assert "OnUnitActiveSec=2min" in script
    assert "CRATE_ADMIN_PASSWORD=" in script
    assert "/etc/crate/admin-password" in script
    assert "chmod 600 /etc/crate/admin-password" in script


def test_auto_updater_still_requires_successful_push_workflow():
    script = Path("scripts/proxmox-auto-update.py").read_text()
    assert 'run.get("event") == "push"' in script
    assert 'run.get("conclusion") == "success"' in script
    assert 'run.get("head_sha") == revision' in script


def test_v51_defaults_admin_password_once_and_preserves_future_changes():
    install = Path("scripts/proxmox-guest.sh").read_text()
    update = Path("scripts/proxmox-update-guest.sh").read_text()
    assert 'crate_admin_password="password"' in install
    assert '.v51-admin-default-applied' in install
    assert 'password="password"' in update
    assert '[[ ! -f "$marker" ]]' in update
    assert 'else\n    password="$(sed -n' in update
