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
set_build_ref() {
  local revision="$1"
  sed -i '/^CRATE_BUILD_SHA=/d' /etc/crate/environment
  printf 'CRATE_BUILD_SHA=%s\n' "$revision" >> /etc/crate/environment
}
ensure_admin_password() {
  local password
  install -d -m 700 /etc/crate
  if ! grep -q '^CRATE_ADMIN_PASSWORD=' /etc/crate/environment; then
    password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    printf 'CRATE_ADMIN_PASSWORD=%s\n' "$password" >> /etc/crate/environment
  else
    password="$(sed -n 's/^CRATE_ADMIN_PASSWORD=//p' /etc/crate/environment | head -1)"
  fi
  printf '%s\n' "$password" > /etc/crate/admin-password
  chmod 600 /etc/crate/admin-password
}
rollback() {
  trap - ERR
  echo "Update failed; restoring the previous app revision."
  git checkout --detach "$old_ref"
  set_build_ref "$old_ref"
  .venv/bin/pip install -r requirements.txt
  systemctl restart crate
}
trap rollback ERR
git checkout --detach "$ref"
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/check_runtime.py
set_build_ref "$ref"
ensure_admin_password
mkdir -p /etc/systemd/system/crate.service.d
cat > /etc/systemd/system/crate.service.d/download-resources.conf <<'UNIT'
[Service]
MemoryMax=infinity
TasksMax=infinity
UNIT
systemctl daemon-reload
systemctl start crate
.venv/bin/python - "$ref" <<'PY'
import json, sys, time, urllib.request
revision = sys.argv[1]
for attempt in range(45):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=2) as response:
            health = json.load(response)
        assert health["status"] == "ok"
        assert health["runtime"] == "crate-v5"
        assert health["build"] == revision[:12]
        print("Verified Crate v5 health at", revision[:12])
        break
    except Exception:
        if attempt == 44:
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
