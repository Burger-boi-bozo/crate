#!/usr/bin/env python3
"""Run as root on Proxmox VE. Creates a new VM; never replaces an existing VM."""
import argparse
import hashlib
import ipaddress
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "Burger-boi-bozo/crate"
IMAGE_ROOT = "https://cloud.debian.org/images/cloud/trixie/latest"
IMAGE = "debian-13-genericcloud-amd64.qcow2"


def run(*args, check=True, timeout=120):
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=timeout)


def api(path, *args):
    return json.loads(run("pvesh", "get", path, *args, "--output-format", "json").stdout)


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def choose_storage(rows, requested, disk_gb):
    eligible = [s for s in rows if s.get("active") and "images" in s.get("content", "").split(",")
                and int(s.get("avail", 0)) >= disk_gb * 1024**3]
    if requested:
        eligible = [s for s in eligible if s["storage"] == requested]
    eligible.sort(key=lambda s: (s["storage"] not in {"local-lvm", "local-zfs"}, s.get("shared", 0), s["storage"]))
    if not eligible:
        raise RuntimeError("No active VM storage has enough free space. Set --storage or free disk space.")
    return eligible[0]["storage"]


def guest(vmid, *args):
    result = json.loads(run("qm", "guest", "exec", str(vmid), "--timeout", "15", "--", *args, timeout=25).stdout)
    if result.get("exitcode") != 0:
        raise RuntimeError(result.get("err-data") or "Guest command is not ready")
    return result.get("out-data", "").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Read-only preflight; create nothing")
    parser.add_argument("--storage")
    parser.add_argument("--bridge", default="vmbr0")
    parser.add_argument("--vmid", type=int)
    parser.add_argument("--memory", type=int, default=2048)
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--disk", type=int, default=24)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.machine() != "x86_64":
        raise RuntimeError("Run this in the root Shell of an x86-64 Proxmox node.")
    for command in ("pvesh", "pvesm", "qm", "ip", "ssh", "ssh-keygen"):
        if not shutil.which(command):
            raise RuntimeError(f"Missing {command}; use the Proxmox node Shell, not your Mac terminal.")
    if args.memory < 2048 or args.cores < 1 or args.disk < 16:
        raise RuntimeError("Use at least 2048 MB RAM, one CPU core, and 16 GB disk.")
    available = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))) // 1024
    if available < args.memory + 512:
        raise RuntimeError("Insufficient available RAM for the VM plus host headroom.")
    links = json.loads(run("ip", "-j", "link", "show", "dev", args.bridge).stdout)
    if not links or links[0].get("linkinfo", {}).get("info_kind") != "bridge":
        raise RuntimeError("Select an existing Linux bridge with --bridge. No host networking will be changed.")
    node = socket.gethostname().split(".")[0]
    storage = choose_storage(api(f"/nodes/{node}/storage", "--enabled", "1"), args.storage, args.disk)
    vmid = args.vmid if args.vmid is not None else int(api("/cluster/nextid"))
    if vmid < 100 or any(int(v["vmid"]) == vmid for v in api("/cluster/resources", "--type", "vm")):
        raise RuntimeError("That VM ID is invalid or already exists. Nothing was changed.")
    plan = {"vmid": vmid, "name": "crate-down", "node": node, "storage": storage,
            "bridge": args.bridge, "ram_mb": args.memory, "cores": args.cores, "disk_gb": args.disk,
            "network": "DHCP", "os": "Debian 13", "changes_existing_vms": False}
    print(json.dumps(plan, indent=2), flush=True)
    if args.plan:
        return

    # Pin every guest file to the same repository commit for this installation.
    ref = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/main"))["sha"]
    raw = f"https://raw.githubusercontent.com/{REPO}/{ref}"
    bootstrap = fetch(raw + "/scripts/proxmox-guest.sh").decode()
    tunnel = fetch(raw + "/scripts/connect-cloudflare.sh").decode()
    work = Path(f"/var/lib/vz/crate-bootstrap-{vmid}")
    key = Path(f"/root/.ssh/crate-vm-{vmid}")
    if work.exists() or key.exists():
        raise RuntimeError("Setup files already exist for this ID. Inspect them or choose a new --vmid.")
    work.mkdir(mode=0o700)
    key.parent.mkdir(mode=0o700, exist_ok=True)
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
    cloud = {
        "hostname": "crate-down", "manage_etc_hosts": True, "ssh_pwauth": False, "disable_root": True,
        "users": [{"name": "crate-admin", "groups": ["sudo"], "shell": "/bin/bash", "lock_passwd": True,
                   "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [key.with_suffix(".pub").read_text().strip()]}],
        "package_update": True,
        "packages": ["qemu-guest-agent", "git", "curl", "ca-certificates", "python3-venv", "ffmpeg", "xz-utils"],
        "write_files": [
            {"path": "/root/crate-bootstrap.sh", "permissions": "0700", "content": bootstrap},
            {"path": "/usr/local/sbin/crate-connect-cloudflare", "permissions": "0700", "content": tunnel}],
        "runcmd": [["systemctl", "enable", "--now", "qemu-guest-agent"],
                   ["env", f"CRATE_REF={ref}", "bash", "/root/crate-bootstrap.sh"]],
    }
    snippets = work / "snippets"
    snippets.mkdir()
    (snippets / "user.yaml").write_text("#cloud-config\n" + json.dumps(cloud, indent=2) + "\n")
    snippet_storage = f"crate-ci-{vmid}"
    with tempfile.TemporaryDirectory(prefix="crate-image-") as temporary:
        image = Path(temporary) / IMAGE
        checksums = fetch(IMAGE_ROOT + "/SHA512SUMS").decode()
        expected = next((line.split()[0] for line in checksums.splitlines()
                         if len(line.split()) == 2 and line.split()[1].lstrip("*").removeprefix("./") == IMAGE), None)
        if not expected:
            raise RuntimeError("The official Debian checksum manifest did not contain the requested image.")
        print("Downloading and verifying Debian's cloud image…", flush=True)
        with urllib.request.urlopen(IMAGE_ROOT + "/" + IMAGE, timeout=120) as response, image.open("wb") as target:
            shutil.copyfileobj(response, target)
        with image.open("rb") as source:
            actual = hashlib.file_digest(source, "sha512").hexdigest()
        if actual != expected:
            raise RuntimeError("Debian image checksum mismatch. No VM was created.")
        # A newly chosen ID is created atomically; a concurrent allocation fails safely.
        run("qm", "create", str(vmid), "--name", "crate-down", "--memory", str(args.memory),
            "--cores", str(args.cores), "--cpu", "host", "--net0", f"virtio,bridge={args.bridge}",
            "--scsihw", "virtio-scsi-pci", "--agent", "enabled=1", "--onboot", "1")
        Path(f"/root/crate-vm-{vmid}.json").write_text(json.dumps({**plan, "commit": ref, "ssh_key": str(key)}, indent=2))
        print(f"Created VM {vmid}. If interrupted, inspect this VM before rerunning; it will not be deleted automatically.", flush=True)
        run("pvesm", "add", "dir", snippet_storage, "--path", str(work), "--content", "snippets", "--nodes", node)
        run("qm", "set", str(vmid), "--scsi0", f"{storage}:0,import-from={image}", timeout=600)
        run("qm", "resize", str(vmid), "scsi0", f"{args.disk}G")
        run("qm", "set", str(vmid), "--ide2", f"{storage}:cloudinit", "--boot", "order=scsi0",
            "--serial0", "socket", "--vga", "serial0", "--ipconfig0", "ip=dhcp",
            "--cicustom", f"user={snippet_storage}:snippets/user.yaml")
        run("qm", "start", str(vmid))

    print("Installing Crate in the VM and testing a real public media download. This can take 10–20 minutes.", flush=True)
    for attempt in range(240):
        try:
            status = guest(vmid, "cat", "/var/lib/crate/bootstrap-status")
        except (subprocess.SubprocessError, ValueError, RuntimeError):
            status = "booting"
        if status == "ready":
            break
        if status == "failed":
            raise RuntimeError(f"Guest setup failed. Inspect VM {vmid}: qm guest exec {vmid} -- tail -n 80 /var/log/cloud-init-output.log")
        if attempt % 12 == 0:
            print(f"VM {vmid}: {status}…", flush=True)
        time.sleep(5)
    else:
        raise RuntimeError(f"VM {vmid} did not finish setup in time. Check its DHCP lease and cloud-init logs; it has been left in place.")
    interfaces = json.loads(run("qm", "guest", "cmd", str(vmid), "network-get-interfaces").stdout)
    addresses = [a["ip-address"] for i in interfaces for a in i.get("ip-addresses", [])
                 if a.get("ip-address-type") == "ipv4"
                 and not ipaddress.ip_address(a["ip-address"]).is_loopback
                 and not ipaddress.ip_address(a["ip-address"]).is_link_local]
    if not addresses:
        raise RuntimeError("Crate is ready but no guest LAN address was reported. Inspect the VM's network in Proxmox.")
    address = addresses[0]
    host_key = guest(vmid, "cat", "/etc/ssh/ssh_host_ed25519_key.pub")
    if not host_key.startswith("ssh-ed25519 "):
        raise RuntimeError("Guest SSH host key was not available; refusing an unverified SSH connection.")
    known = Path(f"/root/.ssh/crate-vm-{vmid}-known-hosts")
    known.write_text(f"{address} {host_key}\n")
    ssh = ["ssh", "-t", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", f"UserKnownHostsFile={known}", f"crate-admin@{address}"]
    helper = Path(f"/root/crate-connect-{vmid}.sh")
    helper.write_text("#!/bin/bash\nset -euo pipefail\nexec " + shlex.join(ssh) + " sudo /usr/local/sbin/crate-connect-cloudflare\n")
    helper.chmod(0o700)
    print(f"\nCrate is ready: http://{address}:8080\nMP4 download verified inside the VM. No website access code is required.")
    print(f"Cloudflare setup command (run now or later): bash {helper}")
    print("Existing Render hosting and DNS have not been removed. Connect and test the public hostname before retiring Render.")
    if os.isatty(0):
        choice = input("Open Cloudflare setup now? [Y/n] ").strip().lower()
        if choice in {"", "y", "yes"}:
            subprocess.run(["bash", str(helper)], check=True)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise SystemExit("Setup stopped: " + detail)
