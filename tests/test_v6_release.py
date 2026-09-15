import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.job_queue import Queue
from app.media_convert_v6 import build_args
from app.media_policy import validate_url
from app.models import Config, MediaOptions, Submission
from app.runtime import create_app
from app.version import RELEASE_VERSION, RUNTIME_VERSION

HEADERS = {"X-Crate-Request": "1"}


def idle_queue(monkeypatch):
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "work", idle)


def login_admin(client):
    response = client.post("/api/admin/login", json={"password": "password"}, headers=HEADERS)
    assert response.status_code == 200
    return response


def test_release_identity_and_public_capabilities(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        assert RELEASE_VERSION == "6.0.0"
        assert RUNTIME_VERSION == "crate-v6"
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["version"] == "6.0.0"
        assert capabilities["features"]["advanced"] is True
        assert capabilities["features"]["shares"] is True
        releases = client.get("/api/releases").json()
        assert releases[0]["version"] == "6.0.0"
        assert releases[0]["current"] is True
        assert client.get("/manifest.webmanifest").status_code == 200
        assert client.get("/sw.js").status_code == 200


def test_three_core_defaults(monkeypatch, tmp_path):
    for key in ("CRATE_WORKERS", "CRATE_HEAVY_WORKERS", "CRATE_FFMPEG_THREADS",
                "CRATE_MIN_AVAILABLE_MEMORY", "CRATE_MAX_LOAD_RATIO"):
        monkeypatch.delenv(key, raising=False)
    config = Config(data_dir=tmp_path)
    assert config.workers == 3
    assert config.heavy_workers == 1
    assert config.min_available_memory == 768 * 1024**2
    assert config.max_load_ratio == 1.25


def test_advanced_options_validation():
    options = MediaOptions(format="webm", quality="1080", video_codec="vp9", audio_codec="opus",
                           start=2.5, end=8.0, fps=30, crf=28, subtitles=True,
                           subtitle_mode="external", filename_template="{creator} - {title}")
    assert options.end - options.start == 5.5
    assert options.option_dict()["video_codec"] == "vp9"
    with pytest.raises(ValidationError):
        MediaOptions(format="mp4", quality="320")
    with pytest.raises(ValidationError):
        MediaOptions(format="mp4", start=10, end=5)
    with pytest.raises(ValidationError):
        MediaOptions(format="flac", quality="192")
    with pytest.raises(ValidationError):
        MediaOptions(format="mp3", subtitles=True, subtitle_mode="embed")


def test_private_v6_admin_controls(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        for path in ("/api/admin/settings", "/api/admin/storage", "/api/admin/audit",
                     "/api/admin/security", "/api/admin/deployment", "/api/admin/tokens",
                     "/api/admin/webhooks", "/api/admin/passkeys", "/api/admin/capabilities",
                     "/api/admin/metrics/timeseries", "/api/admin/diagnostics", "/api/admin/backup"):
            assert client.get(path).status_code == 401
        login_admin(client)
        assert client.get("/api/admin/settings").status_code == 200
        assert client.get("/api/admin/storage").status_code == 200
        assert client.get("/api/admin/security").json()["session_ttl"] > 0
        assert client.get("/api/admin/deployment").json()["current"]["version"] == "6.0.0"


def test_api_tokens_are_scoped_and_isolated(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        login_admin(client)
        created = client.post("/api/admin/tokens", headers=HEADERS,
                              json={"name": "automation", "scopes": ["jobs:read", "jobs:write"]})
        assert created.status_code == 201
        token = created.json()["token"]
        bearer = {"Authorization": f"Bearer {token}", **HEADERS}
        job = client.post("/api/jobs", headers=bearer,
                          json={"url": "https://example.com/media.mp4", "format": "mp4", "quality": "best"})
        assert job.status_code == 202
        assert len(client.get("/api/jobs", headers=bearer).json()) == 1
        read_only = client.post("/api/admin/tokens", headers=HEADERS,
                                json={"name": "reader", "scopes": ["jobs:read"]}).json()["token"]
        denied = client.post("/api/jobs", headers={"Authorization": f"Bearer {read_only}", **HEADERS},
                             json={"url": "https://example.com/other.mp4"})
        assert denied.status_code == 403


def test_upload_endpoint_queues_local_media(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        client.get("/api/session")
        response = client.post("/api/uploads", headers=HEADERS,
                               files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
                               data={"options": json.dumps({"format": "mp4", "quality": "best"})})
        assert response.status_code == 202
        assert response.json()["source_kind"] == "upload"


def test_password_share_link_and_download_limit(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        client.get("/api/session")
        created = client.post("/api/jobs", headers=HEADERS,
                              json={"url": "https://example.com/file.mp4", "format": "mp4"}).json()
        job = app.state.queue.jobs[created["id"]]
        folder = app.state.queue.folder(job["id"]); folder.mkdir(parents=True, exist_ok=True)
        output = folder / "ready.mp4"; output.write_bytes(b"crate-test-file")
        job.update(status="ready", stage="ready", path=str(output), filename="ready.mp4",
                   size=output.stat().st_size, finished_at=1, expires_at=9999999999)
        app.state.queue.save()
        share = client.post(f"/api/jobs/{job['id']}/share", headers=HEADERS,
                            json={"password": "open-sesame", "max_downloads": 1, "expires_in": 3600})
        assert share.status_code == 201
        token = share.json()["url"].rsplit("/", 1)[-1]
        assert client.get(f"/api/share/{token}/file").status_code == 401
        assert client.post(f"/share/{token}/unlock", data={"password": "wrong"}).status_code == 401
        assert client.post(f"/share/{token}/unlock", data={"password": "open-sesame"}).status_code == 200
        assert client.get(f"/api/share/{token}/file").content == b"crate-test-file"
        assert client.get(f"/api/share/{token}/file").status_code == 410


def test_cache_reuse_creates_new_ready_job(tmp_path):
    queue = Queue(Config(data_dir=tmp_path, cache_reuse=True))
    queue.store.initialize()
    body = Submission(url="https://example.com/media.mp4", format="mp4", quality="best")
    first = queue.submit("owner-a", body)
    folder = queue.folder(first["id"]); folder.mkdir(parents=True, exist_ok=True)
    output = folder / "cached.mp4"; output.write_bytes(b"cached-output")
    first.update(status="ready", path=str(output), filename="cached.mp4", size=output.stat().st_size,
                 checksum="abc", finished_at=1, expires_at=9999999999)
    queue.save()
    reused = queue.submit("owner-b", body)
    assert reused["id"] != first["id"]
    assert reused["status"] == "ready"
    assert reused["cache_hit"] is True
    assert Path(reused["path"]).read_bytes() == b"cached-output"


def test_persistent_batch_and_runtime_settings(tmp_path):
    queue = Queue(Config(data_dir=tmp_path))
    queue.store.initialize()
    bodies = [Submission(url=f"https://example.com/{name}.mp4") for name in ("a", "b")]
    batch_id, jobs = queue.submit_batch("owner", bodies)
    queue.update_setting("workers", 3)
    queue.save()
    restored = Queue(Config(data_dir=tmp_path, workers=1))
    restored.store.initialize(); restored.jobs = restored.store.load_jobs(); restored.batches = restored.store.load_batches(); restored.apply_saved_settings()
    assert batch_id in restored.batches
    assert len(restored.batches[batch_id]["job_ids"]) == 2
    assert restored.config.workers == 3


def test_fast_codec_args_for_small_server(tmp_path, monkeypatch):
    monkeypatch.setenv("CRATE_FFMPEG_THREADS", "2")
    monkeypatch.setattr("app.media_convert_v6.choose_video_encoder", lambda codec, hardware: ("libx265", False))
    details = {"streams": [
        {"codec_type": "video", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "aac"},
    ], "format": {"duration": "10"}}
    args = build_args(tmp_path / "in.mkv", tmp_path / "out.mp4", "mp4", "1080", details,
                      {"video_codec": "hevc", "audio_codec": "auto", "hardware": "off", "quality": "1080"},
                      {"title": "test"})
    assert "veryfast" in args
    assert args[args.index("-threads") + 1] == "2"
    assert "+faststart" in args


def test_webhook_and_media_url_policy_blocks_local_networks():
    for url in ("http://127.0.0.1/hook", "http://localhost/hook", "http://10.0.0.1/hook"):
        with pytest.raises(ValueError):
            validate_url(url, source=False)
    assert validate_url("https://example.com/hook", source=False) == "https://example.com/hook"


def test_blue_green_deploy_and_resource_tuning_are_pinned():
    deploy = Path("scripts/proxmox-update-guest.sh").read_text()
    bootstrap = Path("scripts/proxmox-guest.sh").read_text()
    for text in (deploy, bootstrap):
        assert "CRATE_WORKERS=3" in text or "CRATE_WORKERS 3" in text
        assert "CRATE_HEAVY_WORKERS=1" in text or "CRATE_HEAVY_WORKERS 1" in text
        assert "CRATE_FFMPEG_THREADS=2" in text or "CRATE_FFMPEG_THREADS 2" in text
        assert "crate-v6" in text
    assert "PORT=18082" in deploy
    assert "/opt/crate-releases" in deploy
    assert "/opt/crate-current" in deploy
    assert "MemoryMax=3200M" in deploy


def test_admin_diagnostics_backup_and_restore(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app) as client:
        login_admin(client)
        diagnostics = client.get("/api/admin/diagnostics")
        assert diagnostics.status_code == 200
        with zipfile.ZipFile(io.BytesIO(diagnostics.content)) as archive:
            assert "diagnostics.json" in archive.namelist()
            data = json.loads(archive.read("diagnostics.json"))
            assert data["version"]["version"] == "6.0.0"
            assert "admin_password" not in json.dumps(data)
        backup = client.get("/api/admin/backup")
        assert backup.status_code == 200
        with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
            assert {"crate.db", "backup.json"}.issubset(archive.namelist())
        app.state.queue.maintenance = True
        restored = client.post("/api/admin/restore", headers=HEADERS,
                               files={"file": ("backup.zip", backup.content, "application/zip")})
        assert restored.status_code == 200
        assert restored.json()["restored"] is True


def test_v6_artifacts_are_present_and_exclusions_stay_excluded():
    public = Path("app/converter_static/v6.js").read_text()
    admin = Path("app/converter_static/admin-v6.js").read_text()
    admin_html = Path("app/converter_static/admin.html").read_text()
    manifest = json.loads(Path("app/converter_static/manifest.webmanifest").read_text())
    extension = json.loads(Path("extras/browser-extension/manifest.json").read_text())
    assert "serviceWorker.register('/sw.js')" in public
    assert "share_target" in manifest
    assert "notifications" not in json.dumps(manifest).lower()
    assert "qr" not in public.lower()
    assert "/api/admin/settings" in admin and "/api/admin/backup" in admin_html
    assert set(extension["permissions"]) == {"activeTab", "storage"}
    assert Path("tools/crate-cli.py").is_file()
    assert Path("docs/automation/PHONE_AUTOMATION.md").is_file()


def test_gif_filter_maps_palette_output(tmp_path):
    details = {"streams": [{"codec_type": "video", "width": 640, "height": 360}], "format": {"duration": "3"}}
    args = build_args(tmp_path / "in.mp4", tmp_path / "out.gif", "gif", "480", details, {"fps": 12, "quality": "480"}, {})
    graph = args[args.index("-filter_complex") + 1]
    assert "paletteuse[v]" in graph
    assert args[args.index("-map") + 1] == "[v]"


def test_passkey_registration_options_are_admin_only(tmp_path, monkeypatch):
    idle_queue(monkeypatch)
    app = create_app(Config(data_dir=tmp_path, secure_cookie=False, admin_password="password"))
    with TestClient(app, base_url="https://crate.test") as client:
        denied = client.post("/api/admin/passkeys/register/options", headers=HEADERS, json={"name":"Laptop"})
        assert denied.status_code == 401
        login_admin(client)
        response = client.post("/api/admin/passkeys/register/options", headers=HEADERS, json={"name":"Laptop"})
        assert response.status_code == 200
        body = response.json()
        assert len(body["challenge_id"]) >= 16
        assert body["options"]["rp"]["name"] == "Crate"
        assert client.post("/api/admin/passkeys/auth/options", headers=HEADERS).status_code == 404
