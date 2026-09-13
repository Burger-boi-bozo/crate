import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("proxmox_installer", Path(__file__).parents[1] / "scripts/proxmox-install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_storage_selection_requires_active_capacity_and_vm_content():
    rows = [
        {"storage": "backup-only", "active": 1, "content": "backup", "avail": 100 * 1024**3},
        {"storage": "offline", "active": 0, "content": "images", "avail": 100 * 1024**3},
        {"storage": "small", "active": 1, "content": "images", "avail": 5 * 1024**3},
        {"storage": "local-lvm", "active": 1, "content": "images,rootdir", "avail": 40 * 1024**3},
    ]
    assert installer.choose_storage(rows, None, 24) == "local-lvm"
    for requested in ("backup-only", "offline", "small", "missing"):
        with pytest.raises(RuntimeError):
            installer.choose_storage(rows, requested, 24)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(installer.shutil, "which", lambda command: "/usr/bin/" + command)
    monkeypatch.setattr(installer.socket, "gethostname", lambda: "pve")
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k: "MemAvailable: 8000000 kB\n"
                        if str(p) == "/proc/meminfo" else original_read(p, *a, **k))
    def read_only_command(*args, **kwargs):
        assert args[0] == "ip" and args[-4:] == ("link", "show", "dev", "vmbr0"), "Preflight attempted a mutation"
        # Ordinary ip JSON output omits the interface kind; details are required.
        link = {"ifname": "vmbr0", "link_type": "ether", "operstate": "UP"}
        if "-d" in args or "-details" in args:
            link["linkinfo"] = {"info_kind": "bridge"}
        return subprocess.CompletedProcess(args, 0, json.dumps([link]))
    monkeypatch.setattr(installer, "run", read_only_command)
    def no_fetch(url):
        raise AssertionError("Preflight must not download or execute code")
    monkeypatch.setattr(installer, "fetch", no_fetch)
    responses = {
        "/nodes/pve/storage": [{"storage": "local-lvm", "active": 1, "content": "images", "avail": 40 * 1024**3}],
        "/cluster/nextid": 101, "/cluster/resources": [],
    }
    monkeypatch.setattr(installer, "api", lambda path, *args: responses[path])
    return responses


def test_plan_is_read_only_and_reports_resources(host, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["proxmox-install.py", "--plan"])
    installer.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["vmid"] == 101 and plan["ram_mb"] == 2048 and plan["disk_gb"] == 24
    assert plan["changes_existing_vms"] is False


def test_existing_vm_is_refused_before_mutation(host, monkeypatch):
    host["/cluster/resources"] = [{"vmid": 101}]
    monkeypatch.setattr(sys, "argv", ["proxmox-install.py", "--vmid", "101"])
    with pytest.raises(RuntimeError, match="already exists"):
        installer.main()


def test_guest_exit_failure_is_not_mistaken_for_readiness(monkeypatch):
    monkeypatch.setattr(installer, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0,
                        json.dumps({"exited": 1, "exitcode": 1, "out-data": "ready", "err-data": "failed"})))
    with pytest.raises(RuntimeError, match="failed"):
        installer.guest(101, "cat", "/var/lib/crate/bootstrap-status")
