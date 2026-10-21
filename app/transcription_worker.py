"""Isolated, persistent CPU speech-to-text worker for Crate.

Launch with: /opt/crate-transcriber-venv/bin/python -m app.transcription_worker
The main downloader does not import Whisper or share transcription CPU workers.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(os.getenv("CRATE_TRANSCRIPTS_DIR", "/var/lib/crate/transcriptions"))
MODEL_NAME = os.getenv("CRATE_TRANSCRIBE_MODEL", "base")
THREADS = max(1, min(4, int(os.getenv("CRATE_TRANSCRIBE_THREADS", "2"))))


def connect():
    db = sqlite3.connect(ROOT / "jobs.sqlite3", timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def update(ident, status=None, progress=None, message=None, error=None,
           detected_language=None, segment_count=None):
    fields = {"updated_at": time.time()}
    for name, value in (("status", status), ("progress", progress), ("message", message),
                        ("error", error), ("detected_language", detected_language),
                        ("segment_count", segment_count)):
        if value is not None:
            fields[name] = value
    with connect() as db:
        db.execute("UPDATE transcripts SET " + ",".join(key + "=?" for key in fields)
                   + " WHERE id=?", (*fields.values(), ident))


def heartbeat(stop):
    while not stop.is_set():
        try:
            with connect() as db:
                db.execute("INSERT OR REPLACE INTO worker_status(id,updated_at,model) VALUES(1,?,?)",
                           (time.time(), MODEL_NAME))
        except Exception as exc:
            print("heartbeat error:", type(exc).__name__, str(exc)[:150], flush=True)
        stop.wait(10)


def claim():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM transcripts WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE transcripts SET status='extracting',progress=1,message='Checking audio track',updated_at=? WHERE id=?",
                       (time.time(), row["id"]))
        return dict(row) if row else None


def timestamp(seconds, vtt=False):
    ms = max(0, round(float(seconds) * 1000))
    hh, rem = divmod(ms, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, mil = divmod(rem, 1000)
    return f"{hh:02}:{mm:02}:{ss:02}{'.' if vtt else ','}{mil:03}"


def save_formats(directory, segments, detected_language):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    sentences = [x["text"] for x in segments]
    srt = []
    vtt = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        text = seg["text"]
        srt.extend((str(i), timestamp(seg["start"]) + " --> " + timestamp(seg["end"]), text, ""))
        vtt.extend((timestamp(seg["start"], True) + " --> " + timestamp(seg["end"], True), text, ""))
    files = {
        "txt": "\n".join(sentences) + ("\n" if sentences else ""),
        "srt": "\n".join(srt) + ("\n" if srt else ""),
        "vtt": "\n".join(vtt) + "\n",
        "json": json.dumps({"language": detected_language, "segments": segments}, indent=2, ensure_ascii=False) + "\n",
    }
    for ext, value in files.items():
        tmp = directory / ("transcript." + ext + ".tmp")
        tmp.write_text(value, encoding="utf-8")
        os.replace(tmp, directory / ("transcript." + ext))


def process(row, model):
    ident = row["id"]
    source = Path(row["source_path"])
    if not source.is_file():
        raise ValueError("Source media is unavailable; it may have expired.")
    result_dir = ROOT / ident
    result_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    wav = result_dir / "audio.processing.wav"
    try:
        meta = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                               "stream=codec_type:format=duration", "-of", "json", str(source)],
                              capture_output=True, text=True, timeout=90, check=True)
        info = json.loads(meta.stdout)
        if not any(track.get("codec_type") == "audio" for track in info.get("streams", [])):
            raise ValueError("No audio track found in this file.")
        duration = float(info.get("format", {}).get("duration") or 0)
        if duration > 8 * 3600:
            raise ValueError("Media longer than eight hours is not supported.")
        update(ident, message="Extracting mono audio with FFmpeg", progress=3)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(wav)],
                       capture_output=True, text=True, timeout=3600, check=True)
        update(ident, status="transcribing", progress=10, message="Recognizing speech")
        language = None if row["language"] == "auto" else row["language"]
        iterator, details = model.transcribe(str(wav), language=language, vad_filter=True,
                                            beam_size=3, condition_on_previous_text=False)
        detected = details.language or row["language"]
        length = max(details.duration, 1)
        segments = []
        last_write = 0
        for part in iterator:
            text = part.text.strip()
            if not text:
                continue
            segments.append({"start": round(part.start, 3), "end": round(part.end, 3), "text": text})
            if time.monotonic() - last_write > 2:
                update(ident, progress=min(95, 10 + int(85 * part.end / length)),
                       message="Recognizing speech", detected_language=detected,
                       segment_count=len(segments))
                last_write = time.monotonic()
        update(ident, progress=97, message="Writing transcript and subtitles")
        save_formats(result_dir, segments, detected)
        update(ident, status="completed", progress=100,
               detected_language=detected, segment_count=len(segments),
               message="Transcript ready" if segments else "Completed; no speech detected")
        if row["uploaded"]:
            try: source.unlink(missing_ok=True)
            except OSError as exc: print("upload cleanup warning:", str(exc)[:150], flush=True)
    finally:
        wav.unlink(missing_ok=True)


def main():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS worker_status(
            id INTEGER PRIMARY KEY, updated_at REAL NOT NULL, model TEXT NOT NULL)""")
        db.execute("UPDATE transcripts SET status='queued',progress=0,message='Resuming after restart' "
                   "WHERE status IN ('extracting','transcribing')")
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(stop,), daemon=True)
    thread.start()
    model = None
    print("Crate transcriber worker started with model", MODEL_NAME, flush=True)
    try:
        while True:
            row = claim()
            if not row:
                time.sleep(2)
                continue
            try:
                if model is None:
                    from faster_whisper import WhisperModel
                    update(row["id"], message="Loading local speech model", progress=2)
                    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8",
                                         cpu_threads=THREADS, num_workers=1,
                                         download_root=str(ROOT / "models"))
                process(row, model)
                print("Completed:", row["id"], flush=True)
            except Exception as exc:
                message = str(exc)[:400] or type(exc).__name__
                update(row["id"], status="failed", progress=0, error=message,
                       message="Transcription failed")
                print("Failed:", row["id"], type(exc).__name__, message, flush=True)
                if row["uploaded"]:
                    try: Path(row["source_path"]).unlink(missing_ok=True)
                    except OSError as cleanup: print("upload cleanup warning:", str(cleanup)[:150], flush=True)
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
