#!/usr/bin/env python3
"""External DPI heartbeat receiver. Env-only credentials; standard-library only."""
import hmac
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOSTS = ("pve", "nas")
TOKEN = os.environ["DPI_TOKEN"]
TOPIC = os.environ["NTFY_TOPIC"]
TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "660"))
STARTUP_GRACE = int(os.getenv("STARTUP_GRACE_SECONDS", "720"))
START = time.monotonic()
LOCK = threading.Lock()
STATE = {name: {"seen": None, "time": None, "down": False, "notified": False,
                "notify_retry": 0} for name in HOSTS}

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def notify(title, message, priority="4"):
    request = urllib.request.Request(
        "https://ntfy.sh/" + TOPIC, method="POST", data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority,
                 "Tags": "warning" if priority == "4" else "white_check_mark"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status in (200, 201, 202):
                print(timestamp(), "push accepted:", title, flush=True)
                return True
    except Exception as exc:
        print(timestamp(), "push error:", type(exc).__name__, str(exc)[:120], flush=True)
    return False

def watcher():
    while True:
        time.sleep(20)
        now = time.monotonic()
        candidates = []
        with LOCK:
            for host, node in STATE.items():
                age = (now - node["seen"]) if node["seen"] is not None else (now - START)
                expired = age > (TIMEOUT if node["seen"] is not None else STARTUP_GRACE)
                if expired and not node["down"]:
                    node["down"] = True
                    node["notified"] = False
                    node["notify_retry"] = 0
                    print(timestamp(), host, "OFFLINE", int(age), flush=True)
                if node["down"] and not node["notified"] and now >= node["notify_retry"]:
                    node["notify_retry"] = now + 120
                    candidates.append((host, int(age), node["time"]))
        for host, age, last_seen in candidates:
            body = ("No heartbeat from " + host.upper() + " for " + str(age // 60) +
                    " minutes. Last successful heartbeat: " + str(last_seen or "never") +
                    "\nExternal Render observer. Check power, router, WAN, and node status." +
                    "\nThis alert does not automatically reboot equipment.")
            accepted = notify("DPI DEAD MAN: " + host.upper() + " silent", body)
            if accepted:
                with LOCK:
                    if STATE[host]["down"]:
                        STATE[host]["notified"] = True

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def response(self, status=200, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        provided = self.headers.get("X-DPI-Key", "")
        return hmac.compare_digest(provided, TOKEN)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self.response(200, {"ok": True, "app": "dpi-deadman"})
        if path == "/status":
            if not self.authorized():
                return self.response(403, {"error": "forbidden"})
            now = time.monotonic()
            with LOCK:
                nodes = {host: {"online": node["seen"] is not None and
                         (now-node["seen"]) < TIMEOUT,
                         "last_seen": node["time"], "alert_active": node["down"]}
                         for host, node in STATE.items()}
            return self.response(200, {"observer": "online", "nodes": nodes,
                                       "checked_at": timestamp()})
        return self.response(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/heartbeat/"):
            return self.response(404, {"error": "not found"})
        host = path.rsplit("/", 1)[-1]
        if host not in HOSTS:
            return self.response(404, {"error": "unknown node"})
        if not self.authorized():
            return self.response(403, {"error": "forbidden"})
        recovered = False
        with LOCK:
            node = STATE[host]
            recovered = node["down"]
            node["seen"] = time.monotonic()
            node["time"] = timestamp()
            node["down"] = False
            node["notified"] = False
            node["notify_retry"] = 0
        if recovered:
            threading.Thread(target=notify, args=(
                "DPI RESTORED: " + host.upper(),
                host.upper() + " sent a new heartbeat at " + timestamp(),
                "3"), daemon=True).start()
        return self.response(200, {"ok": True, "node": host, "received_at": timestamp()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    threading.Thread(target=watcher, daemon=True).start()
    print(timestamp(), "DPI dead-man observer listening on", port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
