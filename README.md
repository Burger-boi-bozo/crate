# Crate — A link. A file. Done.

Paste a public media link, choose **MP4 video** or **MP3 audio**, and save the converted file. The hosted edition is a small shared tool for lessons, presentations, and offline viewing. It runs on one Render Free Python service; your home server can stay off.

**Moving to Proxmox?** The [Proxmox installer](docs/PROXMOX.md) creates a separate Debian VM for this same converter and guides Cloudflare Tunnel setup. Run it from your Proxmox node's Shell.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Burger-boi-bozo/crate)

The button loads `render.yaml`, creates the free service, and registers `down.dpifiles.org` as its custom domain. The owner needs a Render account and DNS configuration. Visitors can paste a link immediately: no account or access code is required.

**Keep the hosting cost at $0:** use the Free instance in a Hobby workspace **without a payment method**. Render can charge bandwidth and build overages when a payment method is present; without one, it suspends free services/builds at the limit. The app's limits reduce usage but are not a billing guarantee. See [Render's free-service limits](https://render.com/docs/free).

## Hosted converter

- Public media links handled by yt-dlp, with FFmpeg for MP4 remuxing/audio encoding and MP3 conversion.
- MP4 up to 1080p, with H.264 passthrough or conversion from other video codecs; MP3 at 128 kbps. Lower-resolution sources are never upscaled.
- One conversion at a time, five queued/active jobs globally, two per browser.
- Clips up to 10 minutes and output files up to 100 MB; ten submissions per rolling day while the process runs.
- Automatic anonymous browser sessions, private download URLs, cancellation, and video/audio filters.
- Up to 50 recent history entries stored in the current browser. Clear history deletes completed server files as well.
- Files expire after one hour, or sooner if Render sleeps/restarts. Each file allows three download requests.
- No R2, paid database, persistent disk, Redis, cron job, or always-on home computer.

YouTube and other sites can block cloud-server downloads, require sign-in, or stop working when they change. This app does not bypass those restrictions, and it cannot guarantee every link. Playlists, live streams, private and protected media are excluded. Save files to your device when they are ready.

The hosted converter accepts public HTTP(S) links across yt-dlp's site extractors, plus its generic embedded/direct-media extractor. Examples include YouTube, Vimeo, TikTok, Instagram, Facebook, Reddit, X, SoundCloud, Bandcamp, and Apple Podcasts. See the [upstream supported-site list](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md); listing a site does not guarantee hosted access to it. Spotify and Apple Music subscription tracks cannot be exported to full MP3 files here; the app explains that limitation before queuing and never substitutes a different recording or preview.

See **[free deployment and domain setup](docs/RENDER_FREE.md)** and **[the ten hosting alternatives reviewed](docs/HOSTING_OPTIONS.md)**.

### Run the converter locally

Use Python 3.12+, FFmpeg (including ffprobe), and Node.js 22+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-converter.txt
export CRATE_SECURE_COOKIE=false  # local HTTP only; keep true for hosted HTTPS
python -m app.serve
```

Open `http://localhost:8080`. The queue and media are temporary. `CRATE_DATA_DIR` defaults to `./converter-data`; `CRATE_SESSION_SECRET` should be a stable secret on a hosted service. Run a single Uvicorn worker because the queue is held in that process.

### Converter API

Mutating requests require `X-Crate-Request: 1`; browser requests must have the same origin. Start an anonymous session and keep the returned HTTP-only cookie. This is browser ownership, not an access-code gate. Any old `CRATE_ACCESS_CODE` environment variable is ignored.

- `GET /api/session` — create/refresh an anonymous browser session and return limits
- `POST /api/session` — the same, with no code or body required
- `POST /api/jobs` with `{"url":"https://…","format":"mp4"}`
- `GET /api/jobs` — only the current browser's conversions
- `POST /api/jobs/{id}/cancel`
- `GET /api/jobs/{id}/file` — download the completed file
- `DELETE /api/jobs/{id}` — cancel/remove the job and its file
- `DELETE /api/session` — discard the browser session
- `GET /api/health` — runtime readiness

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
