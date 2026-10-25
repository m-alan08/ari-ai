# Pilot operations: deployment, verification and recovery

The operator manual for the controlled design-partner pilot. Everything here is
implemented, not aspirational — each section names the code or script that does it.
Companion checklist: [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). Hosting
topology: [RAILWAY_AND_GITHUB_APP.md](RAILWAY_AND_GITHUB_APP.md) and
[docs/18](../18_INFRASTRUCTURE_AND_DEPLOYMENT.md).

## 1. Deployment paths

| Path | Command | Use |
|---|---|---|
| Local stack | `docker compose up --build` | repeatable staging rehearsal on any machine with Docker |
| Local, no Docker | `make dev` | embedded PostgreSQL + API + worker |
| Railway | `railway-start` (`deploy/railway/start.sh`) | the real hosted control plane |

All three paths run the same sequence: **preflight → migrate → serve + workers**.

## 2. Configuration validation at startup (`backend/preflight.py`)

`hackatl-preflight` runs inside `hackatl-api` and `hackatl-worker` before either
opens a socket or claims a job, and inside `deploy/railway/start.sh` before
migrations. In `staging`/`production` it fails the boot (non-zero exit) on:

- `AUTH_DEV_LOGIN_ENABLED=true` or `SECURITY_SANDBOX=local-dev`
- `DATABASE_URL` carrying the docker-compose credentials (`app-local-only`, `app:app@`)
- missing `ANTHROPIC_API_KEY`, GitHub App credentials, `GITHUB_WEBHOOK_SECRET`,
  or OAuth client pair
- workers missing `RL_CHECKPOINT_PATH`/`RL_CHECKPOINT_SHA256`/`RL_TOPOLOGY_PATH`

Warnings (non-blocking): unset `OPS_TOKEN`, `ALERT_WEBHOOK_URL`, `SESSION_SECRET`,
non-https origins, local `file://` artifact store in production.

Run it by hand any time: `uv run hackatl-preflight` (add `--worker` for
worker-only checks, `--config` to skip the DB check).

## 3. Database migration preflight

- `hackatl-migrate` (Alembic `upgrade heads`) runs as its own compose service and
  inside `railway-start` before any process serves traffic.
- `hackatl-worker` **refuses to start** when `alembic_version` is missing or
  behind the code heads — a stale-schema worker cannot silently corrupt a queue.
- `GET /api/v1/ops/status` reports `migrations.current` vs `expected` vs `at_head`
  against the live database.
- `deploy/release_check.py` fails the release when `at_head` is false.

## 4. Secrets

Secrets arrive only through environment variables / the host's secret store
(`.env` locally — git-ignored; Railway variables in production). Nothing reads
them from disk except `GITHUB_APP_PRIVATE_KEY_PATH`. Settings are Pydantic
`SecretStr`: they never appear in reprs, and every log line passes through
`backend/redaction.py` before emission.

New variables for this workstream: `OPS_TOKEN`, `ALERT_WEBHOOK_URL`,
`LOG_FORMAT`, `FEATURE_FLAGS` — documented in `.env.example` and required
staging values in `.env.staging.example`.

## 5. Health, readiness and queue visibility

| Endpoint | Auth | Shows |
|---|---|---|
| `GET /healthz` | none | process alive + version |
| `GET /readyz` | none | database reachable, job table reachable, LLM mode, GitHub/webhook configured |
| `GET /api/v1/ops/status` | `X-Ops-Token` | migration head, job counts by status, running jobs with lease owner + heartbeat age, workers holding leases, non-default first-responder modes, alert config, active feature flags |

Honest limit: a worker only appears in `ops/status` while it holds a lease —
idle workers leave no trace in the jobs table (there is no worker registry by
design). Queue depth, dead/failed counts and oldest-queued age are always exact.

## 6. Logs

`LOG_FORMAT=json` (the `auto` default outside dev/test) emits one JSON object per
line — `ts`, `level`, `logger`, `msg`, `request_id`, `exc` — through a formatter
that applies `redact_text` to the finished line, so a credential interpolated
into any record cannot reach the stream. Uvicorn runs with `log_config=None` so
access logs take the same path. Implemented in `backend/ops_logging.py`.

## 7. Bounded worker resources

- Compose: `worker` is capped at `mem_limit: 2g`, `cpus: 2.0`, `pids_limit: 512`;
  `api` at `1g`/`1.0`/`256`. One worker process = one job at a time.
- Railway: `WORKER_PROCESSES` (default 12, ~80 MB each) bounds concurrency;
  `LLM_MAX_TOKENS_PER_ASSESSMENT` / `LLM_MAX_USD_PER_ASSESSMENT` /
  `AUTOMATIC_USD_PER_DAY` bound spend; sandbox bounds are in
  `SANDBOX_*` and enforced by the driver, not this file.

