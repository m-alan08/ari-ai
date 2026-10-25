# PostgreSQL storage: reporting, bounded retention, and alerts

Why the Railway Postgres volume fills up:

- **`index_payloads`** stores the full code-intelligence payload set for *every*
  `(project_id, repo_id, commit_sha, index_version)` — immutable `LargeBinary` rows.
  Automatic assessments write a complete payload set, so Postgres grows with assessed
  commits.
- **`jobs`** rows carry JSONB `payload`/`result`/`last_error` and are never deleted,
  including terminal `succeeded`/`failed`/`dead` rows.
- **`webhook_deliveries`**, **`monitoring_events`**, **`reassessment_requests`**,
  **`runtime_windows`**, **`idempotency_keys`**, and **`sessions`** accumulate forever.
- Failed/abandoned index jobs can leave payload bytes that were never marked ready.
- On the app-service `/data` volume (not the Postgres volume): the artifact
  content-addressed store and bare repo clones under `SECURITY_WORKDIR/repos/*.git`,
  where each fetched commit leaves a `refs/ari/<sha>` ref and objects never get pruned.

None of this shrinks the volume by deleting rows: Postgres reuses freed space inside its
own files. See "Reclaiming disk" for what actually returns space to Railway.

## Automatic assessments and storage

Each automatic assessment stores about 5.7 MB of physical data in `index_payloads`
(production measurement on 2026-10-25: 3.46 GB across 609 snapshots). With `*` and
500 assessments per day, the database grew about 330 MB/day. An empty
`GITHUB_AUTO_COMMIT_AUTHORS` allowlist disables per-commit full runs, but pushes from
repositories with `automatic_assessments` ON fall back to the legacy impact-gated,
coalesced monitoring policy. That policy applies project `monitoring_policy`, requires
active scope, uses the monitoring budget and allows at most one open monitoring
assessment per repository. For a strictly manual pilot, switch
`automatic_assessments` OFF per repository (or disable project monitoring); manual
Run remains available.

After the owner set an empty allowlist plus `AUTOMATIC_ASSESSMENTS_PER_HOUR=5` and
`AUTOMATIC_ASSESSMENTS_PER_DAY=20` on 2026-10-25 at about 02:03Z, the deferred queue
drained from 386 to 0 and only one new assessment completed in the following ~10
minutes. If per-commit full runs are re-enabled, keep
`AUTOMATIC_ASSESSMENTS_PER_DAY` at or below 20 and enable retention first.

## What ships in this change

- `backend/maintenance.py` — `python -m backend.maintenance report|apply|loop`.
- `GET /api/v1/ops/storage` + `/ops/storage/prometheus` + `POST /api/v1/ops/maintenance`
  — bearer-token ops surface (`OPS_API_TOKEN`; unset = endpoints return 404).
- A maintenance sidecar in `deploy/railway/start.sh` (`MAINTENANCE_ENABLED=true`
  default, `MAINTENANCE_INTERVAL_S=3600`).
- Bounded retention with safety guards, **shipping in dry-run mode**
  (`RETENTION_DRY_RUN=true`) so the first cycles report what would be deleted without
  deleting anything.
- Threshold alerts at 70% / 80% / 90% of `POSTGRES_VOLUME_BYTES`, logged and posted to
  `OPS_ALERT_WEBHOOK_URL` when set.

## Rollout

1. Set `OPS_API_TOKEN` (any long random string) and `POSTGRES_VOLUME_BYTES`
   (the volume size shown on the Railway Postgres service → e.g. `5368709120` for 5 GiB).
2. Optionally set `OPS_ALERT_WEBHOOK_URL` (a Slack incoming webhook or similar).
3. Deploy. The sidecar runs hourly reports + dry-run retention; the JSON report is also
   available on demand:

   ```sh
   curl -H "Authorization: Bearer $OPS_API_TOKEN" \
     https://<backend-host>/api/v1/ops/storage | jq .volume, .tables[:10], .reclaimable
   ```

4. Review `reclaimable` + `payloads_by_kind`. When comfortable, set
   `RETENTION_DRY_RUN=false`. From then on each hourly cycle deletes in bounded batches
   (`RETENTION_BATCH_LIMIT`, default 5000) inside short transactions.
5. A one-shot manual cycle: `python -m backend.maintenance apply --apply` inside the
   container (`railway run` / Railway shell) or `POST /api/v1/ops/maintenance`
   `{"dry_run": false}`.

## Retention policy (what ordinary cleanup may and may not delete)

