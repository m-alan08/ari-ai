# backend/

The control plane: the FastAPI `/api/v1` API, PostgreSQL persistence and migrations, the
durable job queue and worker, the hash-chained audit log, the artifact store, GitHub
webhook and App integration, the Claude LLM gateway, and the `AssessmentHost` that runtime
agents use. Owner: **Srihith**. Spec: [docs/13](../docs/13_BACKEND_API_DESIGN.md),
[docs/10](../docs/10_CONTINUOUS_MONITORING.md). Integration guide for the other owners:
[INTEGRATION.md](INTEGRATION.md).

## What exists (verified by tests on this branch)

| Area | Module | Status |
|---|---|---|
| Settings from env | `settings.py` | implemented |
| Schema + Alembic migration (append-only audit trigger) | `db.py`, `migrations/` | implemented; up/down/up tested |
| Durable queue: SKIP LOCKED, leases, heartbeats, retry/backoff, dead-letter, cancel | `jobs.py`, `worker.py` | implemented; crash/lease-expiry recovery tested |
| Execution reliability: validated lifecycle + `job_events` history, `waiting`/`timed_out`/`recovery_required`, `ctx.effect` uncertain-effect guard, reconciliation, fair claims + per-project limits, backpressure, deadlines, retry policies, worker registry, `/projects/{id}/execution`, `/jobs/{id}/history`, `/jobs/{id}/recovery`, `python -m backend.execution` | `jobs.py`, `worker.py`, `execution.py`, `api/execution.py` + migration 0016 | implemented; `tests/test_execution_reliability.py` (real Postgres). See `docs/architecture/execution-reliability.md` |
| Sessions (dev login + GitHub OAuth), CSRF, org authz, rate limits, idempotency | `api/` | implemented; OAuth exchange not exercised against real GitHub |
| Projects, repos, scopes (approver ≠ requester), approvals, assessments, findings, patches, artifacts, audit | `api/routes.py` | implemented |
| SSE with `Last-Event-ID` resume from the durable log | `api/routes.py` | implemented |
| GitHub webhook: size bound, HMAC on raw body, delivery dedupe, redacted payload, enqueue | `api/webhooks.py` | implemented |
| GitHub repository picker | `api/github_repos.py`, `github.py` | sign-in snapshot plus live refresh for trusted personal installations; organization repositories still refresh at sign-in |
| Monitoring pipeline (normalize → correlate → policy → reassessment) | `pipeline.py` + `monitoring/` | implemented; symbol-level impact waits on code-intelligence |
| AssessmentHost gates (transitions, scope, approval, budget, cancel, Verify independence) | `host.py` | implemented |
| Claude gateway (Anthropic SDK, retries, typed errors, usage/USD, redaction, tool loop) | `llm.py` | implemented; live provider exercised once via `make smoke-live` (claude-opus-5-5, 2 turns, $0.009) |
| RL recommendations + explanations via `rl_defense` | `pipeline.py` | integrated and tested against `rl-defense` on main |
| GitHub App installation tokens / compare API | `github.py` | implemented; **not exercised against a real App** |
| Seven-agent orchestrator | — | **not here**: Alan's `security_agents`. Until it lands, assessments end `FAILED / SUBSYSTEM_UNAVAILABLE` |
| Runtime signal ingestion: per-project ingest tokens, `/ingest/runtime/{project_id}`, explainable per-route scoring, labeled `log_anomaly` events, RL signal feed | `api/runtime.py` + `monitoring/runtime.py` | implemented; `owned_staging` / `runtime_demo` are authenticated (`simulated=false`); replay/simulated/drill stay labelled simulated. No production telemetry source |
| Live Defense shadow pilot: staging apps, ingest-token metadata, recorded health/graph, analyst reviews, shadow `LivePolicyDecision` | `api/live_defense.py` + `live_defense/` + migrations 0010–0011 | implemented; health status always unknown; accept does not execute; shadow decisions are NOT EXECUTED |
| Live Defense Phase 4 owned-test controls | `api/live_defense_controls.py` + `live_defense/{authorize,adapters,controls,verify,owned_test_app}.py` + migration 0012 | implemented; 15-check fail-closed gate; HMAC loopback adapter; verify uses real probes; HTTP 200 ≠ effectiveness; production not granted; disconnected apps cannot authorize/execute |
| Live Defense Phase 6 shadow vs enforcement audit | `tests/test_live_defense_phase6.py` | shadow ingest/decision/review/report does not enqueue execute even with grant+channel; queue depth is project-scoped; failed jobs appear on the pilot trail |
| Live Defense Phase 7A first-responder | `api/live_defense_first_responder.py` + `live_defense/{modes,orchestrator,capabilities,first_responder}.py` + migration 0015 | server-enforced OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected; seven-agent registry from real AgentSpecs; OFF cancels FR jobs only; Patch worktree-only; Probe needs AuthorizedScope; handoff delivered only after provider ack |
| Code-intelligence PayloadStore (Postgres) | `intel_store.py` | implemented; integration-tested with Sarosh's IntelligenceService |
| Build-recipe generator: on connect (and on `installation`/`installation_repositories` for already-connected repos) drafts `build_recipe.json` (+ `deploy/sandbox_target.py` when needed) from the code-intelligence index, Claude (`recipe` agent) only for identities/routes/harness, validated deterministically, delivered as a PR on `ari/build-recipe` | `recipes.py` | implemented; real git against a file:// remote in tests, GitHub App and Claude faked; live Claude draft not yet exercised |
| `POST /patches/{id}/pull-request`, `PATCH /expectations/{id}` | — | **not implemented** (MVP cut order / owned by intelligence) |

