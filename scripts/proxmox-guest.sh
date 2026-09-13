#!/bin/bash
set -euo pipefail
: "${CRATE_REF:?Missing pinned repository revision}"
install -d /var/lib/crate
echo installing > /var/lib/crate/bootstrap-status
trap 'echo failed > /var/lib/crate/bootstrap-status' ERR
install -d /opt/node22
crate_node_tmp=$(mktemp -d)
curl --fail --silent --show-error --location --retry 3 https://nodejs.org/dist/v22.22.0/SHASUMS256.txt -o "$crate_node_tmp/SHASUMS256.txt"
curl --fail --silent --show-error --location --retry 3 https://nodejs.org/dist/v22.22.0/node-v22.22.0-linux-x64.tar.xz -o "$crate_node_tmp/node-v22.22.0-linux-x64.tar.xz"
python3 - "$crate_node_tmp" <<'PY'
import hashlib, sys
from pathlib import Path
directory = Path(sys.argv[1])
name = 'node-v22.22.0-linux-x64.tar.xz'
expected = next(line.split()[0] for line in (directory / 'SHASUMS256.txt').read_text().splitlines() if line.split()[-1] == name)
with (directory / name).open('rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected, 'Node checksum mismatch'
PY
tar -xJf "$crate_node_tmp/node-v22.22.0-linux-x64.tar.xz" --strip-components=1 -C /opt/node22
ln -sf /opt/node22/bin/node /usr/local/bin/node
rm -rf "$crate_node_tmp"
id crate >/dev/null 2>&1 || useradd --system --home-dir /var/lib/crate --shell /usr/sbin/nologin crate
install -d -o crate -g crate /var/lib/crate/media
git init /opt/crate
git -C /opt/crate remote add origin https://github.com/Burger-boi-bozo/crate.git
git -C /opt/crate fetch --depth 1 origin "$CRATE_REF"
git -C /opt/crate checkout --detach FETCH_HEAD
python3 -m venv /opt/crate/.venv
/opt/crate/.venv/bin/pip install -r /opt/crate/requirements.txt
/opt/crate/.venv/bin/python /opt/crate/scripts/check_runtime.py
install -d -m 700 /etc/crate
crate_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
crate_admin_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
cat > /etc/crate/environment <<EOF
PORT=8080
CRATE_DATA_DIR=/var/lib/crate/media
CRATE_SECURE_COOKIE=false
CRATE_HOSTING=proxmox
CRATE_SESSION_SECRET=$crate_secret
CRATE_ADMIN_PASSWORD=$crate_admin_password
CRATE_BUILD_SHA=$CRATE_REF
CRATE_WORKERS=2
CRATE_FRAGMENT_CONCURRENCY=4
CRATE_DOWNLOAD_RETRIES=4
CRATE_FFMPEG_THREADS=0
CRATE_STALL_TIMEOUT=300
CRATE_PREVIEW_TIMEOUT=20
EOF
chmod 600 /etc/crate/environment
printf '%s\n' "$crate_admin_password" > /etc/crate/admin-password
chmod 600 /etc/crate/admin-password
cat > /etc/systemd/system/crate.service <<'UNIT'
[Unit]
Description=Crate media converter
After=network-online.target
Wants=network-online.target
[Service]
User=crate
Group=crate
WorkingDirectory=/opt/crate
EnvironmentFile=/etc/crate/environment
ExecStart=/opt/crate/.venv/bin/python -m app.serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/crate
MemoryMax=infinity
TasksMax=infinity
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now crate
install -m 0755 /opt/crate/scripts/proxmox-auto-update.py /usr/local/sbin/crate-auto-update.py
cat > /etc/systemd/system/crate-auto-update.service <<'UNIT'
[Unit]
Description=Update Crate after GitHub Actions passes
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/crate-auto-update.py
UNIT
cat > /etc/systemd/system/crate-auto-update.timer <<'UNIT'
[Unit]
Description=Check GitHub for tested Crate updates
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
RandomizedDelaySec=15
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now crate-auto-update.timer
curl --fail --silent --show-error --location --retry 3 \
  https://github.com/cloudflare/cloudflared/releases/download/2026.9.1/cloudflared-linux-amd64 \
  --output /usr/local/bin/cloudflared
echo '03f1f25d1cc93b9ad6c60569d44060bc4f17ed97075760ed8cfca4b12dcd68cc  /usr/local/bin/cloudflared' | sha256sum --check
chmod 755 /usr/local/bin/cloudflared
python3 - "$CRATE_REF" <<'PY'
import http.cookiejar, json, sys, time, urllib.request
expected = sys.argv[1][:12]
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def api(path, body=None):
    req = urllib.request.Request('http://127.0.0.1:8080' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'X-Crate-Request':'1', 'Content-Type':'application/json'})
    with client.open(req, timeout=30) as response: return json.load(response)
for _ in range(30):
    try:
        health = api('/api/health')
        assert health['status'] == 'ok' and health['runtime'] == 'crate-v4' and health['build'] == expected
        break
    except Exception: time.sleep(2)
else: raise RuntimeError('Crate did not become healthy')
assert api('/api/session')['access_code_required'] is False
job = api('/api/jobs', {'url':'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', 'format':'mp4'})
for _ in range(60):
    job = next(item for item in api('/api/jobs') if item['id'] == job['id'])
    if job['status'] == 'failed': raise RuntimeError(job['error'])
    if job['status'] == 'ready': break
    time.sleep(3)
else: raise RuntimeError('Media smoke test timed out')
with client.open('http://127.0.0.1:8080/api/jobs/' + job['id'] + '/file', timeout=30) as response:
    content = response.read(2 * 1024 * 1024)
    assert len(content) == job['size'] and b'ftyp' in content[:32]
print('Crate v4 public-media download verified:', job['width'], 'x', job['height'])
PY
echo ready > /var/lib/crate/bootstrap-status
