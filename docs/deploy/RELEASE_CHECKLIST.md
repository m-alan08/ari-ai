# Pilot release checklist

Run before promoting a build to the pilot environment. Every row maps to a check
that `deploy/release_check.py` + `deploy/scripts/smoke_staging.sh` actually
execute — keep this list and the tooling in sync.

```sh
uv run python deploy/release_check.py --base-url <staging-url> \
    --ops-token "$OPS_TOKEN" --backup-dir backups/ --strict \
    --allow-skipped-check live
OPS_TOKEN=... deploy/scripts/smoke_staging.sh <staging-url>
```

| # | Item | Verified by |
|---|---|---|
| 1 | CI success on the release commit | `release_check.py` (via `gh` when authed, else manual). Every check must have run and succeeded: an empty check list FAILs, and a SKIPPED/NEUTRAL check (gitleaks, audit, regression gates) is reported by name and blocks under `--strict` unless named with `--allow-skipped-check`. Commit statuses (`state`/`context` rows) count like check runs. Under `--strict` every gate in `REQUIRED_GATES` must have produced a row; a gate that did not run is a FAIL |
| 2 | Migration compatibility — DB at code head | `migration_status` / `ops/status` `at_head` |
| 3 | Secrets and configuration valid for the env | `hackatl-preflight` (also runs at every boot) |
| 4 | Authentication required, authorization enforced | `smoke_staging.sh` (401 unauth, POST rejected, foreign CORS rejected, dev-login 404) |
| 5 | Health + readiness endpoints | `/healthz`, `/readyz` |
| 6 | Job queue availability + worker visibility | `/readyz` queue flag; `ops/status` counts + leases |
| 7 | First-responder OFF by default | `ops/status` and DB: zero non-`off` stored modes |
| 8 | Cancellation + emergency-stop surface present | `release_check.py` vs `openapi.json` paths |
| 9 | Read-only pilot scope | `APP_ENV` staging/production, dev login off, no extra feature flags |
| 10 | Backup exists and is readable | newest `*.dump` within `--max-backup-age-h`, `pg_restore --list` |
| 11 | Rollback readiness | restore script + runbook present; deployed-from SHA recorded |
| 13 | No stuck assessments or jobs | `ops/status` `stuck` (expired leases, deadlines, starved queue, recovery_required, idle non-terminal assessments) — `release_check.py` + smoke |
| 14 | Failed jobs reviewed | `ops/status` `failed_jobs_24h` (WARN until each is explained) |
| 15 | Recommendation-only staging posture | preflight (`PILOT_RECOMMENDATION_ONLY`, `ALLOW_AUTO_DELIVERY`, production-host isolation, `OPS_TOKEN`) + `ops/status` `pilot` |
| 12 | Full-stack readiness rehearsal | `deploy/release_readiness.py` GO report (executed proof of boot, queue, crash recovery, backup/restore, authz, emergency stop, alerting, redaction, artifacts) |

## Sign-off

- [ ] Staging pilot: `release_check.py --strict` exits 0 (every gate PASS — no WARN/SKIP; only consciously allow-listed opt-in CI jobs such as `live` may be skipped)
- [ ] Other releases: all rows PASS or WARN (WARNs itemized in the release note)
- [ ] `smoke_staging.sh` exits 0 against the freshly deployed build
- [ ] A rollback + restore drill has been executed in the last 30 days
- [ ] `coordination/status/ALAN.md` and `docs/planning/SESSION_HANDOFF.md` updated
- [ ] Release note records: deployed SHA, migration head, backup file + sha256,
      ops/status snapshot, remaining WARNs

The staging pilot procedure (deploy, monitor, emergency disable, rollback) is
[STAGING_PILOT_RUNBOOK.md](STAGING_PILOT_RUNBOOK.md).

## Candidate binding (strict sign-off)

Run `python deploy/release_check.py --strict --expected-sha <full PR head sha> --base-url <staging>`. Sign-off FAILs unless every required check (`REQUIRED_CHECKS`, or the `--require-check` list) succeeded on that exact SHA. The PR head must equal it before and after the results are read, and `/healthz` `git_sha` must equal it. Missing, skipped, pending, failed or stale results are never evidence.
