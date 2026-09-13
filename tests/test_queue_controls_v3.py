import asyncio
import signal

import pytest

from app.job_queue import Queue
from app.models import Config


@pytest.mark.asyncio
async def test_running_pause_and_resume_send_process_group_signals(tmp_path, monkeypatch):
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    class Process:
        pid = 1234
        returncode = None
    process = Process()
    job = {"id": "job", "status": "downloading", "stage": "downloading"}
    queue.processes["job"] = process
    seen = []
    monkeypatch.setattr("os.killpg", lambda pid, sig: seen.append((pid, sig)))
    await queue.pause(job)
    assert job["status"] == "paused" and seen[-1] == (1234, signal.SIGSTOP)
    await queue.resume(job)
    assert job["status"] == "downloading" and seen[-1] == (1234, signal.SIGCONT)
