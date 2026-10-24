# Execution reliability: lifecycle, recovery, limits and metrics

Status: implemented on `feat/devin-execution-reliability` (migration `0016`).
Extends the PostgreSQL queue of ADR-0005 (`backend/backend/jobs.py`,
`backend/backend/worker.py`); nothing was replaced. Queue semantics stay
**at-least-once**: handlers must still be idempotent.

## Lifecycle

| Requested state            | `jobs.status`        | Meaning |
|----------------------------|----------------------|---------|
| Queued                     | `queued`             | runnable at `run_after` |
| Waiting on a prerequisite  | `waiting` (new)      | `depends_on` job has not succeeded yet |
| Running                    | `running`            | leased by `lease_owner` until `lease_expires_at` |
| Completed                  | `succeeded`          | |
| Failed                     | `failed` / `dead`    | `dead` = retry budget exhausted |
| Cancelled                  | `cancelled`          | |
| Timed out                  | `timed_out` (new)    | `deadline_at` passed (before, during or between attempts) |
| Recovery required          | `recovery_required` (new) | external effect outcome uncertain; operator decides |

Every change goes through `jobs.transition()`, which validates against
`jobs.TRANSITIONS`, applies a compare-and-set on the current status and appends a
row to `job_events` (from/to status, attempt, worker, `ErrorCategory`, reason,
diagnostics). `GET /api/v1/jobs/{id}/history` returns that history. Existing
statuses and the `GET /jobs/{id}` fields are unchanged; new fields are additive.

Structured failure categories (`jobs.ErrorCategory`): `transient`,
`infrastructure` (database), `network`, `backpressure`, `permanent`,
`authorization`, `cancelled`, `deadline_exceeded`, `lease_expired`,
`retry_exhausted`, `prerequisite_failed`, `uncertain_side_effect`, `operator`.
`worker.classify()` maps exceptions to a category and retryability.

## Uncertain external effects

A handler wraps each external side effect in `ctx.effect(key)`. Before the body
runs, `jobs.begin_effect` commits `effect_state='in_flight'` in its own
transaction (refused if the lease, cancellation or deadline no longer allows it).
On normal exit the effect is `settled`.

* Body raises, or the worker dies while the effect is set → the job becomes
  `recovery_required` (from `fail()` or from reconciliation). It is **never**
  re-claimed: the claim query excludes expired leases with an effect set.
* Body raises `EffectNotApplied` → the effect definitely did not happen; normal
  retry rules apply.
* The original worker can still `complete()` a `recovery_required` job (it
  observed the outcome); a different worker cannot.

`live_defense_execute` passes `ctx.effect` into `execute_control`, which guards the
adapter `apply` call. Before this change a worker crash during that call rolled
back the control's `executing` transition and the re-claimed job would call the
adapter again.

Operators resolve with `POST /api/v1/jobs/{id}/recovery`
(`{"resolution": "applied" | "not_applied" | "retry", "note": ...}`), project
**owner** only, audited as `job.recovery_resolved`. `retry` re-queues the job; the
handler re-runs every authorization, mode and cancellation gate it normally runs,
and `retry` is refused once cancellation was requested.

## Recovery (`backend.execution.reconcile`)

Runs at worker start, from `drain()`, and every `WORKER_RECONCILE_INTERVAL_S` in
the worker loop. A transaction-scoped try-lock lets one reconciler run at a time;
rows being mutated by a live worker are skipped (`SKIP LOCKED`). It never runs a
handler. For each expired lease, in order:

1. effect set → `recovery_required` with diagnostics (previous owner, last
   heartbeat, effect key, required action);
2. cancellation requested → `cancelled` (previously such rows stayed `running`
   forever because the claim query skips cancelled rows);
3. deadline passed → `timed_out`;
4. final attempt → `dead`;
5. otherwise → `queued` (next claim increments `attempts`).

It also times out pending jobs past their deadline, promotes/fails `waiting` jobs,
and prunes worker rows not seen for 10 × `WORKER_STALE_S`.

| Failure                         | Handling |
|---------------------------------|----------|
| Worker killed / host restart    | lease expires → reconcile/claim recovers; effect jobs → `recovery_required` |
| Graceful SIGTERM                | unchanged: finish current job, then exit and deregister |
| Orchestrator interruption       | handler exception → classified retry; assessment orchestrator still re-emits by `step_key` |
| DB failure in handler           | `infrastructure`, retried with backoff |
| DB failure finalizing a job     | `complete`/`fail` retried `WORKER_FINALIZE_RETRIES` times; then lease expiry recovers |
| Network failure                 | `network`, retried; inside an effect → `recovery_required` |
| Heartbeat loss                  | handler sees `JobCancelled` on next `check_cancelled()`; `complete` refuses |

