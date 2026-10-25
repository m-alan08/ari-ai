# Pilot Operations — deployment topology, checklist, and recovery runbook

Status: **implemented and exercised** against a disposable staging-like stack
(`deploy/evidence/restart_recovery.py`, 6/6 scenarios PASS — see
`deploy/evidence/last-run-evidence.json`). One remaining pre-pilot step needs an
operator: one authorized, budget-capped, real-model staging assessment from the UI.

## Topology (what is actually deployed)

```
Browser ──► Vercel (Next.js, ari-ai-lake.vercel.app)
            │  /api/v1/*  same-origin proxy → BACKEND_ORIGIN
            ▼
Railway service (single container, deploy/railway/start.sh)
   ├─ hackatl-migrate          (alembic upgrade head at boot)
   ├─ hackatl-api             (uvicorn :8000 — /healthz /readyz /diagnostics + /api/v1)
   ├─ hackatl-worker  × N     (WORKER_PROCESSES, default 12)
   └─ python -m backend.redeliver   (GitHub webhook redelivery sweeper)
            │
            ├──► Railway PostgreSQL — durable job queue (SKIP LOCKED, lease heartbeats),
            │    assessments, worker_heartbeats, artifacts index
            ├──► /data volume — ARTIFACT_STORE_URI=file:///data/artifacts (hashed blobs),
            │    SECURITY_WORKDIR=/data/work (bare repo checkouts, sandbox base map)
            ├──► GitHub App — installation tokens: clones, webhook deliveries, delivery PRs
            ├──► Anthropic API — live model (production pins claude-haiku-5-5, #380/ADR-0015)
            └──► Vercel Sandbox — deny-all microVMs for attack execution
                 (VERCEL_TOKEN / VERCEL_PROJECT_ID / VERCEL_TEAM_ID read by the SDK only)
```

Every worker heartbeats into `worker_heartbeats` (upsert per poll while idle, and from
the lease-heartbeat thread while a job runs). `/readyz` counts a worker as alive when
its heartbeat is fresher than `WORKER_LIVENESS_S` (default 30s).

## Safe configuration checklist (names only — never values)

Railway backend service must set, or `/readyz.capabilities` reports the gap:

| Required for | Variables |
|---|---|
| App wiring | `APP_ENV=production`, `DATABASE_URL`, `FRONTEND_ORIGIN`, `API_BASE_URL`, `RAILWAY_RUN_UID=0` |
| Storage | `ARTIFACT_STORE_URI=file:///data/artifacts`, `SECURITY_WORKDIR=/data/work` (a persistent `/data` volume) |
| Model | `ANTHROPIC_API_KEY` (production forces `claude-haiku-5-5`; `ANTHROPIC_MODEL*` pins are set in start.sh) |
| GitHub | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `GITHUB_WEBHOOK_SECRET` |
| Sandbox | `SECURITY_SANDBOX=vercel` + `VERCEL_TOKEN`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (or `docker` where a daemon exists) |
| Rollouts | `WORKER_PROCESSES` (default 12), optional `READYZ_REQUIRE_WORKERS`, `WORKER_LIVENESS_S`, `WORKER_HEARTBEAT_INTERVAL_S` |

Vercel frontend must set `BACKEND_ORIGIN=<railway public origin>`; leave
`NEXT_PUBLIC_DEMO_ONLY` unset and Deployment Protection off so the OAuth
callback + API proxy work.

`RAILWAY_GIT_COMMIT_SHA` is injected by Railway and surfaces as
`git_sha` in `/healthz`, `/readyz`, `/diagnostics`.

## Operational gates

| Check | Endpoint / command | Passes when |
|---|---|---|
| Liveness | `GET /healthz` | `status=ok` + `git_sha` + `schema_version` |
| Infra ready | `GET /readyz` | HTTP 200; `status=ready|degraded|not_ready`; 503 on `not_ready`/`degraded` |
| Assessment-capable | `/readyz` → `capabilities_ready` | `llm.live`, `github.app_configured`, `sandbox.configured`, `artifact_store.writable`, `workers.alive≥1` — each named separately |
| Deep verify | `GET /diagnostics` (**owner-signed-in only** — worker ids, env-var names, queue counts, migration state) | `github.verified=true` (real `GET /app`), `sandbox.sdk_importable`, `migrations.pending=[]`, queue counts, worker rows |
| Deploy pairing | `deploy/verify_pair.sh <frontend> [backend]` | exit 0 — schema versions equal (`/api/frontend-version` on Vercel vs `schema_version` on Railway) |

