"""Structured release history used by the public changelog and private deployment view."""
from __future__ import annotations

from app.version import version_payload

_RELEASES = [
    {
        "version": "6.0.0", "date": "2026-09-14", "commit": None,
        "summary": "Advanced media workflows, integrations, PWA support, and a private operator control room.",
        "features": ["advanced conversion", "uploads", "subtitles", "sharing", "API tokens", "webhooks", "backup/restore"],
        "fixes": ["adaptive scheduling", "resumable work", "automatic retry and fallback"],
        "rollback": "5.1.0",
    },
    {
        "version": "5.1.0", "date": "2026-09-14", "commit": "b7ccc1bdc30536c6b3d38bcc3bb57d1ef6263b53",
        "summary": "Polished the converter UI and made 24-hour retention explicit.",
        "features": ["visible changelog", "24-hour retention"],
        "fixes": ["paste-field arrow", "asset cache busting"], "rollback": "5.0.0",
    },
    {
        "version": "5.0.0", "date": "2026-09-13", "commit": "8b31746e217add9f321eab80c220435b031b1fd2",
        "summary": "Batching, SSE progress, smarter scheduling, and expanded admin telemetry.",
        "features": ["batch downloads", "SSE progress", "provider health", "preview recommendations"],
        "fixes": ["persistent SQLite queue", "duplicate suppression"], "rollback": "4.0.0",
    },
]


def releases() -> list[dict]:
    current = version_payload()
    result = []
    for item in _RELEASES:
        row = dict(item)
        if row["version"] == current["version"]:
            row["commit"] = current["build"] if current["build"] != "dev" else None
            row["current"] = True
        else:
            row["current"] = False
        result.append(row)
    return result