## Resource and concurrency limits

* `JOB_MAX_RUNNING_PER_PROJECT` (default 4): live leases per project. Enforced in
  the claim statement; claims serialize on an advisory lock while any limit is on,
  so the limit holds across workers (claims are one short statement).
* `JOB_MAX_RUNNING_GLOBAL` (default 0 = off; the worker count already bounds it).
* Fairness: claim order is `priority`, then the project with the fewest running
  jobs, then age. A project with 1,000 queued jobs cannot starve another project
  at the same priority. Jobs without a project are not limited.
* `JOB_MAX_RUNNING_EMERGENCY` (default 2): reserved claim capacity for the
  emergency/cleanup kinds `live_defense_emergency_stop`, `live_defense_rollback`
  and `live_defense_expire`. These bypass `JOB_MAX_RUNNING_PER_PROJECT` and
  `JOB_MAX_RUNNING_GLOBAL` so emergency-stop/rollback/expiry remain claimable
  under ordinary saturation, while staying bounded by their own limit and not
  consuming ordinary slots. New apply operations (`live_defense_execute`) and
  `live_defense_verify` deliberately do NOT get this exemption. Setting it to 0
  folds emergency kinds back under the ordinary limits (not recommended).
* `JOB_MAX_PENDING_PER_PROJECT` (default 1000): `enqueue` raises
  `QueueBackpressure` (HTTP 429 with `Retry-After`). Dedupe hits and
  `CONTROL_JOB_KINDS` (including emergency stop) are exempt. Soft limit: concurrent
  enqueues can overshoot by the number of concurrent writers.
* Deadlines: `enqueue(..., deadline_s=)` bounds the whole execution, or
  `deadline_at=` to propagate an existing absolute deadline across follow-on
  enqueues (never extended by redelivery). Expired jobs are not claimed;
  heartbeats surface the deadline to the handler (`JobDeadlineExceeded`); a retry
  whose backoff would land past the deadline ends `timed_out`.
* Retry budgets: `max_attempts` plus per-kind `RetryPolicy` (exponential, capped,
  positive jitter; default reproduces the original `min(300, 2**n)` curve).
  Deferrals (`defer_count`) do not consume the budget — the effective attempt
  count is `attempts - defer_count` in `claim`, `fail` and reconciliation.
