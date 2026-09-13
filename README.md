# Crate — A link. A file. Done.

Paste a public media link, choose a format and quality, and save the file. The converter runs on your own Proxmox VM, with Cloudflare Tunnel connecting your domain. No account or access code is required.

## Self-hosted converter

- Maximum available video resolution, including 4K/8K when the source provides it; optional 2160p, 1440p, 1080p, 720p and 480p choices.
- Compatible MP4, MP3 up to 320 kbps, or original video/audio in MKV/MKA with no re-encoding. Sources are never upscaled; MP3 bitrate does not improve source fidelity.
- No default duration, file-size, daily-job, queue-length, repeat-download or conversion-time caps.
- A serial queue, cancellation, browser ownership, audio/video filters and native streaming downloads for large files.
- Persistent queue/history and completed files in `CRATE_DATA_DIR`. Interrupted jobs restart from the source after a service restart. Files remain until deleted; clearing history also deletes completed files.
- Public links handled by yt-dlp: YouTube, Vimeo, TikTok, Instagram, Facebook, Reddit, X, SoundCloud, Bandcamp, Apple Podcasts and many more.
- Spotify and Apple Music **individual song lookup**: read public metadata, show YouTube candidates, and let the visitor check and choose a recording before submitting it. This does not export subscription streams, silently substitute a song, or use previews as full tracks.

Availability depends on the source. Public pages can reject requests, require login or change their format. Individual recordings are supported; playlists, ongoing live streams, private and protected media are not. Only save media you own or have permission to download.

[Install or update on Proxmox](docs/PROXMOX.md). Existing Render and Cloudflare Containers deployment files remain for historical compatibility; this unrestricted converter targets your own persistent server. Host resources and upstream service constraints still apply.

### Update an existing Crate VM

Run in **Proxmox → node → Shell**, as root:

```bash
curl -fL https://raw.githubusercontent.com/Burger-boi-bozo/crate/main/scripts/proxmox-update.py -o /root/crate-update.py
python3 /root/crate-update.py
```

The updater discovers the existing installer-created VM, verifies its SSH host key through the guest agent, updates one pinned revision and verifies health. It preserves the existing tunnel, domain and environment. If you installed more than one Crate VM, use `--vmid NUMBER`. Finish active downloads before the first update from the old nonpersistent edition.

### Run locally

Use Python 3.12+, FFmpeg/ffprobe and Node.js 22+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-converter.txt
export CRATE_SECURE_COOKIE=false
python -m app.serve
```

Open `http://localhost:8080`. For HTTPS hosting keep `CRATE_SECURE_COOKIE=true` and use a stable `CRATE_SESSION_SECRET`. `CRATE_DATA_DIR` defaults to `./converter-data`; use a persistent directory. Run one Uvicorn worker. The app keeps network-destination validation, CSRF protection and HTTP request-abuse controls.

### Converter API

Start with `GET /api/session` and retain the anonymous HTTP-only cookie. Mutations require `X-Crate-Request: 1` and the same browser origin.

- `POST /api/jobs`: `{"url":"https://…","format":"mp4","quality":"best"}`
- Formats: `mp4`, `mp3`, `mkv`, `mka`. Original MKV/MKA use `quality: "best"`.
- `POST /api/music/lookup`: `{"url":"https://open.spotify.com/track/…"}` returns public recording candidates without downloading.
- `GET /api/jobs`: this browser’s queue and history.
- `POST /api/jobs/{id}/cancel`, `GET /api/jobs/{id}/file`, `DELETE /api/jobs/{id}`.
- `GET /api/health`: runtime readiness and application version.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm run test:ui
npm run typecheck
```

## Original self-hosted download manager

The Docker edition below retains the original multi-tool manager for users with their own active server. It is a separate entry point (`app.main`) from the hosted converter (`app.converter`). The existing Cloudflare Containers configuration is a **paid legacy deployment option**, not the free Render path.

## What it supports

- Direct HTTP/HTTPS/FTP files, torrents, and magnet links with **aria2**
- Video and audio from supported sites with **yt-dlp** and FFmpeg
- Image galleries with **gallery-dl**
- Direct-file fallback with **curl**
- Automatic tool selection, with a manual override
- Persistent priority queue with configurable parallel downloads
- Pause, resume, retry, cancel, and history controls
- Progress, transfer speed, ETA, file size, and free-space reporting
- Category folders such as Videos, Music, Pictures, Documents, and Archives
- Multiple links in one submission
- Optional HTTP Basic authentication
- SQLite history and restart recovery
- Responsive dark web UI with no frontend build step

## Quick start with Docker

1. Copy the example settings:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. At minimum, choose a strong password if the site will be reachable outside your LAN.

3. Start it:

   ```bash
   docker compose up -d --build
   ```

4. Open `http://YOUR-SERVER-IP:8080`.