`/readyz` deliberately separates **can serve traffic** (HTTP status) from
**can run an assessment** (`capabilities_ready`): a live service missing
`VERCEL_TOKEN` returns `status=ready` with `capabilities_ready=false` and
`capabilities.sandbox.configured=false` — config errors are visible, not silent.
The public body returns booleans only; the `detail` strings (which name env vars
and filesystem errors) appear only in owner-authenticated `/diagnostics`.

Worker liveness is not a bare heartbeat: a row counts only when `heartbeat_at`
is fresh AND the worker is verifiably processing — `last_poll_at` fresh (written
solely by the main claim loop) or it holds a claimed job. A process that is
alive but whose claim loop stopped does **not** count; a worker genuinely inside
a long handler stays counted via its held job and reports `last_poll_age_s` in
`/diagnostics` (the "how long on this job" surface). Residual, honest: a handler
deadlocked mid-job still renews its lease — that is a *stuck job*, visible via
`worker_rows[].last_poll_age_s` and the job's own stale-progress, not something
a heartbeat can distinguish from a legitimately long run.

## Recovery runbook

| Event | What happens (proven by evidence harness) | Operator action |
|---|---|---|
| Deploy / rolling restart | start.sh TERM trap → workers finish the in-hand job then exit; API killed last (S1) | none — drain is automatic |
| Worker crash mid-run | Job lease expires → reclaimed → no mid-run checkpoint → `CONFLICT_STATE` → assessment **FAILED**, job `failed`, visible error — never fabricated success (S2) | start a new assessment from the UI; the dead worker's job does not strand |
| Run paused for approval | `AWAITING_APPROVAL` writes a hashed checkpoint artifact; a fresh worker `claim_checkpoint`s it (row lock, exactly one claimant) and resumes to `COMPLETED` (S3) | approve the pending remediation approval; resume is automatic |
| Two workers race | SKIP LOCKED claim + unique `dedupe_key` → exactly-once (S4) | none |
| New work after a crash | A brand-new assessment on the same repo+scope reaches `AWAITING_APPROVAL` post-kill — the supported retry path is a new run, never a hidden re-run (S7) | start a new assessment from the UI |
| Migration mistake | `hackatl-migrate` is alembic; `downgrade`/`upgrade` round-trips verified (S5) | fix the bad migration, redeploy (migrations run at boot) |
| Backup/restore | `pg_dump --file` → `psql -f` reproduces identical row counts (S6) | nightly dump is Railway's job; restore procedure verified |

## Restart + rollback procedures

- **Rollback (app)**: redeploy the previous Railway deployment (dashboard → deploys →
  rollback) or `git revert` + push; migrations run at boot — downgrade-only changes
  need `hackatl-migrate` to `downgrade` target (see S5) or a compensating migration.
- **Rollback (pairing)**: frontend is pinned to `shared/generated` at its build SHA;
  `deploy/verify_pair.sh` detects schema skew between the pair before it breaks users.
- **Backup**: managed Postgres snapshots + `pg_dump` (procedure exercised in S6);
  artifacts on `/data` are content-addressed — re-verify on restore via the
  `artifacts` table `sha256`.

## Honesty notes

- `/diagnostics` `github.verified` mints the App JWT and calls `GET /app` (60 s
  cache); it reports the app slug only — never a token or key byte.
- A mid-run kill before any approval checkpoint cannot resume — the orchestrator
  intentionally fails loudly rather than replay side effects (S2). Checkpointing
  is designed at `AWAITING_APPROVAL`, the only human gate, and resume is durable.
- Scripted-model runs (e2e, this harness) are recorded `provider_mode="fake"`; the
  only remaining "is it really live" signal is the one real-model staging run.
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

## 13. What still needs a human

- Real `OPS_TOKEN`, `ALERT_WEBHOOK_URL`, `SESSION_SECRET` values in Railway/Vercel
- A backup schedule (cron/GitHub Action) — scripts exist, nothing schedules them
- The four in-flight PRs merging to main (this workstream does not merge them)
