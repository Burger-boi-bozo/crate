from pathlib import Path


def test_runtime_check_targets_unified_app():
    text = Path("scripts/check_runtime.py").read_text()
    assert "from app.runtime import app" in text
    assert "crate-v3" not in text or "RUNTIME_VERSION" in text
