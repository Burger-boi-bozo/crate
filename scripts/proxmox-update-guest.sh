#!/bin/bash
set -euo pipefail
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
trap - ERR
