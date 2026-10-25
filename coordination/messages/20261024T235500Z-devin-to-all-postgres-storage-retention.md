# MSG: Postgres storage retention + ops surface — zero-migration, no merge conflicts expected

- ID: `20261024T235500Z-devin-to-all-postgres-storage-retention`
- From: Devin (Adam lane — session `4b6599edf9e34330a847d73165010ea9`)
- To: integration lead + all agents landing pilot PRs
- Kind: status
- Timestamp (UTC): 2026-10-24T23:55:00Z
- Branch: `fix/adam/postgres-storage-retention`

## Summary (facts, not assumptions)

Railway reported the production Postgres volume at 80%. Code inventory shows the
dominant unbounded writers: `index_payloads` (full payload set per assessed commit),
terminal `jobs` rows (JSONB payload/result/last_error retained forever),
`webhook_deliveries`/`monitoring_events`/`reassessment_requests`/`runtime_windows`/
`idempotency_keys`/`sessions`, orphaned payload bytes from failed indexing, and on the
app `/data` volume the artifact CAS + `refs/ari/*` refs in bare clones.

## What this branch adds

- `backend/backend/maintenance.py` — `python -m backend.maintenance report|apply|loop`;
  bounded batched retention + a full storage report (DB bytes, WAL, per-table sizes,
  dead tuples, payload bytes by kind, artifact/workdir disk).
- `backend/backend/api/ops.py` — `GET /api/v1/ops/storage`,
  `GET /api/v1/ops/storage/prometheus`, `POST /api/v1/ops/maintenance`,
  `POST /api/v1/ops/maintenance/dry-run-report`; Bearer `OPS_API_TOKEN`, 404 when unset.
- `deploy/railway/start.sh` — hourly maintenance sidecar (excluded from `pids` so a
  maintenance failure can never restart the service).
- `backend/tests/test_maintenance.py` — 14 tests incl. RLS-scoped payload pruning and
  active-assessment protection.
- `docs/deploy/POSTGRES_STORAGE.md` — runbook, rollout, retention table, reclaim steps.
- Alerts: fixed 70/80/90% of `POSTGRES_VOLUME_BYTES` → log + `OPS_ALERT_WEBHOOK_URL`.

## Migration / contract impact

**None.** Retention deliberately ships with ZERO schema migrations — `0017` belongs
to PR #533 (`worker_heartbeats`) and `test_parallel_migrations` expects head `0016`.
No `shared/` contract changes; new endpoints are additive under `/api/v1/ops/*`.
RLS honored: payload pruning runs per project inside a `set_config('ari.project_id')`
transaction, same predicate as `intel_store.py`.

## Safety posture

Ships as dry-run (`RETENTION_DRY_RUN=true`): reports what would be deleted, deletes
nothing until flipped. audit_events, findings, approvals, authorized_scopes,
assessment rows, and any snapshot referenced by a non-terminal assessment are never
eligible. Every real prune appends an audit row.

## Action required by recipient

Integration lead: no ordering constraint vs. the pilot merge lane — this branch
conflicts with nothing. Adam: see rollout steps in `docs/deploy/POSTGRES_STORAGE.md`
(set `OPS_API_TOKEN`, `POSTGRES_VOLUME_BYTES`, review first dry-run report, flip
`RETENTION_DRY_RUN=false`).

## Evidence

`pytest backend/tests/test_maintenance.py` 14/14; regression subset 50/50; ruff clean.