## 8. Alerting on critical failures (`backend/alerts.py`)

The worker POSTs to `ALERT_WEBHOOK_URL` (Slack-compatible `{"text": ...}`) when:

- a job exhausts its attempts and lands **dead** — nobody will retry it
- a job fails **permanently** (`PermanentJobError`: bad input, policy denial)

Delivery is best-effort and fully redacted; a webhook outage logs a warning and
never takes the worker down. Without the variable set, alerting is silently off
— preflight warns about that in staging/production.

## 9. Backup and restore

```sh
# backup (works against any reachable DATABASE_URL; strips the +psycopg suffix)
DATABASE_URL=... deploy/scripts/db_backup.sh backups/

# verify
pg_restore --list backups/hackatl-<stamp>.dump | head

# restore — overwrites objects in the target DB, requires the confirm flag,
# re-runs migrations afterwards. APP_ENV=production needs a second opt-in.
RESTORE_CONFIRM=I-KNOW-THIS-OVERWRITES-DATA \
DATABASE_URL=... deploy/scripts/db_restore.sh backups/hackatl-<stamp>.dump
```

Schedule: take a dump before every deploy and daily during the pilot. Keep the
path + sha256 lines the script prints in the deploy log. Practising the restore
is part of the release checklist — a backup nobody has restored is a hypothesis.

## 10. Rollback

Application rollback is redeploying the previous image/revision:

1. **Railway:** Deployments → pick the previous deployment → Redeploy. Worker
   drain semantics in `start.sh` keep in-flight jobs alive through the swap.
2. **Git:** revert the merge commit and push; CI re-runs, then redeploy.
3. **Database:** `upgrade()` is forward-only. If the bad release migrated the
   schema, restore the pre-deploy dump (section 9), then redeploy the old
   revision — `hackatl-preflight` will stop the old code if the schema is newer
   than it understands only in the worker path; the API stays up but reports
   `migrations.at_head: false` in ops/status.
4. Confirm with `deploy/scripts/smoke_staging.sh <url>` and `release_check.py`.

## 11. Feature flags

`FEATURE_FLAGS=flag-a,flag-b` — a comma-separated kill-switch list surfaced in
`ops/status`. Flags are OFF unless listed. New pilot surface goes behind a flag
checked via `get_settings().feature_enabled("name")` — see `settings.flags()`.
Existing pilot gates that are NOT env flags: per-repository
`automatic_assessments`, per-project first-responder intent (default `off`,
auto-execute unrepresentable by design), and `GITHUB_AUTO_COMMIT_AUTHORS`.

## 12. Validating the in-flight PRs together

`deploy/scripts/integration_branch.sh <pr#>...` builds
`integration/pilot-<date>` from `origin/main`, merges each PR head in order,
reports conflicts without resolving them, then runs `make check` and the backend
suite. `--push` publishes the branch so CI runs on the combination. Nothing is
merged to `main`; delete the branch after the PRs land.

## 13. Automated release-readiness rehearsal

`deploy/release_readiness.py` runs the whole pilot stack end-to-end on one machine
(embedded Postgres, real API + worker + migrations + alert webhook sink) under the
staging profile and writes a GO/NO-GO report to `deploy/reports/` (gitignored):

```sh
# local rehearsal — scratch DB created and dropped automatically
uv run python deploy/release_readiness.py

# remote mode — only HTTP checks against a live deployment
uv run python deploy/release_readiness.py --remote \
    --base-url https://staging-api.example.com --ops-token $OPS_TOKEN \
    --database-url $DATABASE_URL
```

Every check is an executed proof, not a config review: negative preflight (boot
refused without secrets), full-config preflight, migrations to head, API+worker
boot, SIGTERM drain, the 15-check staging smoke, a probe job succeeding, the
alert webhook receiving a redacted `job_failed_permanent`, JSON logging,
SIGKILL worker crash → lease expiry → another worker finishing the job
(`worker.recovery`), a real `pg_dump`/`pg_restore` into a sibling database, CSRF +
non-member 404 + owner-only emergency-stop authorization, the emergency-stop job
reaching a terminal state, off-mode default reporting in `ops/status`, artifact
round-trip, and `release_check.py` itself.

`deploy/readiness_worker.py` is its probe worker: registers three test-only job
kinds (`release_probe_success`, `release_probe_fail`, `release_probe_sleep`) then
runs the real `backend.cli.worker` path — same preflight gates, same loop.

Re-run it after every integration change; the report is the release-readiness
evidence. A FAIL or WARN blocks GO until explained.

## 14. What still needs a human

- Real `OPS_TOKEN`, `ALERT_WEBHOOK_URL`, `SESSION_SECRET` values in Railway/Vercel
- A backup schedule (cron/GitHub Action) — scripts exist, nothing schedules them
- The four in-flight PRs merging to main (this workstream does not merge them)
