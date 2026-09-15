#!/usr/bin/env python3
"""End-to-end Crate v6 release smoke and small-server conversion benchmark."""
from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

POLL_SECONDS = 0.25


def wait_job(client: httpx.Client, job_id: str, timeout: float = 90) -> tuple[dict, float]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        jobs = client.get("/api/jobs").raise_for_status().json()
        job = next(item for item in jobs if item["id"] == job_id)
        if job["status"] == "ready":
            return job, time.monotonic() - started
        if job["status"] == "failed":
            raise RuntimeError(f"{job.get('error_code')}: {job.get('error')}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


def fixture(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
        "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
    ], check=True, timeout=30)


def upload_job(client: httpx.Client, source: Path, options: dict) -> dict:
    with source.open("rb") as stream:
        response = client.post(
            "/api/uploads", files={"file": (source.name, stream, "video/mp4")},
            data={"options": __import__("json").dumps(options)}, headers={"X-Crate-Request": "1"},
            timeout=60,
        )
    response.raise_for_status()
    return response.json()


def download_and_probe(client: httpx.Client, job: dict, directory: Path) -> dict:
    response = client.get(f"/api/jobs/{job['id']}/file", timeout=60)
    response.raise_for_status()
    target = directory / job["filename"]
    target.write_bytes(response.content)
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)
    ], capture_output=True, text=True, check=True, timeout=30)
    return __import__("json").loads(result.stdout)


def run_case(client, source, directory, name, options, limit):
    queued = upload_job(client, source, options)
    job, elapsed = wait_job(client, queued["id"], timeout=max(90, limit * 2))
    details = download_and_probe(client, job, directory)
    assert job.get("checksum") and len(job["checksum"]) == 64
    assert job["size"] > 0
    if elapsed > limit:
        raise RuntimeError(f"{name} took {elapsed:.2f}s; release limit is {limit:.2f}s")
    print(f"PASS {name:12} {elapsed:6.2f}s  {job['size']/1024:8.1f} KiB  encoder={job.get('video_encoder')}")
    client.delete(f"/api/jobs/{job['id']}", headers={"X-Crate-Request": "1"}).raise_for_status()
    return details, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:18082")
    parser.add_argument("--public", action="store_true", help="also run a public-network source conversion")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base, follow_redirects=True, timeout=60) as client:
        health = client.get("/api/health").raise_for_status().json()
        assert health["status"] == "ok" and health["runtime"] == "crate-v6" and health["workers"] == 3
        client.get("/api/session").raise_for_status()
        caps = client.get("/api/capabilities").raise_for_status().json()
        assert caps["version"] == "6.0.0" and caps["features"]["uploads"] is True
        print("PASS health       ", health["version"], health["build"], "workers=3")

        with tempfile.TemporaryDirectory(prefix="crate-v6-smoke-") as raw:
            directory = Path(raw)
            source = directory / "fixture.mp4"
            fixture(source)
            cases = [
                ("h264-mp4", {"format":"mp4","quality":"480","video_codec":"h264","crf":23,"hardware":"auto","start":0.25,"end":2.75}, 15),
                ("hevc-mp4", {"format":"mp4","quality":"480","video_codec":"hevc","crf":28,"hardware":"off","start":0.25,"end":2.75}, 25),
                ("vp9-webm", {"format":"webm","quality":"480","video_codec":"vp9","audio_codec":"opus","crf":32,"hardware":"off"}, 30),
                ("av1-mp4", {"format":"mp4","quality":"480","video_codec":"av1","audio_codec":"aac","crf":32,"hardware":"off"}, 40),
                ("mkv-copy", {"format":"mkv","quality":"best","video_codec":"copy","audio_codec":"copy"}, 15),
                ("mka-copy", {"format":"mka","quality":"best","audio_codec":"copy"}, 15),
                ("mp3", {"format":"mp3","quality":"192"}, 15),
                ("m4a", {"format":"m4a","quality":"192"}, 15),
                ("opus", {"format":"opus","quality":"192"}, 15),
                ("flac", {"format":"flac","quality":"best"}, 15),
                ("wav", {"format":"wav","quality":"best"}, 15),
                ("aac", {"format":"aac","quality":"192"}, 15),
                ("gif", {"format":"gif","quality":"480","fps":12}, 20),
                ("webp", {"format":"webp","quality":"480","fps":12}, 20),
            ]
            timings = []
            for name, options, limit in cases:
                _, elapsed = run_case(client, source, directory, name, options, limit)
                timings.append((name, elapsed))
            print(f"PASS matrix       {len(cases)} formats · slowest {max(timings, key=lambda x: x[1])[0]} {max(x[1] for x in timings):.2f}s")

            if args.public:
                preview = client.post(
                    "/api/preview", headers={"X-Crate-Request": "1"},
                    json={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}, timeout=45,
                )
                preview.raise_for_status()
                assert preview.json()["title"]
                print("PASS preview      ", preview.json()["title"])

                response = client.post(
                    "/api/jobs", headers={"X-Crate-Request": "1"},
                    json={"url":"https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
                          "format":"mp4","quality":"best"},
                )
                response.raise_for_status()
                job, elapsed = wait_job(client, response.json()["id"], timeout=60)
                details = download_and_probe(client, job, directory)
                assert any(stream.get("codec_type") == "video" for stream in details.get("streams", []))
                assert elapsed < 30
                print(f"PASS public-mp4   {elapsed:.2f}s · {job['size']/1024:.1f} KiB")
                client.delete(f"/api/jobs/{job['id']}", headers={"X-Crate-Request": "1"}).raise_for_status()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
