"""Smoke checks for the owner-scoped transcription API.

Run in an isolated test root: .venv/bin/python tests/test_transcription_smoke.py
"""
import os
import subprocess
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="crate-transcribe-smoke-"))
os.environ["CRATE_TRANSCRIPTS_DIR"] = str(root / "transcripts")
os.environ["CRATE_DATA_DIR"] = str(root / "crate")
os.environ["CRATE_SESSION_SECRET"] = "transcription-smoke-only"
os.environ["CRATE_SECURE_COOKIE"] = "false"

from fastapi.testclient import TestClient
from app.runtime_v6 import app

source = root / "smoke.wav"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=1", "-y", str(source)],
               check=True, timeout=30)

with TestClient(app) as browser:
    response = browser.get("/api/session")
    assert response.status_code == 200, response.text
    headers = {"X-Crate-Request": "1"}
    response = browser.post("/api/transcriptions/upload", files={
        "file": ("smoke.wav", source.read_bytes(), "audio/wav")},
        data={"language": "en"}, headers=headers)
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "queued"
    tid = created["id"]
    assert browser.get("/api/transcriptions").json()[0]["id"] == tid
    assert browser.get("/api/transcriptions/" + tid).status_code == 200
    assert browser.get("/api/transcriptions/" + tid + "/file").status_code == 409
    assert browser.post("/api/transcriptions/from-job", json={"job_id": "bad"},
                        headers=headers).status_code == 404
    assert browser.delete("/api/transcriptions/" + tid, headers=headers).status_code == 204
    assert browser.get("/api/transcriptions/" + tid).status_code == 404
print("Transcription API smoke test passed")
