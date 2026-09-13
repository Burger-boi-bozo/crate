import shutil
from types import SimpleNamespace

import pytest

from app.api_admin import public_job
from app.job_queue import Queue
from app.models import Config, Submission


def ready_queue(tmp_path, **config):
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1, **config))
    queue.store.initialize()
    return queue


def test_submit_records_event_and_broadcasts_live_packet(tmp_path):
    queue = ready_queue(tmp_path)
    channel = queue.subscribe("owner")
    job = queue.submit("owner", Submission(url="https://example.com/media.mp4"))
    packet = channel.get_nowait()
    assert packet["type"] == "job"
    assert packet["job"]["id"] == job["id"]
    assert packet["event"]["kind"] == "queued"
    events = queue.events("owner", job["id"])
    assert [event["kind"] for event in events] == ["queued"]
    assert queue.events("other-owner", job["id"]) == []


def test_duplicate_active_submission_returns_same_job(tmp_path):
    queue = ready_queue(tmp_path)
    body = Submission(url="https://example.com/media.mp4", format="mp4", quality="best")
    first = queue.submit("owner", body)
    second = queue.submit("owner", body)
    assert second["id"] == first["id"]
    assert len(queue.jobs) == 1


def test_storage_reserve_blocks_new_job(tmp_path, monkeypatch):
    queue = ready_queue(tmp_path, min_free_bytes=1024)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(total=4096, used=4000, free=96))
    with pytest.raises(Exception) as raised:
        queue.submit("owner", Submission(url="https://example.com/media.mp4"))
    assert getattr(raised.value, "status_code", None) == 503


def test_admin_job_view_hides_submitted_url_and_owner(tmp_path):
    queue = ready_queue(tmp_path)
    job = queue.submit("owner-secret", Submission(url="https://example.com/private-path/video.mp4"))
    result = public_job(queue, job)
    assert "url" not in result
    assert "owner" not in result
    assert result["source_host"] == "example.com"


def test_delete_removes_job_and_persisted_events(tmp_path):
    queue = ready_queue(tmp_path)
    job = queue.submit("owner", Submission(url="https://example.com/media.mp4"))
    assert queue.events("owner", job["id"])
    queue.delete(job["id"])
    assert job["id"] not in queue.jobs
    assert queue.events("owner", job["id"]) == []
