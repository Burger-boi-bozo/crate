"""Crate release and deployed build metadata."""
import os

RELEASE_VERSION = "5.0.0"
RUNTIME_VERSION = "crate-v5"


def version_payload() -> dict[str, str]:
    build = os.getenv("CRATE_BUILD_SHA", "").strip()[:12] or "dev"
    return {
        "version": RELEASE_VERSION,
        "build": build,
        "label": f"v{RELEASE_VERSION} · {build}",
    }
