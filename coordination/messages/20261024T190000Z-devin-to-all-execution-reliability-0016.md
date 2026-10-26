# Devin (for Alan) → all: execution reliability, Alembic 0016

- Branch `feat/devin-execution-reliability` adds Alembic revision `0016` (additive:
  `jobs` columns, statuses `waiting`/`timed_out`/`recovery_required`, tables
  `job_events`, `worker_heartbeats`). No shared contract or `SCHEMA_VERSION` change.
- If your PR also adds a `0016`, whoever merges second renumbers and bumps the head
  pin in `backend/tests/test_parallel_migrations.py`.
- New queue rules for handler authors: wrap external side effects in
  `ctx.effect(key)`; pass `deadline_s` for bounded work; jobs carry `project_id`
  (defaults from payload / assessment) for per-project limits and metrics.
- `live_defense/first_responder.py` (PR #458) is untouched; see
  docs/architecture/execution-reliability.md "Integration after PR #458".
