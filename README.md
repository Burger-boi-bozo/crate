# Crate — Universal Download Manager

Crate is a self-hosted download queue with a fast web interface. Paste one link or a hundred, let Crate choose the right engine, and keep the results organized in category folders.

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/Burger-boi-bozo/crate)

The button deploys Crate directly to **Cloudflare Workers + Containers** and automatically provisions R2 persistence. Cloudflare Containers require the Workers Paid plan. During setup, choose the login username and password Cloudflare prompts you for; everything else is created automatically.

> Cloudflare mode is ideal when you want Crate hosted entirely on Cloudflare. For very large downloads, private trackers, or sites that block data-center IPs, the Docker/Proxmox deployment remains the more flexible option.

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

The button at the top is a separate, fully Cloudflare-hosted option. It runs the same FastAPI application inside a Cloudflare Container, keeps queue state in an R2-backed SQLite snapshot, and moves completed files into R2.

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
