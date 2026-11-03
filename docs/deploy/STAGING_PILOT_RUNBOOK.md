# Staging pilot runbook (owner-authorized, recommendation-only)

Scope: one staging deployment, owner-authorized test repositories, recommendation
only. Ari reports findings and verified patch proposals; it never approves on an
owner's behalf, pushes, opens pull requests, or enables first-responder actions.
Production is out of scope; nothing here deploys to it.

Companion documents: [PILOT_OPERATIONS.md](PILOT_OPERATIONS.md) (mechanics),
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) (gates),
[POSTGRES_STORAGE.md](POSTGRES_STORAGE.md) (storage/retention).

## 1. Isolation rules

- Staging has its own Railway project/service, its own PostgreSQL, its own
  artifact volume and its own GitHub App + OAuth app. Nothing is shared with
  production.
- `hackatl-preflight --config` (run by `deploy/railway/start.sh` at every boot)
  refuses a staging boot when any of these is true:
  - `API_BASE_URL`, `FRONTEND_ORIGIN`, `DATABASE_URL` or `MIGRATION_DATABASE_URL`
    contains a host in `PRODUCTION_HOSTS`;
  - `PILOT_RECOMMENDATION_ONLY=false` or `ALLOW_AUTO_DELIVERY=true`;
  - `OPS_TOKEN` is unset (stuck-work monitoring would be dark);
  - `AUTH_DEV_LOGIN_ENABLED=true`, `SECURITY_SANDBOX=local-dev`, local docker-compose
    database credentials, a non-PostgreSQL URL, or a missing required secret.
  Errors name the variable, never its value.
- Connect only repositories the pilot owner controls and has authorized in an
  `AuthorizedScope`. Never install the staging App on a customer organisation and
  never load customer credentials into staging secrets.
- First-responder mode stays `off` (the server has no AUTO-EXECUTE mode).

## 2. Configuration

Start from [`.env.staging.example`](../../.env.staging.example); set values in the
Railway secret store only. Pilot-specific variables:

| Variable | Pilot value | Effect |
|---|---|---|
| `APP_ENV` | `staging` | JSON logs, strict preflight, model pinned by `start.sh` |
| `PILOT_RECOMMENDATION_ONLY` | `true` (default in staging) | `delivery.authority()` returns none, `push_patch` raises `FORBIDDEN`, build-recipe PRs skipped |
| `ALLOW_AUTO_DELIVERY` | `false` | belt-and-braces: direct default-branch commits off |
| `PRODUCTION_HOSTS` | production API + frontend hosts | isolation check above |
| `OPS_TOKEN` | random 32+ bytes | enables `GET /api/v1/ops/status` |
| `ALERT_WEBHOOK_URL` | staging alert channel | dead/permanently-failed jobs page |
| `STUCK_ASSESSMENT_S` | `3600` | idle threshold for stuck assessments |
| `FEATURE_FLAGS` | empty | no optional surfaces |

## 3. Deploy (staging only)

1. Take a backup: `DATABASE_URL=... deploy/scripts/db_backup.sh backups/`; record
   the path and sha256 it prints. Verify it: `pg_restore --list <dump> | head`.
2. Record the currently deployed SHA (the rollback target).
3. Deploy the release commit to the staging service. `start.sh` runs preflight,
   then `hackatl-migrate`, then API + workers; a preflight error stops the boot.
4. Run the gates:
   ```sh
   OPS_TOKEN=... deploy/scripts/smoke_staging.sh https://<staging-api>
   uv run python deploy/release_check.py --base-url https://<staging-api> \
       --ops-token "$OPS_TOKEN" --backup-dir backups/ --strict \
       --allow-skipped-check live
   ```
   `--strict` turns every WARN/SKIP into a failure: the pilot needs PASS evidence.
   The CI gate counts only checks that ran and succeeded; a skipped security or
   regression check blocks unless you name it with `--allow-skipped-check`
   (only for opt-in jobs such as `live`), and a PR with no checks fails.
   Strict mode also refuses to sign off when any gate in `REQUIRED_GATES`
   produced no row (so `--no-http`, a crash or a skipped section cannot pass).
   for every gate. Paste both outputs into the release note.

## 4. Monitoring

