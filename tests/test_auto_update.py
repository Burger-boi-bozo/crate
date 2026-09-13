import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "auto_update", Path(__file__).parents[1] / "scripts/proxmox-auto-update.py")
auto_update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto_update)


def test_only_successful_push_workflow_approves_exact_revision():
    revision = "a" * 40
    base = {"head_sha": revision, "path": ".github/workflows/publish.yml",
            "event": "push", "status": "completed", "conclusion": "success"}
    assert auto_update.approved([base], revision)
    for key, value in {
        "head_sha": "b" * 40,
        "path": ".github/workflows/other.yml",
        "event": "pull_request",
        "status": "in_progress",
        "conclusion": "failure",
    }.items():
        assert not auto_update.approved([{**base, key: value}], revision)
