import asyncio

import pytest

from app import rebuild
from app.converter import Config


@pytest.mark.asyncio
async def test_rebuild_starts_two_workers_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CRATE_WORKERS", raising=False)
    queue = rebuild.ParallelQueue(Config(data_dir=tmp_path))
    await queue.start()
    try:
        worker_tasks = [task for task in queue.tasks if task.get_name().startswith("crate-worker-")]
        assert len(worker_tasks) == 2
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_rebuild_recovers_bad_state_file(tmp_path):
    (tmp_path / "jobs.json").write_text("not valid json")
    queue = rebuild.ParallelQueue(Config(data_dir=tmp_path))
    await queue.start()
    try:
        assert queue.jobs == {}
    finally:
        await queue.stop()


def test_version_payload_uses_deployed_revision(monkeypatch):
    monkeypatch.setenv("CRATE_BUILD_SHA", "1234567890abcdef")
    payload = rebuild.version_payload()
    assert payload == {
        "version": "2.0.0",
        "build": "1234567890ab",
        "label": "v2.0.0 · 1234567890ab",
    }
