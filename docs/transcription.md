# Crate transcription

Crate has a same-origin speech-to-text interface at `/#transcribe`.
It accepts public media URLs, local video/audio uploads, and completed Crate download jobs. The link path submits a low-priority audio job to Crate's existing downloader and automatically transcribes the finished file, including when the browser is closed.
It extracts the primary audio stream with FFmpeg, recognizes speech using
faster-whisper locally, and makes four files available:

- TXT: readable text
- SRT: subtitles for video editors
- VTT: web captions
- JSON: timestamps, segment text and detected language

The original media is not altered. Uploads are removed after successful or failed
processing; Crate-owned downloads stay managed by Crate and can expire normally.
Results persist in `/var/lib/crate/transcriptions` until the user deletes them.

## Installation on Proxmox VM 101

1. Deploy the application files and ensure `app.runtime_v6` imports and calls
   `api_transcription.install(app, queue, config, get_owner)`. Add
   `/api/transcriptions/upload` to the web middleware's streaming paths.
   Include `/assets/transcribe.css` and `/assets/transcribe.js` in the index.
2. Create an isolated virtual environment at `/opt/crate-transcriber-venv`:
   `python3 -m venv /opt/crate-transcriber-venv` and
   `/opt/crate-transcriber-venv/bin/pip install -r requirements-transcriber.txt`.
3. Create `/var/lib/crate/transcriptions` owned by `crate:crate`, with mode 0700.
   Put its Hugging Face cache below that directory (not in another service's cache).
4. Install `deploy/crate-transcriber.service` into
   `/etc/systemd/system/crate-transcriber.service`, run
   `systemctl daemon-reload`, and enable/start the worker.
5. Run `PYTHONPATH=. .venv/bin/python tests/test_transcription_smoke.py`.
   Then submit a real speech sample and verify all four exports.

The transcriber has a single persistent queue and a separate systemd unit.
Queued/extracting/transcribing tasks are recovered on worker restart.
Systemd limits the worker to 120% CPU and 1.8 GB memory by default.
The speech model is configured by `CRATE_TRANSCRIBE_MODEL` (default `base`);
`CRATE_TRANSCRIBE_THREADS` defaults to 2.

## Scope and constraints

Requests use Crate's existing owner cookie/API token model, session protections,
and `jobs:read`/`jobs:write` scopes. Files are served only to their owners.
No arbitrary server filesystem paths are accepted. The upload limit is 4 GB
and the maximum media duration is eight hours. GIF/WebP files without audio
are rejected by the worker with an explanatory message. This is speech-to-text,
not a translation or speaker-diarization service.

## Pasted URLs

POST `/api/transcriptions/from-url` with `{"url":"https://...","language":"auto"}`.
The URL passes Crate's existing public-URL validation and downloader, including
its private-address protections and source limitations. The response immediately
returns a persisted transcript job in `waiting_download` state. The separate
speech worker checks the Crate job database without modifying it; when the audio
is ready, it copies that audio to its isolated temporary directory, runs local
speech recognition and serves TXT/SRT/VTT/JSON outputs from the normal owner-
scoped transcript endpoints. Reusing a completed download, upload transcription,
and manual from-job transcription continue to work. If a provider blocks access
or a download fails, the transcript job reports the failure rather than hanging.

The worker's read-only access to Crate's job store defaults to
`/var/lib/crate/state/crate.db`, customizable with `CRATE_DOWNLOADER_DB`.
The speech model does not make external inference calls.
