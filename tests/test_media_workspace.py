"""Exercise yt-dlp's real format probe, which writes even for metadata-only calls."""
import errno
import importlib
import tempfile
from pathlib import Path

import pytest

from app.media_runner import PublicYoutubeDL, error_code, friendly_error


@pytest.fixture
def guarded_format_probe(tmp_path, monkeypatch):
    release = tmp_path / "read-only-release"
    release.mkdir()
    monkeypatch.chdir(release)
    original = tempfile.NamedTemporaryFile
    paths = []

    def create(*args, **kwargs):
        directory = Path(kwargs.get("dir") or tempfile.gettempdir()).resolve()
        if directory == release:
            raise OSError(errno.EROFS, "Read-only file system", str(directory / "probe.tmp"))
        file = original(*args, **kwargs)
        paths.append(Path(file.name))
        return file

    def extract(downloader, url, download=False):
        info = {"id": "fixture", "title": "Fixture", "format_id": "test",
                "url": "https://media.example/video.mp4", "protocol": "https", "ext": "mp4"}
        assert list(downloader._check_formats([info])) == [info]
        return info

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", create)
    monkeypatch.setattr(PublicYoutubeDL, "dl", lambda *args, **kwargs: (True, True))
    monkeypatch.setattr(PublicYoutubeDL, "extract_info", extract)
    return paths, release


@pytest.mark.parametrize("module_name", ["app.media_runner", "app.media_runner_v3"])
def test_download_format_probes_use_job_storage(module_name, tmp_path, monkeypatch, guarded_format_probe):
    paths, release = guarded_format_probe
    directory = tmp_path / "job"
    directory.mkdir()
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "install_network_guard", lambda: None)
    monkeypatch.setattr(PublicYoutubeDL, "process_info", lambda *args: (directory / "source.mp4").write_bytes(b"media"))
    monkeypatch.setattr(module, "convert_file", lambda *args: {})
    module.run("https://media.example/video.mp4", "mp4", directory)
    assert paths and all(path.parent == directory for path in paths)
    assert not any(path.exists() for path in paths)
    assert not list(release.iterdir())


def test_preview_format_probes_use_disposable_storage(monkeypatch, guarded_format_probe):
    from app import preview_lookup
    paths, release = guarded_format_probe
    monkeypatch.setattr(preview_lookup, "install_network_guard", lambda: None)
    result = preview_lookup.lookup("https://media.example/video.mp4")
    assert result["title"] == "Fixture"
    assert paths and all(path.parent != release for path in paths)
    assert all(not path.parent.exists() for path in paths)


@pytest.mark.parametrize("number", [errno.EROFS, errno.EACCES, errno.ENOSPC, errno.EDQUOT])
def test_local_storage_errors_are_not_provider_failures(number):
    error = OSError(number, "Storage failure", "/opt/crate-releases/build/probe.tmp")
    assert error_code(error) == "storage_error"
    assert "storage" in friendly_error(error).lower()
    assert "source" not in friendly_error(error).lower()


def test_wrapped_storage_failure_is_not_automatically_retried(tmp_path):
    from app.job_queue import Queue
    from app.models import Config
    try:
        try:
            raise OSError(errno.EROFS, "Read-only file system")
        except OSError as error:
            raise RuntimeError("Unable to download media") from error
    except RuntimeError as error:
        code = error_code(error)
    assert code == "storage_error"
    queue = Queue(Config(data_dir=tmp_path))
    assert not queue.schedule_auto_retry({"id": "storage-failure"}, code)
