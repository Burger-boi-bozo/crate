from app.job_queue import Queue
from app.models import Config, Submission


def test_job_schema_contains_progress_and_diagnostics(tmp_path):
    queue = Queue(Config(data_dir=tmp_path, secret="test", workers=1))
    job = queue.submit("owner", Submission(url="https://example.com/media.mp4"))
    public = queue.public(job)
    for key in ("stage", "progress", "downloaded_bytes", "total_bytes", "speed", "eta",
                "conversion_progress", "error_code", "diagnostic", "queue_position"):
        assert key in public
