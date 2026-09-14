> Historical v4 review. Current production behavior is documented in the README and CHANGELOG.

# Crate v4 review

V4 is intentionally kept off production until review.

## User-facing changes
- Paste/link preview before submission: title, creator, duration, source, and a server-fetched size-limited thumbnail.
- Public server telemetry removed from the downloader page.
- Existing progress, pause/resume, retry, quality selection, music lookup, and download history remain.
- Duplicate active submissions for the same user/url/format/quality reuse the existing queued job.

## Admin panel
- `/admin` uses a separate HttpOnly admin session.
- Shows exact build, uptime, workers, active/pending jobs, disk, memory, load, recent failures, provider health, and recent jobs.
- Admin can cancel active jobs, retry failures, and delete finished jobs.
- Proxmox deploys generate a strong password and store it root-only in `/etc/crate/admin-password`.

## Reliability changes
- Job state moves from JSON snapshots to SQLite in WAL mode.
- Existing `jobs.json` is migrated automatically and retained once as `jobs.json.v3-backup`.
- Job lifecycle events are persisted for diagnostics.
- V4 deploy health checks require the exact commit SHA and `crate-v4` runtime before accepting the rollout.