`GET /api/v1/ops/status` (header `X-Ops-Token`) is the single operator view:

| Field | Page when |
|---|---|
| `migrations.at_head` | `false` — workers refuse to claim against a stale schema |
| `queue.counts`, `queue.oldest_queued_age_s` | backlog older than `JOB_STUCK_PENDING_S` |
| `stuck.jobs`, `stuck.jobs_by_reason` | any: `recovery_required`, `lease_expired`, `deadline_exceeded`, `queued_starved`, `waiting_on_prerequisite` |
| `stuck.assessments`, `stuck.assessment_ids` | any: a non-terminal assessment idle past `STUCK_ASSESSMENT_S` with no live `run_assessment` job (`AWAITING_APPROVAL` is a human gate, not stuck) |
| `failed_jobs_24h` | any `dead` / `recovery_required`; review `failed` / `timed_out` daily |
| `pilot.recommendation_only`, `pilot.auto_delivery_allowed` | not `true` / `false` |
| `first_responder.projects_with_non_default_mode` | non-zero |

Detail: `uv run python -m backend.execution stuck` lists each stuck job with
its redacted recovery record; `... stats` gives per-kind failure rates.
Workers appear in `queue.workers_with_leases` only while holding a job; an idle
fleet shows an empty list (honest limit, see PILOT_OPERATIONS §5).

Logs are JSON outside development/test, carry `request_id`, and pass through the
redaction filter (tokens, keys, signed URLs, credentials in URLs). Attach
`/api/v1/ops/status` output, never raw environment dumps, to incident notes.

## 5. Provider outage

Anthropic 429/5xx/timeouts are retried with bounded exponential backoff
(`LLM_MAX_RETRIES`, execution deadline). When retries are exhausted the
assessment is terminated `FAILED` with `LLM_UNAVAILABLE` — never a fabricated
result. A rejected credential fails immediately (non-retryable). Operator action:
confirm the provider status page, wait, then start a new assessment from the UI.
`backend/tests/test_staging_pilot.py::test_provider_outage_fails_the_assessment_visibly`
pins this behaviour.

## 6. Emergency disable (fastest first)

| Lever | How | Effect |
|---|---|---|
| Cancel one assessment | `POST /api/v1/assessments/{id}/cancel` (or Cancel in the UI) | run stops at the next checkpoint, `CANCELLED` |
| Stop a repository's automatic reviews | Settings → Repositories → Automatic reviews OFF | pushes no longer schedule assessments |
| Disconnect a repository | `POST /api/v1/repositories/{id}/disconnect` | no further GitHub access for that repo |
| Live-defense stop | `POST /api/v1/projects/{id}/emergency-stop` | blocks every live-defense action for the project |
| Stop all processing | scale the staging worker service to 0 (Railway) | queued jobs wait; the API stays up |
| Take staging offline | stop the staging service | nothing runs; data intact |
| Revoke GitHub | suspend the staging GitHub App installation | every GitHub call fails closed |

Recommendation-only mode is not a lever to flip during an incident: it is fixed
by configuration and enforced again at preflight.

## 7. Rollback

1. Application: redeploy the recorded previous staging deployment (Railway →
   Deployments → Redeploy). SIGTERM drains the in-hand job per worker.
2. Schema: migrations are forward-only in normal operation. If the bad release
   migrated, restore the pre-deploy dump, then redeploy the old revision:
   ```sh
   RESTORE_CONFIRM=I-KNOW-THIS-OVERWRITES-DATA \
   DATABASE_URL=<staging-db> deploy/scripts/db_restore.sh backups/<stamp>.dump
   ```
   `db_restore.sh` refuses without the confirm flag and needs a second opt-in for
   `APP_ENV=production` — never point it at production from this runbook.
3. Re-run §3 step 4. Rollback is complete only when smoke and `--strict` pass.

## 8. Recovery drill (required evidence)

Before the pilot and every 30 days: take a backup, restore it into a scratch
database, run `hackatl-migrate` and `release_check.py --no-http`. The local
rehearsal `uv run python deploy/release_readiness.py` performs the same drill
against an embedded PostgreSQL and writes its report to `deploy/reports/`
(gitignored); a staging drill must still be performed against the staging
database snapshot.
