#!/usr/bin/env python3
"""Small dependency-free CLI for Crate v6 API tokens."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def request(base: str, token: str, path: str, method="GET", body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "X-Crate-Request": "1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        try: detail = json.load(exc).get("detail")
        except Exception: detail = exc.reason
        raise SystemExit(f"Crate API error {exc.code}: {detail}") from exc
    return response if raw else json.load(response)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="crate", description="Send work to a Crate v6 server")
    root.add_argument("--server", default=os.getenv("CRATE_URL", "https://down.dpifiles.org"))
    root.add_argument("--token", default=os.getenv("CRATE_TOKEN", ""))
    sub = root.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Create one conversion")
    add.add_argument("url")
    add.add_argument("--format", default="mp4")
    add.add_argument("--quality", default="best")
    add.add_argument("--priority", choices=["low","normal","high"], default="normal")
    add.add_argument("--start", type=float)
    add.add_argument("--end", type=float)

    jobs = sub.add_parser("jobs", help="List this token's jobs")
    jobs.add_argument("--status", default="")
    jobs.add_argument("--query", default="")

    download = sub.add_parser("download", help="Download a ready job")
    download.add_argument("job_id")
    download.add_argument("--output", type=Path)

    priority = sub.add_parser("priority", help="Change a queued job priority")
    priority.add_argument("job_id")
    priority.add_argument("value", choices=["low","normal","high"])

    cancel = sub.add_parser("cancel", help="Cancel a job")
    cancel.add_argument("job_id")
    return root


def main() -> int:
    args = parser().parse_args()
    if not args.token:
        raise SystemExit("Set CRATE_TOKEN or pass --token. Create API tokens in /admin.")
    if args.command == "add":
        body = {"url": args.url, "format": args.format, "quality": args.quality, "priority": args.priority}
        if args.start is not None: body["start"] = args.start
        if args.end is not None: body["end"] = args.end
        result = request(args.server, args.token, "/api/jobs", "POST", body)
        print(json.dumps(result, indent=2)); return 0
    if args.command == "jobs":
        from urllib.parse import urlencode
        query = urlencode({"status": args.status, "q": args.query})
        print(json.dumps(request(args.server, args.token, "/api/jobs?" + query), indent=2)); return 0
    if args.command == "priority":
        result = request(args.server, args.token, f"/api/jobs/{args.job_id}/priority", "POST", {"priority": args.value})
        print(json.dumps(result, indent=2)); return 0
    if args.command == "cancel":
        result = request(args.server, args.token, f"/api/jobs/{args.job_id}/cancel", "POST", {})
        print(json.dumps(result, indent=2)); return 0
    if args.command == "download":
        response = request(args.server, args.token, f"/api/jobs/{args.job_id}/file", raw=True)
        name = args.output
        if name is None:
            disposition = response.headers.get("Content-Disposition", "")
            import re
            match = re.search(r'filename="?([^";]+)', disposition)
            name = Path(match.group(1) if match else f"crate-{args.job_id}")
        with name.open("wb") as stream:
            while chunk := response.read(1024 * 1024): stream.write(chunk)
        print(name); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
