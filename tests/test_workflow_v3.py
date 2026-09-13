from pathlib import Path


def test_ci_tests_exact_runtime_without_container_publish():
    workflow = Path(".github/workflows/publish.yml").read_text()
    assert "python -m pytest -q" in workflow
    assert "npm run test:ui" in workflow
    assert "python scripts/check_runtime.py" in workflow
    assert "docker/build-push-action" not in workflow
    assert "wrangler" not in workflow
