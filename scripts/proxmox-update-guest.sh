#!/bin/bash
set -euo pipefail
exec 9>/run/lock/crate-update.lock
flock -n 9 || { echo "Another Crate update is already running."; exit 0; }

ref="${1:?Missing revision}"
[[ "$ref" =~ ^[a-f0-9]{40}$ ]] || { echo "Invalid revision"; exit 1; }
base_repo=/opt/crate
release_root=/opt/crate-releases
release_dir="$release_root/$ref"
short="${ref:0:12}"
mkdir -p "$release_root" /var/lib/crate/backups

if [[ -L /opt/crate-current ]]; then
  old_target="$(readlink -f /opt/crate-current)"
else
  old_target="$base_repo"
fi
old_ref="$(git -C "$old_target" rev-parse HEAD)"

if [[ -n "$(git -C "$base_repo" status --porcelain --untracked-files=no)" ]]; then
  echo "Local tracked changes found in $base_repo. Preserve them before updating."
  exit 1
fi

git -C "$base_repo" fetch origin "$ref"
git -C "$base_repo" cat-file -e "$ref^{commit}"
git -C "$base_repo" worktree prune
if [[ -e "$release_dir" ]]; then
  git -C "$base_repo" worktree remove --force "$release_dir" 2>/dev/null || rm -rf "$release_dir"
fi
git -C "$base_repo" worktree add --detach "$release_dir" "$ref"
python3 -m venv "$release_dir/.venv"
"$release_dir/.venv/bin/pip" install -q --disable-pip-version-check -r "$release_dir/requirements.txt"
"$release_dir/.venv/bin/python" "$release_dir/scripts/check_runtime.py"

set_env() {
  local key="$1" value="$2"
  sed -i "/^${key}=/d" /etc/crate/environment
  printf '%s=%s\n' "$key" "$value" >> /etc/crate/environment
}

ensure_runtime_env() {
  install -d -m 700 /etc/crate
  set_env CRATE_BUILD_SHA "$ref"
  set_env CRATE_TTL 86400
  set_env CRATE_WORKERS 3
  set_env CRATE_HEAVY_WORKERS 1
  set_env CRATE_FFMPEG_THREADS 2
  set_env CRATE_MIN_AVAILABLE_MEMORY 805306368
  set_env CRATE_MAX_LOAD_RATIO 1.25
  set_env CRATE_FRAGMENT_CONCURRENCY 3
  set_env CRATE_DOWNLOAD_RETRIES 4
}
rollback() {
  trap - ERR
  echo "Production health failed; rolling back to ${old_ref:0:12}."
  ln -sfn "$old_target" /opt/crate-current.next
  mv -Tf /opt/crate-current.next /opt/crate-current
  set_env CRATE_BUILD_SHA "$old_ref"
  systemctl daemon-reload
  systemctl restart crate
  exit 1
}
trap rollback ERR

ensure_runtime_env
chmod 600 /etc/crate/environment

# Back up the live SQLite database before the release can touch it.
if [[ -f /var/lib/crate/media/crate.db ]]; then
  "$release_dir/.venv/bin/python" - "$short" <<'PY'
import sqlite3, sys
from pathlib import Path
src = Path('/var/lib/crate/media/crate.db')
dst = Path('/var/lib/crate/backups') / f'pre-{sys.argv[1]}.sqlite'
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
dst.chmod(0o600)
PY
fi
green_data="/var/lib/crate/green-$short"
rm -rf "$green_data"
install -d -o crate -g crate -m 700 "$green_data"
cat > /etc/systemd/system/crate-green.service <<UNIT
[Unit]
Description=Crate candidate health gate
After=network-online.target
[Service]
User=crate
Group=crate
WorkingDirectory=$release_dir
Environment=PORT=18082
Environment=CRATE_BIND_HOST=127.0.0.1
Environment=CRATE_DATA_DIR=$green_data
Environment=CRATE_SECURE_COOKIE=false
Environment=CRATE_BUILD_SHA=$ref
Environment=CRATE_WORKERS=3
Environment=CRATE_HEAVY_WORKERS=1
Environment=CRATE_FFMPEG_THREADS=2
Environment=CRATE_MIN_AVAILABLE_MEMORY=805306368
Environment=CRATE_MAX_LOAD_RATIO=1.25
ExecStart=$release_dir/.venv/bin/python -m app.serve
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
UMask=0077
UNIT
systemctl daemon-reload
systemctl start crate-green
"$release_dir/.venv/bin/python" - "$ref" <<'PY'
import json, sys, time, urllib.request
revision = sys.argv[1]
for attempt in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18082/api/health', timeout=2) as response:
            health = json.load(response)
        assert health['status'] == 'ok'
        assert health['runtime'] == 'crate-v6'
        assert health['build'] == revision[:12]
        with urllib.request.urlopen('http://127.0.0.1:18082/api/capabilities', timeout=2) as response:
            capabilities = json.load(response)
        assert capabilities['version'] == '6.0.0' and capabilities['features']['advanced'] is True
        print('Candidate health gate passed:', revision[:12])
        break
    except Exception:
        if attempt == 44: raise
        time.sleep(1)
PY
systemctl stop crate-green
rm -f /etc/systemd/system/crate-green.service
rm -rf "$green_data"
systemctl daemon-reload
cat > /etc/systemd/system/crate.service <<'UNIT'
[Unit]
Description=Crate media converter
After=network-online.target
Wants=network-online.target
[Service]
User=crate
Group=crate
WorkingDirectory=/opt/crate-current
EnvironmentFile=/etc/crate/environment
ExecStart=/opt/crate-current/.venv/bin/python -m app.serve
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/crate
MemoryHigh=2800M
MemoryMax=3200M
TasksMax=1024
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT

ln -sfn "$release_dir" /opt/crate-current.next
mv -Tf /opt/crate-current.next /opt/crate-current
systemctl daemon-reload
systemctl restart crate
"$release_dir/.venv/bin/python" - "$ref" <<'PY'
import json, sys, time, urllib.request
revision = sys.argv[1]
for attempt in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2) as response:
            health = json.load(response)
        assert health['status'] == 'ok'
        assert health['runtime'] == 'crate-v6'
        assert health['build'] == revision[:12]
        print('Production health passed:', revision[:12])
        break
    except Exception:
        if attempt == 44: raise
        time.sleep(1)
PY

"$release_dir/.venv/bin/python" - "$old_ref" "$ref" <<'PY'
import json, sys, time
from pathlib import Path
path = Path('/var/lib/crate/deploy-history.json')
old, current = sys.argv[1:3]
data = {'previous': old, 'current': current, 'deployed_at': time.time()}
path.write_text(json.dumps(data, indent=2) + '\n')
path.chmod(0o600)
PY

install -m 0755 "$release_dir/scripts/proxmox-auto-update.py" /usr/local/sbin/crate-auto-update.py
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
trap - ERR

echo "Crate blue-green update complete: ${old_ref:0:12} -> ${ref:0:12}"
