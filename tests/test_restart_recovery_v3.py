import asyncio
import json

import pytest

from app.job_queue import Queue
from app.models import Config


@pytest.mark.asyncio
async def test_interrupted_jobs_requeue_and_partial_files_are_removed(tmp_path, monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)
    job_id = "a" * 32
    folder = tmp_path / ("job-" + job_id)
    folder.mkdir()
    (folder / "source.mp4.part").write_bytes(b"partial")
    state = {job_id: {"id": job_id, "owner": "owner", "url": "https://example.com/test.mp4",
                      "format": "mp4", "quality": "best", "status": "downloading",
                      "created_at": 1, "expires_at": None}}
    (tmp_path / "jobs.json").write_text(json.dumps(state))
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    await queue.start()
    try:
        job = queue.jobs[job_id]
        assert job["status"] == "queued"
        assert job["progress"] == 0
        assert not folder.exists()
    finally:
        await queue.stop()


def test_state_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    queue.jobs["example"] = {"id": "example", "status": "failed", "expires_at": None, "created_at": 1}
    queue.save()
    assert json.loads((tmp_path / "jobs.json").read_text())["example"]["status"] == "failed"
    assert not (tmp_path / "jobs.json.tmp").exists()
