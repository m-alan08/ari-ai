# ADR-0023: Job execution lifecycle, effect guard and fair claims

Status: accepted (pending review on `feat/devin-execution-reliability`)

## Context

ADR-0005's queue gave leases, retries and dead-lettering, but status changes were
unrecorded, a cancelled job whose worker died stayed `running` forever, there were
no deadlines or per-project limits, one project could starve others, and a crash
during an external call (e.g. a live control adapter `apply`) led to the call being
replayed by the next claim.

## Decision

Extend the existing `jobs` table (migration `0016`, additive): new statuses
`waiting`, `timed_out`, `recovery_required`; columns for project, prerequisite,
deadline, timings, effect state and recovery diagnostics; an append-only
`job_events` history; a `worker_heartbeats` registry. Status changes go through a
validated transition table. Handlers declare external effects with `ctx.effect`;
jobs whose effect outcome is uncertain become `recovery_required` and are resolved
by a project owner. Claims order by priority, then least-busy project, and enforce
per-project/global running limits under an advisory lock. Metrics are SQL queries.

## Consequences

Queue stays at-least-once. Claims serialize while limits are enabled (acceptable at
the ADR-0005 scale). Shared contracts and `SCHEMA_VERSION` are unchanged. Details:
docs/architecture/execution-reliability.md.