## GitHub + Cloudflare deployment

The repository includes a GitHub Actions workflow that runs the tests and publishes multi-platform images to GitHub Container Registry. A separate production Compose stack runs that image beside Cloudflare Tunnel without exposing a host port.

See **[GitHub + Cloudflare deployment](docs/GITHUB_CLOUDFLARE.md)** for the complete setup. After configuring the tunnel and production `.env`, deployment is simply:

```bash
cd deploy
docker compose up -d
```

Cloudflare hosts the public connection, while the downloader itself remains on your Docker/Proxmox machine where it has persistent storage and access to aria2, yt-dlp, and FFmpeg.

The legacy `wrangler.jsonc` and `src/index.ts` configure Cloudflare Containers with R2. They require paid Cloudflare services and are not used by the Render deploy button.

Files are stored in `./downloads` by default and queue/history data lives in `./data`. Change `DOWNLOAD_PATH` to an absolute host path to use a larger drive:

```env
DOWNLOAD_PATH=/mnt/media/Downloads
```

The container runs as UID/GID `1000`. Make sure that account can write to the chosen download directory:

```bash
sudo chown -R 1000:1000 /mnt/media/Downloads
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOWNLOAD_PATH` | `./downloads` | Host folder mounted for completed files |
| `UDM_MAX_CONCURRENT` | `2` | Maximum simultaneous download processes |
| `UDM_USERNAME` | empty | Optional web username |
| `UDM_PASSWORD` | empty | Optional web password |
| `TZ` | `America/Chicago` | Container timezone |
| `UDM_DATA_DIR` | `/data` | Database directory inside the container |
| `UDM_DOWNLOAD_DIR` | `/downloads` | Download root inside the container |

## Cloudflare Tunnel

If you expose Crate at a domain such as `downloads.dpifiles.org`, keep its built-in username and password enabled. A typical tunnel ingress points at:

```yaml
ingress:
  - hostname: downloads.dpifiles.org
    service: http://localhost:8080
  - service: http_status:404
```

For stronger protection, also put the hostname behind Cloudflare Access. Do not expose an unauthenticated downloader to the public internet.

## Tool selection

In **Auto detect** mode, Crate routes common video sites to yt-dlp, gallery hosts to gallery-dl, torrents/magnets and ordinary file links to aria2. Choose a tool manually when a generic URL needs a particular extractor.

The first run of a resumed direct download may restart if the origin server does not support byte ranges. Torrents are stopped after downloading; Crate does not seed by default.

## Local development

Python 3.12+, `aria2c`, `curl`, and FFmpeg are recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
UDM_DATA_DIR=./data UDM_DOWNLOAD_DIR=./downloads uvicorn app.main:app --reload --port 8080
```

Run tests with `python -m pytest -q`.

## API

The browser uses a small JSON API that is also available for automations:

- `POST /api/downloads` — add one URL
- `POST /api/downloads/bulk` — add up to 100 URLs
- `GET /api/downloads` — list queue and history
- `POST /api/downloads/{id}/pause|resume|cancel`
- `PATCH /api/downloads/{id}/priority`
- `DELETE /api/downloads/{id}` — remove the history entry
- `GET /api/downloads/{id}/file` — retrieve a completed single file
- `GET /api/system` — tools, limits, and storage information

Use only downloads you are legally allowed to access, and follow the terms of each source site.
