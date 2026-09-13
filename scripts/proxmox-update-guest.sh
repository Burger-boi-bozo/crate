#!/bin/bash
set -euo pipefail
exec 9>/run/lock/crate-update.lock
flock -n 9 || { echo "Another Crate update is already running."; exit 0; }
ref="${1:?Missing revision}"
[[ "$ref" =~ ^[a-f0-9]{40}$ ]] || exit 1
cd /opt/crate
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Local code changes found in /opt/crate. Preserve those changes before updating."
  exit 1
fi
old_ref="$(git rev-parse HEAD)"
git fetch origin "$ref"
git cat-file -e "$ref^{commit}"
systemctl stop crate
rollback() {
  trap - ERR
  echo "Update failed; restoring the previous app revision."
  git checkout --detach "$old_ref"
  systemctl restart crate
}
trap rollback ERR
git checkout --detach "$ref"
.venv/bin/pip install -r requirements-converter.txt
.venv/bin/python scripts/check_runtime.py
# Remove only the fixed resource ceilings supplied by Crate's original installer.
mkdir -p /etc/systemd/system/crate.service.d
cat > /etc/systemd/system/crate.service.d/download-resources.conf <<'UNIT'
[Service]
MemoryMax=infinity
TasksMax=infinity
UNIT
systemctl daemon-reload
systemctl start crate
.venv/bin/python - <<'PY'
import json, time, urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=2) as response:
            health = json.load(response)
        assert health["status"] == "ok" and health["version"] == "self-hosted-quality-2"
        print("Verified: app healthy, quality update active, no access code.")
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(1)
PY
install -m 0755 scripts/proxmox-auto-update.py /usr/local/sbin/crate-auto-update.py
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
OnBootSec=3min
OnUnitActiveSec=5min
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now crate-auto-update.timer
trap - ERR
