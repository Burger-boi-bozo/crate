#!/usr/bin/env python3
"""Small live-source canary used before adopting yt-dlp updates."""
from __future__ import annotations

import json
import subprocess
import sys

CASES = [
    ("youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("direct-mp4", "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"),
]


def preview(url: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "app.preview_lookup", url],
        capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-500:] or f"preview exited {result.returncode}")
    data = json.loads(result.stdout)
    if not data.get("title"):
        raise RuntimeError("preview returned no title")
    return data


def main() -> int:
    failed = []
    for name, url in CASES:
        try:
            data = preview(url)
            print(f"PASS {name}: {data.get('title')} · {data.get('source')}")
        except Exception as exc:
            failed.append((name, str(exc)))
            print(f"FAIL {name}: {exc}", file=sys.stderr)
    if failed:
        print("\nProvider canary failed:", file=sys.stderr)
        for name, error in failed:
            print(f"- {name}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
