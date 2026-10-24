# Pilot release checklist

Run before promoting a build to the pilot environment. Every row maps to a check
that `deploy/release_check.py` + `deploy/scripts/smoke_staging.sh` actually
execute — keep this list and the tooling in sync.

```sh
uv run python deploy/release_check.py --base-url <staging-url> \
    --ops-token "$OPS_TOKEN" --backup-dir backups/
deploy/scripts/smoke_staging.sh <staging-url>
```

| # | Item | Verified by |
|---|---|---|
| 1 | CI success on the release commit | `release_check.py` (via `gh` when authed, else manual) |
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

## Sign-off

- [ ] All checklist rows PASS or WARN (WARNs itemized in the release note)
- [ ] `smoke_staging.sh` exits 0 against the freshly deployed build
- [ ] A rollback + restore drill has been executed in the last 30 days
- [ ] `coordination/status/ALAN.md` and `docs/planning/SESSION_HANDOFF.md` updated
- [ ] Release note records: deployed SHA, migration head, backup file + sha256,
      ops/status snapshot, remaining WARNs