* Deferral: `jobs.defer(conn, job_id, owner, delay_s=|depends_on=)` returns a
  claimed job to `queued` (or `waiting` onto a durable prerequisite) through the
  validated transition path with a `job_events` record. It refuses after an
  uncertain effect (`effect_state` set — never replay an unknown outcome), and
  cancellation/deadline take precedence (`cancelled`/`timed_out`). This is the
  supported contract for handlers that must re-emit themselves (e.g. Diagnose
  scheduling in PR #458).
* Resource release: the lease is always released by `complete`/`fail`; the
  heartbeat thread is stopped in `finally`; worker rows are deregistered on exit.

## Completion fencing

Ordinary `complete()` and `fail()` require the caller's lease to still be valid
(`lease_expires_at > now()` on the database clock) — a worker whose lease
expired, even before reconciliation notices, cannot report success or record
failure outcomes; the lease-expiry path owns that recovery. The single audited
exception is the original owner's late completion of a `recovery_required` job
(its settled uncertain effect, still attributed to that owner).

## Observability

No new backend (docs/19): figures are SQL over `jobs`, `job_events` and
`worker_heartbeats`.

* `GET /api/v1/projects/{id}/execution` (project viewer+): status counts, queue
  wait p50/p95/max and oldest pending age, lease ages and expired leases, execution
  duration, retry counts, cancellation outcomes (before start / while running /
  after lease loss / pending), failure categories, recovery required/resolved,
  stuck count by reason, plus the stuck job list.
* `uv run python -m backend.execution stats|stuck|reconcile` (operators): the same
  plus fleet-wide worker counts (active/busy/stale).

Stuck reasons: `recovery_required`, `lease_expired`, `deadline_exceeded`,
`queued_starved` (runnable for > `JOB_STUCK_PENDING_S`), `waiting_on_prerequisite`.

## Runbook

* **`recovery_required`**: read `recovery` on the job (effect key, previous
  owner). Check the external system (for controls: adapter state / control row).
  Resolve `applied`, `not_applied`, or `retry`.
* **`lease_expired` stuck count rising**: workers are dying or overloaded; check
  worker logs and `workers.stale`. Reconcile runs automatically; run it manually
  with `python -m backend.execution reconcile`.
* **`queued_starved`**: per-project limit or worker count too low, or a
  high-priority flood. Compare `leases.running` with `limits`.
* **429 `RATE_LIMITED` from enqueue**: the project has `JOB_MAX_PENDING_PER_PROJECT`
  jobs pending; drain or raise the limit.

## Security boundaries

No new execution capability. Recovery never runs handlers; `retry` goes through the
handler's own gates; OFF-mode cancellation (`modes.cancel_first_responder_jobs`)
covers `queued`, `waiting` and `running` first-responder jobs and flags
`recovery_required` ones `cancel_requested` without erasing their effect
evidence (operator `retry` is then refused); control authorization re-checks are
untouched; control jobs are exempt from
backpressure so emergency stop cannot be refused. Project-scoped endpoints use
`require_project`; non-members get 404.

## Operator notes for control jobs

Resolving a control job `applied` marks the **job** succeeded; the `LiveControl`
record itself is advanced inside the handler's transaction, which was rolled back
when the worker died — so it can still show `authorized` until its expiry. Check
the adapter state when deciding, then reconcile the control record separately.
`ctx.effect` is per external call; do not nest it. Once any effect is recorded
`settled`, every later failure of that job ends `recovery_required`, because a
replay could re-apply it — keep effect-adjacent handlers idempotent anyway.

## Limitations

* At-least-once, not exactly-once. Handlers that do external work without
  `ctx.effect` keep the previous replay-on-crash behaviour (they must be idempotent).
* A job whose handler committed its domain write but whose `complete` could not be
  recorded is re-run (or, with an effect set, flagged `recovery_required`).
* Claim serialization is a deliberate trade-off for strict limits at the ADR-0005
  scale (a handful of workers); revisit with the queue interface if that changes.
* `live_defense_agent_step` / first-responder handlers (PR #458 territory) are not
  yet wrapped with `ctx.effect`; see "Integration after PR #458".

## Integration after PR #458 (F1/F7 contract with Grok)

PR #458 owns Scout/Diagnose execution in `live_defense/first_responder.py`. This
work does not edit that file. The agreed interface for the F1/F7 findings:

* **Effect reservations (F1):** every billable provider request runs inside
  `ctx.effect(key)` with a durable, unique key —
  `provider:<job_id>:<attempt>:<request_seq>` (`job_id`+`attempt` is already
  durable via the `job_events` claim record). The reservation commits *before*
  the call; a worker loss or crash between send and response leaves
  `recovery_required`, never an automatic replay of an unknown billable
  outcome. Resolve via the existing recovery API, keeping the per-request
  attempt evidence in `job_events`/`recovery`. Guard billable calls only —
  canonical-intelligence reads are not effects.
* **Durable overall deadline (F7):** the first-responder deadline is computed
  once at invocation creation and passed to every `live_defense_agent_step`
  enqueue as `deadline_at=` (new enqueue parameter — propagating an absolute
  deadline is a queue contract, not a handler concern). Pending waits,
  redeliveries and retries share it; provider calls get the remaining slice.
* **Deferral (F4):** Diagnose scheduling uses `jobs.defer(depends_on=)` rather
  than rewriting status, so the deferral is audited and never burns attempts.
* **Finalization:** before committing a terminal invocation result, the handler
  rechecks mode/cancellation — `ctx.check_cancelled()` already reads the live
  row; OFF-cancellation must not be overwritten by a late submit (F8, Grok
  side, but the queue recheck primitive is `heartbeat_state`/`check_cancelled`).
* `FirstResponderInvocation.job_id` already links invocations to `jobs`, so
  `/jobs/{id}/history` and the project execution figures cover them without
  schema changes.

## Evidence (measured on this branch)

* Starvation: 200 queued jobs from one project, then 5 from another, 4 workers.
  Before (FIFO ordering, no project limit) the quiet project's jobs were claimed at
  positions 201–205; after (fair ordering, limit 4) at 2, 4, 6, 8, 10.
* Cancel after worker death: before, a cancel-requested job with an expired lease
  stayed `running` indefinitely; after, the first reconcile marks it `cancelled`
  (`test_reconcile_never_runs_handlers_or_bypasses_cancellation`).
* Crash during control `apply`: before, the next claim re-ran the adapter call;
  after, the job is `recovery_required` and never re-claimed
  (`test_worker_death_during_effect_is_never_reclaimed_automatically`).