"""Network-guarded webhook delivery helper for Crate v6."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from app.media_policy import install_network_guard, validate_url


def main(spec_path: Path) -> None:
    spec = json.loads(spec_path.read_text())
    url = validate_url(str(spec["url"]), source=False)
    payload = json.dumps(spec["payload"], separators=(",", ":")).encode()
    install_network_guard()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Crate-v6-webhook"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(spec.get("timeout") or 10)) as response:
            print(json.dumps({"ok": True, "status": int(response.status)}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:240]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
