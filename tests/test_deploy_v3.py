from pathlib import Path


def test_updater_verifies_exact_v4_build_and_uses_runtime_requirements():
    script = Path("scripts/proxmox-update-guest.sh").read_text()
    assert 'health["runtime"] == "crate-v4"' in script
    assert 'health["build"] == revision[:12]' in script
    assert "pip install -r requirements.txt" in script
    assert "OnUnitActiveSec=2min" in script
    assert "CRATE_ADMIN_PASSWORD" in script


def test_auto_updater_still_requires_successful_push_workflow():
    script = Path("scripts/proxmox-auto-update.py").read_text()
    assert 'run.get("event") == "push"' in script
    assert 'run.get("conclusion") == "success"' in script
    assert 'run.get("head_sha") == revision' in script