## Commands

```bash
uv sync                                  # install workspace (Python 3.12)
make test                                # all tests; embedded real PostgreSQL if DATABASE_URL unset
make check                               # ruff + generated-contract freshness
make dev                                 # migrate + API (:8000) + worker against embedded Postgres
docker compose up --build                # alternative: Postgres + migrate + API + worker
uv run python -m backend.replay_webhook --repo owner/name --head <sha> --path app/x.py   # signed local delivery
RUN_LIVE_LLM_EVAL=1 ANTHROPIC_API_KEY=... make smoke-live   # opt-in real Claude call (costs money)
uv run python -m backend.recipes <git checkout> [--sha SHA] [--live]   # dry run: recipe + PR Ari would open
```

Set `TSC=/path/to/tsc` to run the generated-TypeScript compile test.
`TEST_DATABASE_URL` points tests at an existing server. Each run creates a fresh database.

## Rules this package enforces

* No long or LLM work in request handlers. The API enqueues and the worker executes.
* No endpoint executes commands, accepts code to run, merges, or deploys.
* Every state change is authorized in code and appended to the audit chain.
* A missing subsystem, key, checkpoint or GitHub App is reported as an explicit error
  code, never replaced with fabricated output.

## Standards and incidents

Migration 0005 adds persisted incidents. Owner-authenticated incident create/actions use the shared contracts and canonical `standards.incident` rules, with database checks for project links, approval, independent Verify, and regression artifacts. Runtime anomalies correlated with validated findings create reports for human triage, not confirmed incidents. See [incident handling](../docs/security/NIST_INCIDENT_HANDLING.md). Reports include persisted classifications and hardening records. `AssessmentHost.record_standards` updates classifications without reverting finding status/history. Incident SSE provides finite durable replay, not a continuously held connection.

Model-proposed CVSS metrics are explicit provisional assumptions, even when they cite an executed run. A run identifier supplies provenance but does not prove an impact. Such scores list every assumption and require human review; missing metrics remain unscored. Incident closure requires actual regression artifact IDs/hashes; eradication requires a succeeded independent Verify run. Authenticated `owned_staging` / `runtime_demo` events keep `simulated=false`; replay/simulated/drill and simulation-derived recommendations still force simulated incident labeling. There is still no production telemetry source.

Migration revision `0005_incidents` deliberately differs from intelligence-memory PR #39’s `0005`. The runner upgrades all current Alembic heads, allowing independent additive branches to coexist. A real PostgreSQL scratch-database test upgrades both heads, downgrades to base, and upgrades again. Disposable databases from earlier unpublished iterations should be recreated; no production upgrade from an earlier incident prototype is claimed.

Incident eradication recognizes approved security remediation plans only when the plan is persisted on the linked finding and the approval belongs to that assessment/project. Finding-bound reviewer approvals remain supported. Hosted regression source archives now supply the Fortify artifact needed for lessons learned; no hand-made artifact insertion is required.

## Assessment request retries

`POST /projects/{project_id}/assessments` serializes concurrent requests sharing a
user and `Idempotency-Key` within PostgreSQL. An accepted retry returns the same
assessment and job IDs; a changed project or payload returns 409
`IDEMPOTENCY_CONFLICT`. Authorization is checked before a cached response is read,
and failed transactions release the key so an authorized retry can proceed.

The request digest now includes the project URL. Keys cached by earlier versions
use a different digest and return 409 after upgrade; start a new action with a fresh
key. No table, shared model, or response schema migration is required.

