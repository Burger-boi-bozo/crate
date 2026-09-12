import asyncio
import ipaddress
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.converter import Config, Queue, Submission, create_app
from app.media_policy import guarded_resolver, public_ip, validate_url
from app.media_runner import PublicYoutubeDL, convert_file, deny_external_download, probe

HEADERS = {"X-Crate-Request": "1", "Origin": "https://testserver"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    async def no_network(self, job):
        # Tests exercise real API/ownership/limits while preventing remote jobs.
        await asyncio.Event().wait()
    monkeypatch.setattr(Queue, "run", no_network)
    return create_app(Config(data_dir=tmp_path, access_code="classroom-test-code", secret="session-test-secret"))


def sign_in(client):
    assert client.post("/api/session", json={"code": "classroom-test-code"}, headers=HEADERS).status_code == 200


def test_access_control_and_cross_browser_isolation(app):
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/jobs").status_code == 401
        assert client.post("/api/session", json={"code": "wrong"}, headers=HEADERS).status_code == 401
        sign_in(client)
        cookie = client.cookies.get("crate_session")
        assert client.get("/api/session").json()["authenticated"]
        created = client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc", "format": "mp4"}, headers=HEADERS)
        assert created.status_code == 202
        job_id = created.json()["id"]
        assert "owner" not in created.json() and "path" not in created.json()
        # Another browser with the same shared access code has different jobs.
        client.cookies.clear()
        sign_in(client)
        assert client.get("/api/jobs").json() == []
        assert client.get(f"/api/jobs/{job_id}/file").status_code == 404
        assert client.post(f"/api/jobs/{job_id}/cancel", headers=HEADERS).status_code == 404
        assert client.delete(f"/api/jobs/{job_id}", headers=HEADERS).status_code == 404
        client.cookies.clear()
        client.cookies.set("crate_session", cookie)
        assert client.post(f"/api/jobs/{job_id}/cancel", headers=HEADERS).json()["status"] == "cancelled"


def test_cookie_csrf_and_bounded_requests(app):
    with TestClient(app, base_url="https://testserver") as client:
        result = client.post("/api/session", json={"code": "classroom-test-code"}, headers=HEADERS)
        cookie = result.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        body = {"url": "https://youtu.be/BaW_jenozKc"}
        assert client.post("/api/jobs", json=body).status_code == 403
        assert client.post("/api/jobs", json=body, headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/jobs", content=b"x" * 5000, headers=HEADERS).status_code == 413
        assert client.post("/api/jobs", json={**body, "command": "arbitrary"}, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://127.0.0.1/file", "https://169.254.169.254/latest/meta-data",
    "https://youtube.com.evil.example/watch", "https://youtube.com@127.0.0.1/a",
    "https://youtube.com:8443/watch", "https://youtube.com\\@evil.example/a",
    "https://youtube.com/a\nheader", "http://youtube.com/watch?v=a", "https://localhost/a",
])
def test_rejects_unsafe_or_unsupported_source_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.1.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1", "64:ff9b::7f00:1"])
def test_redirect_and_dns_rebinding_destinations_are_blocked(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    resolver = guarded_resolver(lambda *a, **kw: [(family, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(OSError):
        resolver("a-public-looking-host.example", 443)
    assert not public_ip(ipaddress.ip_address(address))


def test_transport_cannot_bypass_guard():
    with PublicYoutubeDL({"quiet": True, "proxy": ""}) as downloader:
        assert list(downloader._request_director.handlers) == ["Urllib"]
        with pytest.raises(ValueError):
            downloader.urlopen("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError):
        deny_external_download(None)


def test_queue_limits_and_expired_file_cleanup(app):
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client)
        body = {"url": "https://youtu.be/BaW_jenozKc"}
        ids = [client.post("/api/jobs", json=body, headers=HEADERS).json()["id"] for _ in range(2)]
        assert client.post("/api/jobs", json=body, headers=HEADERS).status_code == 429
        job = app.state.queue.jobs[ids[0]]
        folder = app.state.queue.folder(job["id"])
        folder.mkdir()
        path = folder / "download.mp4"
        path.write_bytes(b"a completed file")
        job.update(status="ready", path=str(path), expires_at=time.time() - 1)
        assert client.get(f"/api/jobs/{job['id']}/file").status_code == 410
        assert not path.exists()
        assert job["status"] == "expired"


def test_download_attachment_and_bounded_repeats(app):
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client)
        job_id = client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc"}, headers=HEADERS).json()["id"]
        job = app.state.queue.jobs[job_id]
        folder = app.state.queue.folder(job_id)
        folder.mkdir()
        path = folder / "download.mp4"
        path.write_bytes(b"test media")
        job.update(status="ready", path=str(path), filename="A lesson.mp4", expires_at=time.time() + 60)
        response = client.get(f"/api/jobs/{job_id}/file")
        assert response.content == b"test media"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["cache-control"] == "no-store"
        for _ in range(2):
            assert client.get(f"/api/jobs/{job_id}/file").status_code == 200
        assert client.get(f"/api/jobs/{job_id}/file").status_code == 429


def test_actual_mp4_and_mp3_conversion(tmp_path):
    source = tmp_path / "sample.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=green:s=160x90:r=10:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-threads", "1",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    for fmt, codec in (("mp4", "h264"), ("mp3", "mp3")):
        target = tmp_path / f"converted.{fmt}"
        convert_file(source, target, fmt, 600, 50 * 1024 * 1024)
        details = probe(target)
        assert any(stream["codec_name"] == codec for stream in details["streams"])
        assert 0 < float(details["format"]["duration"]) < 2
        assert target.stat().st_size > 0


def test_ffmpeg_rejects_playlist_input(tmp_path):
    playlist = tmp_path / "source.mp4"
    playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:1,\nhttp://127.0.0.1/private\n#EXT-X-ENDLIST\n")
    with pytest.raises(subprocess.CalledProcessError):
        probe(playlist)


def test_downloader_to_converted_file_with_offline_http_fixture(tmp_path):
    source = tmp_path / "fixture.mp4"
    output = tmp_path / "output"
    output.mkdir()
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=green:s=160x90:r=10:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-threads", "1",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    # The real yt-dlp downloader and FFmpeg run in a child with the production
    # limits. Only metadata and the HTTP response are replaced by owned test data.
    script = '''
import sys
from pathlib import Path
from app.media_runner import PublicYoutubeDL, run
from yt_dlp.networking.common import Response
fixture, output = map(Path, sys.argv[1:])
def extract(self, *args, **kwargs):
    return {"id": "fixture", "title": "Offline sample", "url": "https://upload.wikimedia.org/fixture.mp4",
            "ext": "mp4", "protocol": "https", "duration": 1.0, "format_id": "test", "height": 90,
            "vcodec": "avc1", "acodec": "mp4a", "extractor": "generic", "extractor_key": "Generic"}
def urlopen(self, request):
    url = request if isinstance(request, str) else request.url
    return Response(fixture.open("rb"), url, {"Content-Length": str(fixture.stat().st_size), "Content-Type": "video/mp4"})
PublicYoutubeDL.extract_info = extract
PublicYoutubeDL.urlopen = urlopen
run("https://upload.wikimedia.org/fixture.mp4", "mp4", output, 52428800, 600)
'''
    result = subprocess.run([sys.executable, "-c", script, str(source), str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert events[-1]["kind"] == "result"
    assert events[-1]["filename"] == "Offline sample.mp4"
    assert (output / events[-1]["file"]).is_file()


@pytest.mark.asyncio
async def test_cancel_kills_running_process_group(tmp_path):
    process = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
    queue = Queue(Config(data_dir=tmp_path))
    queue.processes["example"] = process
    job = {"id": "example", "status": "downloading"}
    await queue.cancel(job)
    assert process.returncode is not None
    assert job["status"] == "cancelled"


@pytest.mark.asyncio
async def test_queue_collects_child_output_and_cleans_leftovers(tmp_path, monkeypatch):
    real_spawn = asyncio.create_subprocess_exec
    async def fixture_spawn(*args, **kwargs):
        directory = args[5]
        script = '''
import json, sys
from pathlib import Path
directory = Path(sys.argv[1])
(directory / "download.mp4").write_bytes(b"media fixture")
(directory / "source.mp4").write_bytes(b"temporary source")
print(json.dumps({"kind": "progress", "status": "converting", "progress": 95}), flush=True)
print(json.dumps({"kind": "result", "file": "download.mp4", "title": "Sample", "filename": "Sample.mp4"}), flush=True)
'''
        return await real_spawn(sys.executable, "-c", script, directory, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fixture_spawn)
    queue = Queue(Config(data_dir=tmp_path))
    job = queue.submit("owner", Submission(url="https://youtu.be/BaW_jenozKc"))
    await queue.run(job)
    assert job["status"] == "ready", job["error"]
    assert job["progress"] == 100
    assert Path(job["path"]).read_bytes() == b"media fixture"
    assert not (queue.folder(job["id"]) / "source.mp4").exists()
