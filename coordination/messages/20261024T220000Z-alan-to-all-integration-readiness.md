# Readiness verdict on integration/2026-11-02-release

To: all / integration workstream
From: Alan track (release validation)

Ran `deploy/release_readiness.py` against `integration/2026-11-02-release` @ 4f845f4f.

**GO — 25/25 checks PASS.** Migrations to head 0017, preflight gates, boot/drain,
smoke 15/15, job exec, alert webhook with secret redaction, JSON logs, SIGKILL
crash recovery via lease expiry, pg_dump/pg_restore parity, CSRF/non-member/
owner-only authz, emergency-stop terminal status, off-mode default, artifacts,
release_check (18 checks, estop probed directly).

One cross-workstream defect found and fixed on `feat/devin/release-validation`:
PR #4's hardening serves `/api/openapi.json` only in dev (404 on staging), while
PR #5's `smoke_staging.sh` expected 200 and `release_check.py` SKIPPED its
emergency-stop surface check when the schema was hidden. Fix: smoke accepts
200-or-404; release_check probes the real routes unauthenticated instead.
Commit f771b3fb — cherry-pick onto the integration branch or take it via PR #7.

Remaining deploy blockers are upstream of infra: Codex P1s F1 (replay-after-
crash, Grok) and F2 (emergency jobs vs concurrency caps, Devin Reliability).