Assessment queue jobs and approval resumes can carry only `assessment_id`.
`GET /jobs/{job_id}` resolves their project through the persisted assessment,
then checks membership; the accepted job identifier is readable by members and
remains 404 to other tenants. No redundant payload project field is required.

## Artifact publication and snapshot refresh

Content-addressed artifacts use a unique temporary file per publication, including
concurrent threads in the same process. Publication remains atomic, failed writes
clean up their temporary files, and identical bytes retain the same content hash
with independently authorized artifact records.

`POST /repositories/{repo_id}/snapshots` reuses in-flight work. For ready snapshots
it asks the configured `CODE_INTELLIGENCE_FACTORY` service for
`current_index_version(project_id, repo_id)` and reuses only a ready snapshot for
that parser/coverage/limits version. An upgrade queues normal indexing for a new
snapshot; historical snapshots remain readable and immutable. Returning to an
older configuration reuses its existing ready snapshot. If a ready snapshot exists
but its current version cannot be resolved, the API returns 503
`SUBSYSTEM_UNAVAILABLE` rather than presenting historical facts as current.
Initial requests and failed retries still enqueue work without requiring the
provider to initialize in the HTTP process. No request/response DTO changes.


Scope activation policy (ADR-0016): an owner may explicitly approve their own nonempty sandbox-only scope. Owner membership, pending/unexpired state and audit recording remain mandatory. Other approval subjects retain independent-approver requirements.

### Automatic commit rollout

`GITHUB_AUTO_COMMIT_AUTHORS` enables a per-commit push queue for a comma-separated
list of GitHub author logins (`*` allows any GitHub-linked author). The Railway
entrypoint defaults to `adamissac`; other environments default to the existing
monitoring policy when unset/empty. Eligibility requires both the signed push sender and GitHub API commit-author attribution to match the rollout; author metadata alone never authorizes paid work.

Install the GitHub App with push events, connect the repository, enable project
monitoring, and approve its sandbox scope once. Subsequent eligible pushes queue
full assessments for every commit, including docs/tests-only changes and commits
arriving during another assessment. Duplicate pushes do not create duplicate runs.
Scope approval resumes commits previously blocked for lack of authorization.
The existing commit page polls persisted assessments; opening it or clicking Run
is not required. This applies to new pushes, not historical backfills.

Full automatic assessments use the project’s normal `default_budget`, clamped to
operator caps, rather than the legacy cheaper monitoring budget. Patch/Verify stages that depend
on remediation approval still wait for that approval; automatic triggering does
not fabricate a successful seven-agent run or approve proposed repairs. Identity
API failures retry in the durable queue and eventually dead-letter visibly.
Compare pagination handles truncated pushes; new-branch payloads at GitHub's 2048
commit limit fail explicitly rather than silently dropping commits.


### Automatic/manual repository mode and Haiku 5.5 (#380)

Each connected repository has a persisted `automatic_assessments` switch, default
OFF. ON queues every eligible new commit in the background without a browser visit
or Run click. OFF prevents unstarted automatic work and exposes manual Run on
selected commits; active runs and history remain intact. GET/PATCH
`/api/v1/repositories/{repo_id}` returns Repository; PATCH requires a project owner
and is audited. Migration 0009 adds the flag; schema 0.15.0 adds RepositoryUpdate
and regenerates JSON/TypeScript. Migration 0014 resets existing repository switches and project monitoring to OFF once, requests cancellation of unfinished automatic assessments, and wakes deferred work to finalize as skipped. Schema 0.18.1 makes both defaults OFF. Owners must explicitly enable project defense and repository automatic runs; manual runs remain available. Deploy the backend and frontend together.

The rollout requires both the signed push sender and GitHub-attributed commit
author to match `GITHUB_AUTO_COMMIT_AUTHORS` (initially adamissac). Attribution
alone is email-derived, not authorization. Project start limits default to 5/hour
and 20/day (`AUTOMATIC_ASSESSMENTS_PER_HOUR`, `AUTOMATIC_ASSESSMENTS_PER_DAY`). Excess
commits are durably deferred, not dropped. Full runs use the normal project budget,
clamped to operator caps. Monitoring and approved sandbox scope remain prerequisites;
repair approvals remain required.

Production/staging gateway, Railway startup and frontend quick reads enforce
`claude-haiku-5-5` only, overriding stale production model choices. Errors are
reported instead of substituting another model. Historical recordings retain
the model that produced them. Live production run verification remains outstanding.