| Data | Default | Notes |
|---|---|---|
| `sessions` expired | always | expired sessions are unusable anyway |
| `idempotency_keys` | 7 days | replay protection window |
| `jobs` terminal rows | 30 days | `queued`/`running` rows are never touched |
| `webhook_deliveries` | 90 days | redelivery uses GitHub's API, not this table |
| `monitoring_events` | 180 days | decision history |
| `reassessment_requests` settled | 180 days | `queued`/`started` never touched |
| `runtime_windows` | 30 days | RL signals only read the last ~24h |
| `index_payloads` orphaned | 24h grace | payloads never marked ready (failed indexing) |
| `index_payloads` superseded | keep 5 ready snapshots/repo, 7d grace | payloads + `index_snapshots` rows deleted; `snapshots` metadata kept |
| `assessment_events`/`agent_runs` | **disabled** (`RETENTION_ASSESSMENT_DETAIL_DAYS=0`) | pilot transcript evidence; opt-in only |
| `audit_events` | **never** | append-only, enforced by DB triggers |
| `findings`/`approvals`/`authorized_scopes`/`assessments` rows | **never** | customer evidence and approvals |
| artifact files | orphans only, 24h grace | files on disk with no `artifacts` row |
| bare clone `refs/ari/*` | keep 50 newest/repo | then `git gc --prune=now` |

Hard protections in code (not just convention):

- Snapshots referenced by any **non-terminal assessment** are never pruned.
- Payloads younger than `RETENTION_SNAPSHOT_GRACE_DAYS` are never pruned, so a queued
  `register_verified_regression`/`deliver_verified_patch` job cannot lose its inputs.
- `index_payloads`/`index_snapshots` pruning runs per project inside a transaction that
  sets `ari.project_id`, honoring the FORCE RLS boundary (same predicate as
  `intel_store.py`).
- Every real prune writes an `audit_events` row (`action = "maintenance.retention"`)
  with per-class counts.
- `purge_project` remains the authoritative "delete everything for a project" path;
  retention never deletes live project data.

## Reading the report

`report["volume"]` — `used_bytes` = `pg_database_size(db)` + WAL bytes,
`pct_used` vs `POSTGRES_VOLUME_BYTES`, plus `alerts`. `tables` lists every user table
by `pg_total_relation_size` with live/dead tuple counts and vacuum timestamps —
`n_dead_tup / n_live_tup` is the bloat signal. `payloads_by_kind` shows which
intelligence component stores the most bytes per assessment. `reclaimable` is the
row/byte count the next real retention cycle would free (reusable *inside* Postgres).

## Diagnosing the current volume without this code deployed

From `railway run -- psql "$DATABASE_URL"` (or a psql shell inside the service):

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS db,
       pg_size_pretty(sum(size)) AS wal FROM pg_ls_waldir();
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total,
       n_live_tup, n_dead_tup
  FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;
SELECT status, count(*), pg_size_pretty(sum(pg_column_size(payload)
     + pg_column_size(result) + pg_column_size(last_error))) FROM jobs GROUP BY 1;
SHOW autovacuum; SELECT count(*) FROM pg_replication_slots;
```

(RLS note: `index_payloads`/`index_snapshots` reads need
`SELECT set_config('ari.project_id', '<project>', true)` per transaction unless the
connecting role bypasses RLS.)

## Reclaiming disk

Row deletes make room *inside* Postgres — the Railway-reported volume usage does not
drop until the data files shrink:

- Autovacuum/`VACUUM` reuses pages but doesn't return them to the OS.
- `VACUUM FULL`/`pg_repack` return space but lock/rewrite tables — run only after a
  verified backup, off-peak, and ideally per-table starting with `index_payloads`.
- If growth outruns retention (payloads per commit × commit rate), raise the Railway
  volume cap — the `storage` report's growth over successive runs is the honest sizing
  signal. Cleanup + a modest headroom bump is cheaper than an emergency resize.

## Backups and restore

Railway Postgres snapshots live under the service's **Volumes/Backups** (plan-dependent).
Before any destructive maintenance (`VACUUM FULL`, repack, or the first non-dry-run
cycle): create a fresh snapshot, and verify restore on a *separate* scratch Postgres —
`pg_dump --format=custom` → `pg_restore` into a scratch service, then
`SELECT count(*)` spot-checks on `assessments`, `findings`, `audit_events`.

## Alert thresholds

Fixed at 70% (early warning), 80% (act soon — review reclaimable + consider a volume
bump), 90% (urgent). Each crossing logs `postgres storage alert` and posts one JSON
payload to `OPS_ALERT_WEBHOOK_URL` per process-level crossing. External monitoring can
alternatively scrape `GET /api/v1/ops/storage/prometheus` (Grafana Agent, Prometheus,
or any HTTP poller) or run a scheduled check against `/api/v1/ops/storage`.
