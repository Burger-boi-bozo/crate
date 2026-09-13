#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run this with sudo inside the Crate VM.'; exit 1; }
read -r -p 'Public hostname you own (for example down.dpifiles.org): ' crate_hostname
python3 - "$crate_hostname" <<'PY'
import re, sys
value = sys.argv[1]
if len(value) > 253 or len(value.split('.')) < 3 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in value.split('.')):
    raise SystemExit('Enter a lowercase subdomain only, without https://, a port, or a path.')
PY
curl --fail --silent http://127.0.0.1:8080/api/health >/dev/null
install -d -m 700 /etc/cloudflared
if [[ ! -f /root/.cloudflared/cert.pem ]]; then
  echo 'Open the Cloudflare authorization URL below in your own browser and select the domain you own.'
  cloudflared tunnel login
fi
if [[ ! -f /etc/cloudflared/crate.json ]]; then
  cloudflared tunnel create --credentials-file /etc/cloudflared/crate.json "crate-down-$(date +%s)"
fi
crate_tunnel_id=$(python3 -c 'import json; print(json.load(open("/etc/cloudflared/crate.json"))["TunnelID"])')
python3 - "$crate_tunnel_id" "$crate_hostname" <<'PY'
import json, sys, uuid
from pathlib import Path
tunnel = str(uuid.UUID(sys.argv[1]))
config = {'tunnel':tunnel, 'credentials-file':'/etc/cloudflared/crate.json',
          'ingress':[{'hostname':sys.argv[2], 'service':'http://localhost:8080'}, {'service':'http_status:404'}]}
Path('/etc/cloudflared/config.yml').write_text(json.dumps(config, indent=2))
PY
chmod 600 /etc/cloudflared/crate.json /etc/cloudflared/config.yml
# No overwrite flag: conflicting DNS records must be reviewed before cutover.
cloudflared tunnel route dns "$crate_tunnel_id" "$crate_hostname"
sed -i 's/^CRATE_SECURE_COOKIE=.*/CRATE_SECURE_COOKIE=true/' /etc/crate/environment
systemctl restart crate
cat > /etc/systemd/system/crate-tunnel.service <<'UNIT'
[Unit]
Description=Cloudflare Tunnel for Crate
After=network-online.target crate.service
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now crate-tunnel
echo "Tunnel started for https://$crate_hostname. Test a download there before removing Render."
