# Crate

**A link. A file. Done.** Crate is a self-hosted public-media converter built for a small Proxmox VM. Paste one link or a batch, choose video or audio, and Crate handles the queue, conversion, and download.

Current release: **v6.0.0**

## What v6 includes

- Single-link and batch downloads (up to 20 links per batch) with ZIP collection.
- MP4, MKV, WebM, GIF, WebP, MP3, M4A, MKA, Opus, FLAC, WAV, and AAC outputs with advanced codec, CRF, FPS, trimming, metadata, thumbnail, subtitle, and filename controls.
- Live job updates over Server-Sent Events with polling fallback.
- SQLite/WAL persistence, restart recovery, duplicate suppression, and job timelines.
- Adaptive CPU/RAM/disk-aware scheduling, user/admin priorities, learned ETA history, automatic retries, fallback streams, resumable work, and completed-output reuse.
- Link previews with source metadata, estimated size, and format/quality recommendations.
- Private admin control room with queue/storage/config controls, metrics, provider health, audit logs, passkeys, API tokens, webhooks, diagnostics, backup/restore, and deployment history.
- Automatic deletion of completed files after **24 hours** (`CRATE_TTL=86400`).
- PWA install/share-target support, local uploads, temporary share links, CLI/browser-extension automation, and Cloudflare Tunnel deployment without router ports.

Source availability still depends on each provider. Private/protected media, ongoing live streams, and provider login walls are not bypassed. Only download media you own or have permission to save.

## Production layout

The current production path is:

`Browser → Cloudflare Tunnel → Crate on Proxmox VM → yt-dlp / FFmpeg`

The app uses a single Uvicorn process with internal workers. Production updates are pinned to the exact `main` commit and only deploy after GitHub Actions succeeds. v6 builds each release in its own directory, health-gates it on localhost port `18082`, atomically switches `/opt/crate-current`, verifies the exact build SHA on port `8080`, and retains the previous release for rollback.

## Admin

Open `/admin` on your Crate hostname. There is no username.

The deployment default admin password is `password`. Future manual password changes are preserved by updates. v6 also supports passkeys, trusted-session duration, optional admin IP restrictions, and login rate limiting.

Because `password` is intentionally simple, change `CRATE_ADMIN_PASSWORD` if the admin page will be exposed beyond your own use.

## Install or update on Proxmox

See [docs/PROXMOX.md](docs/PROXMOX.md) for the full installer. To update an existing installer-managed VM:

```bash
curl -fL https://raw.githubusercontent.com/Burger-boi-bozo/crate/main/scripts/proxmox-update.py -o /root/crate-update.py
python3 /root/crate-update.py
```

The installed auto-updater checks `main` every few minutes and deploys only a successful push workflow.

## Run locally

Requirements: Python 3.12+, FFmpeg/ffprobe, and Node.js 22+ for UI tests.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CRATE_SECURE_COOKIE=false
python -m app.serve
```

Open `http://localhost:8080`.

## Main configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CRATE_DATA_DIR` | `./converter-data` | Database and completed-media directory |
| `CRATE_ADMIN_PASSWORD` | deployment default `password` | Password for `/admin` |
| `CRATE_TTL` | `86400` | Seconds to retain completed files |
| `CRATE_WORKERS` | `3` | Internal job workers; production target is 3 vCPU / 4 GB RAM |
| `CRATE_HEAVY_WORKERS` | `1` | Concurrent CPU-heavy conversions |
| `CRATE_FFMPEG_THREADS` | production `2` | FFmpeg threads per heavy conversion |
| `CRATE_MIN_AVAILABLE_MEMORY` | production `805306368` | RAM reserve before starting another heavy job |
| `CRATE_PER_OWNER_ACTIVE` | `2` | Active jobs allowed per browser session |
| `CRATE_MAX_BATCH` | `20` | Maximum links in one batch |
| `CRATE_MIN_FREE_BYTES` | `1073741824` | Disk reserve before accepting new work |

## API highlights

Start with `GET /api/session` and retain the anonymous HTTP-only cookie. Mutating requests require `X-Crate-Request: 1` and the same browser origin.

- `POST /api/jobs` — submit one conversion.
- `GET /api/jobs` — list this browser session's jobs.
- `POST /api/batches` — submit multiple URLs.
- `POST /api/uploads` — convert local media.
- `GET /api/batches/{id}` — batch state.
- `GET /api/batches/{id}/zip` — collect a completed batch.
- `GET /api/events/stream` — live SSE job updates.
- `POST /api/preview` — inspect a supported public link before conversion.
- `POST /api/jobs/{id}/share` — create a temporary expiring share link.
- `GET /api/health` — runtime/version readiness.
- Bearer API tokens for automation are created only from `/admin`.
- `/api/admin/*` — authenticated operator endpoints.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm run test:ui
npm run typecheck
python scripts/v6-smoke.py --public
```

GitHub Actions runs the Python suite, UI suite, and deployment-runtime import gate for every release PR and `main` push. The separate provider-canary workflow tests candidate/latest yt-dlp builds against live public fixtures before dependency adoption.

## Change log

See [CHANGELOG.md](CHANGELOG.md). The latest entries are also shown near the bottom of the Crate web UI.

## Repository note

The repository still contains older Docker/Cloudflare prototype files for compatibility and reference, but the supported production application is the Proxmox-hosted `app.serve` runtime described above.
