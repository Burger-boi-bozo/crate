# Crate

**A link. A file. Done.** Crate is a self-hosted public-media converter built for a small Proxmox VM. Paste one link or a batch, choose video or audio, and Crate handles the queue, conversion, and download.

Current release: **v5.1.0**

## What v5.1 includes

- Single-link and batch downloads (up to 20 links per batch) with ZIP collection.
- MP4, MP3, MKV, and MKA outputs with quality controls and no source upscaling.
- Live job updates over Server-Sent Events with polling fallback.
- SQLite/WAL persistence, restart recovery, duplicate suppression, and job timelines.
- CPU/load-aware scheduling, heavy-job limits, per-browser fairness, and disk reserve checks.
- Link previews with source metadata, estimated size, and format/quality recommendations.
- Admin dashboard with queue controls, maintenance mode, cleanup, metrics, and provider health.
- Automatic deletion of completed files after **24 hours** (`CRATE_TTL=86400`).
- Cloudflare Tunnel deployment without opening router ports.

Source availability still depends on each provider. Private/protected media, ongoing live streams, and provider login walls are not bypassed. Only download media you own or have permission to save.

## Production layout

The current production path is:

`Browser → Cloudflare Tunnel → Crate on Proxmox VM → yt-dlp / FFmpeg`

The app uses a single Uvicorn process with internal workers. Production updates are pinned to the exact `main` commit and only deploy after the GitHub Actions workflow succeeds; the update script verifies the reported build SHA and rolls back on failure.

## Admin

Open `/admin` on your Crate hostname. There is no username.

The v5.1 default admin password is `password`. On the first v5.1 upgrade, an older generated password is migrated to that default once. Later manual password changes are preserved by future updates.

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
| `CRATE_WORKERS` | `2` | Internal job workers |
| `CRATE_HEAVY_WORKERS` | `1` | Concurrent CPU-heavy conversions |
| `CRATE_PER_OWNER_ACTIVE` | `2` | Active jobs allowed per browser session |
| `CRATE_MAX_BATCH` | `20` | Maximum links in one batch |
| `CRATE_MIN_FREE_BYTES` | `1073741824` | Disk reserve before accepting new work |

## API highlights

Start with `GET /api/session` and retain the anonymous HTTP-only cookie. Mutating requests require `X-Crate-Request: 1` and the same browser origin.

- `POST /api/jobs` — submit one conversion.
- `GET /api/jobs` — list this browser session's jobs.
- `POST /api/batches` — submit multiple URLs.
- `GET /api/batches/{id}` — batch state.
- `GET /api/batches/{id}/zip` — collect a completed batch.
- `GET /api/events/stream` — live SSE job updates.
- `POST /api/preview` — inspect a supported public link before conversion.
- `GET /api/health` — runtime/version readiness.
- `/api/admin/*` — authenticated operator endpoints.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm run test:ui
npm run typecheck
```

GitHub Actions runs the Python suite, UI suite, and deployment-runtime import gate for every release PR and `main` push.

## Change log

See [CHANGELOG.md](CHANGELOG.md). The latest entries are also shown near the bottom of the Crate web UI.

## Repository note

The repository still contains older Docker/Cloudflare prototype files for compatibility and reference, but the supported production application is the Proxmox-hosted `app.serve` runtime described above.
