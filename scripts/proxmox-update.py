#!/usr/bin/env python3
"""Update the existing Crate VM from the Proxmox node's root shell."""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vmid", type=int)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Use the Proxmox node's root shell.")
    states = sorted(Path("/root").glob("crate-vm-*.json"))
    if args.vmid:
        states = [p for p in states if p.name == f"crate-vm-{args.vmid}.json"]
    if len(states) != 1:
        raise RuntimeError("Select your existing Crate VM with --vmid NUMBER.")
    state = json.loads(states[0].read_text())
    vmid = str(int(state["vmid"]))
    interfaces = json.loads(run("qm", "guest", "cmd", vmid, "network-get-interfaces"))
    addresses = [a["ip-address"] for interface in interfaces for a in interface.get("ip-addresses", [])
                 if a.get("ip-address-type") == "ipv4"
                 and not ipaddress.ip_address(a["ip-address"]).is_loopback
                 and not ipaddress.ip_address(a["ip-address"]).is_link_local]
    if not addresses:
        raise RuntimeError("No guest address found. Check that the Crate VM is running.")
    address = addresses[0]
    result = json.loads(run("qm", "guest", "exec", vmid, "--timeout", "15", "--",
                            "cat", "/etc/ssh/ssh_host_ed25519_key.pub"))
    host_key = result.get("out-data", "").strip()
    if result.get("exitcode") != 0 or not host_key.startswith("ssh-ed25519 "):
        raise RuntimeError("Could not verify the VM's SSH host key.")
    with urllib.request.urlopen("https://api.github.com/repos/Burger-boi-bozo/crate/commits/main", timeout=30) as response:
        commit = json.load(response)["sha"]
    if not re.fullmatch("[a-f0-9]{40}", commit):
        raise RuntimeError("GitHub returned an invalid revision.")
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/Burger-boi-bozo/crate/{commit}/scripts/proxmox-update-guest.sh", timeout=30) as response:
        script = response.read()
    with tempfile.NamedTemporaryFile(mode="w", prefix="crate-known-hosts-") as known:
        known.write(f"{address} {host_key}\n")
        known.flush()
        print(f"Updating Crate VM {vmid} at {address} to {commit[:12]}…", flush=True)
        subprocess.run(["ssh", "-i", state["ssh_key"], "-o", "BatchMode=yes",
                        "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", f"UserKnownHostsFile={known.name}", f"crate-admin@{address}",
                        "sudo", "bash", "-s", "--", commit], input=script, check=True)
    print("Crate updated. Refresh your website. Your existing tunnel and domain are preserved.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit("Update stopped: " + (getattr(exc, "stderr", None) or str(exc)))
