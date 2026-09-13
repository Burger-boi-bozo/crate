#!/usr/bin/env python3
"""Update Crate after GitHub Actions passes for the newest main commit."""
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

REPO = "Burger-boi-bozo/crate"
API = f"https://api.github.com/repos/{REPO}"


def fetch_json(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "crate-proxmox-auto-update",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def approved(runs, revision):
    return any(
        run.get("head_sha") == revision
        and run.get("path") == ".github/workflows/publish.yml"
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        for run in runs
    )


def main():
    current = subprocess.run(
        ["git", "-C", "/opt/crate", "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    target = fetch_json(API + "/commits/main")["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", target):
        raise RuntimeError("GitHub returned an invalid revision.")
    if target == current:
        print("Crate is current:", current[:12])
        return
    query = urllib.parse.urlencode({"head_sha": target, "event": "push", "per_page": 10})
    runs = fetch_json(API + "/actions/runs?" + query).get("workflow_runs", [])
    if not approved(runs, target):
        print("Waiting for successful GitHub Actions checks:", target[:12])
        return
    raw = f"https://raw.githubusercontent.com/{REPO}/{target}/scripts/proxmox-update-guest.sh"
    request = urllib.request.Request(raw, headers={"User-Agent": "crate-proxmox-auto-update"})
    with urllib.request.urlopen(request, timeout=30) as response:
        script = response.read(256 * 1024)
    if not script.startswith(b"#!/bin/bash\n") or len(script) >= 256 * 1024:
        raise RuntimeError("The updater script was missing or invalid.")
    with tempfile.NamedTemporaryFile(prefix="crate-update-", suffix=".sh") as update:
        update.write(script)
        update.flush()
        subprocess.run(["bash", update.name, target], check=True, timeout=1800)
    print("Crate automatically updated:", current[:12], "->", target[:12])


if __name__ == "__main__":
    main()
