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
