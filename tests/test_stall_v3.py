import asyncio

import pytest

from app.errors import JobError
from app.job_worker import read_events
from app.models import Config


class NeverOutput:
    def __init__(self):
        self.stdout = self
        self.returncode = None
    async def readline(self):
        await asyncio.sleep(60)
        return b""
    async def wait(self):
        return 0


class QueueStub:
    def __init__(self, tmp_path):
        self.config = Config(data_dir=tmp_path, secret="test", stall_timeout=30)
    def folder(self, job_id):
        path = self.config.data_dir / ("job-" + job_id)
        path.mkdir(exist_ok=True)
        return path


@pytest.mark.asyncio
async def test_paused_job_does_not_trip_stall_timeout(tmp_path, monkeypatch):
    queue = QueueStub(tmp_path)
    queue.config.stall_timeout = 1
    job = {"id": "x", "status": "paused"}
    process = NeverOutput()
    task = asyncio.create_task(read_events(queue, job, process, asyncio.get_running_loop().time()))
    await asyncio.sleep(1.2)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
