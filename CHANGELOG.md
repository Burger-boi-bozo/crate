# Change log

All notable production changes to Crate are recorded here.

## 6.0.0 — 2026-09-14

### Added
- Advanced output controls: trim ranges, FPS, CRF, video/audio codec choice, metadata, artwork, subtitles, and filename templates.
- MP4, MKV, WebM, GIF, WebP, MP3, M4A, MKA, Opus, FLAC, WAV, and AAC output paths.
- Local-media uploads, drag/drop and clipboard input, PWA installation/share target, temporary share links, API tokens, webhooks, CLI, and browser-extension automation.
- Persistent batches, selected batch actions/ZIPs, searchable history, priority controls, learned ETAs, cache reuse, checksums, and richer progress phases.
- Private admin config/storage/metrics/audit/security panels, passkeys, diagnostics, backup/restore, release history, and deployment status.
- Provider canary workflow for testing yt-dlp updates before adoption.
- Blue-green Proxmox deployment with per-release virtualenvs, localhost candidate health gating, exact-SHA verification, database backup, and rollback.

### Performance and reliability
- Production is tuned for 3 vCPUs / 4 GB RAM: 3 general workers, 1 heavy conversion, 2 FFmpeg threads, and a 768 MiB free-memory reserve.
- Faster software presets for HEVC, AV1, and VP9 while retaining H.264 `veryfast` defaults.
- Automatic retry/backoff, compatible source fallback, resumable work, adaptive CPU/RAM/disk scheduling, and 24-hour cleanup remain enabled.
- Real staging matrix validated H.264, HEVC, VP9, AV1, MKV/MKA copy, all audio outputs, GIF, WebP, YouTube preview, and a public CC0 MP4.

### Excluded by design
- Presets, QR handoff, and browser notifications are not part of v6.

## 5.1.0 — 2026-09-14

### Changed
- Fixed the paste-field arrow layout and added a new asset cache key so browsers receive the corrected CSS immediately.
- Added a visible **Change log** section near the bottom of the public UI.
- Bumped the public/application version from 5.0.0 to 5.1.0.
- Updated the public storage note to match the 24-hour retention policy.
- Set the Proxmox admin password default to `password` as requested.
- Added a one-time v5.1 migration so existing generated admin passwords switch to the new default while later manual changes remain preserved.
- Reworked the README around the current Proxmox production architecture and removed stale primary-path documentation from the front page.

### Retention
- Completed downloads expire after 24 hours (`CRATE_TTL=86400`).
- The Proxmox installer and updater explicitly enforce the 24-hour TTL.

## 5.0.0 — 2026-09-13

### Added
- Batch submissions and completed-batch ZIP downloads.
- Server-Sent Events for live job updates with polling fallback.
- Light/heavy workload classification, load-aware scheduling, and per-session fairness.
- Admin maintenance mode, cleanup, bulk actions, metrics, and provider health summaries.
- Provider-specific error suggestions.
- Preview recommendations and output-size estimates.
- SQLite/WAL job persistence, restart recovery, and duplicate-active-job suppression.

### Release validation
- Full Python and browser UI test suites.
- Real CC0 MP4 conversion and file-signature verification.
- Batch/ZIP, SSE, preview, SQLite restart persistence, admin, and public Cloudflare smoke tests.

## 4.0.0 — 2026-09-13

- Added the authenticated admin panel and removed public server telemetry.
- Added link previews.
- Migrated queue/history persistence to SQLite/WAL.
- Added job events/timeline and duplicate active-job suppression.
- Added CI-gated Proxmox auto-updates with exact-build health verification and rollback safeguards.
