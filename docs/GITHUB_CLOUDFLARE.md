# GitHub + Cloudflare deployment

## One-click: run entirely on Cloudflare

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/Burger-boi-bozo/crate)

This option deploys the Worker, Durable Object, Container, and R2 bucket from the repository. It requires the Cloudflare Workers Paid plan. Cloudflare will ask you to choose a Crate username and password during setup.

The rest of this guide covers the alternative Proxmox + Cloudflare Tunnel architecture.

This setup uses each service for what it is good at:

- **GitHub** stores the source and automatically builds a Linux container image.
- **Your Proxmox/Docker server** runs Crate and stores the downloaded files.
- **Cloudflare Tunnel** publishes the web UI without port forwarding or exposing the server's IP.

Cloudflare Pages and standard Worker isolates cannot run Crate by themselves because they do not provide system binaries such as aria2 and FFmpeg. The one-click option solves that by using a Cloudflare Container behind the Worker.

## 1. Create and push the GitHub repository

Create an empty GitHub repository named `crate`, then run these commands in the extracted project folder:

```bash
git init
git add .
git commit -m "Initial Crate release"
git branch -M main
git remote add origin https://github.com/YOUR-GITHUB-USERNAME/crate.git
git push -u origin main
```

The included workflow tests the app and publishes these images to GitHub Container Registry:

```text
ghcr.io/YOUR-GITHUB-USERNAME/crate:latest
ghcr.io/YOUR-GITHUB-USERNAME/crate:sha-...
```

In the repository's **Packages** area, open the new package and set its visibility to **Public** if you want the Proxmox server to pull it without GitHub credentials. The repository itself can remain private if you prefer; in that case, sign in to `ghcr.io` on the server using a GitHub personal access token with `read:packages`.

## 2. Create the Cloudflare Tunnel

1. In the Cloudflare dashboard, open **Networking → Tunnels**.
2. Create a remotely managed tunnel named `crate`.
3. Choose the Docker connector and copy only the long token beginning with `eyJ` from the displayed command.
4. Add a published application:
   - Subdomain: `downloads` (or any name you prefer)
   - Domain: `dpifiles.org`
   - Service type: `HTTP`
   - URL: `crate:8080`

The service URL uses the Docker service name, not `localhost`, because `cloudflared` and Crate share a private Docker network.

## 3. Start it on the server

Copy only the `deploy` folder to the server, then:

```bash
cd deploy
cp .env.example .env
nano .env
docker compose up -d
docker compose logs -f
```

Set these values in `.env` before starting:

- `CRATE_IMAGE` to your exact lowercase GHCR image name.
- `CLOUDFLARE_TUNNEL_TOKEN` to the token from step 2.
- `UDM_USERNAME` and `UDM_PASSWORD` to strong credentials.
- `DOWNLOAD_PATH` to the host drive where files should be stored.

No router port forwarding is required. The application container has no published host port in the production Compose file; only `cloudflared` can reach it through the private Docker network.

## 4. Install updates

Every push to `main` runs tests and publishes a new `latest` image. On the server, update with:

```bash
cd deploy
./update.sh
```

If the script is not executable after being copied from Windows:

```bash
chmod +x update.sh
```

Your data survives container replacements because the database and downloads are bind-mounted outside the container.

## Optional: Cloudflare Access

For another authentication layer, create a Cloudflare Access self-hosted application for your hostname and allow only your email address. Keep Crate's own password enabled as defense in depth.
