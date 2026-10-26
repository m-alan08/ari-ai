# 13 — Backend API design

Status: **specified**. Directory: `backend/`. Base path `/api/v1`.
Models from [12](12_DATA_MODELS_AND_CONTRACTS.md). The OpenAPI document generated
by FastAPI is the source for frontend client generation.

## Conventions

- JSON only; `snake_case` fields; IDs per [12](12_DATA_MODELS_AND_CONTRACTS.md).
- Auth: session cookie (HttpOnly, Secure, SameSite=Lax) after GitHub OAuth;
  CSRF token header `X-CSRF-Token` on state-changing requests.
- Pagination: cursor-based `?limit=50&cursor=…` → `{items, next_cursor}`.
- Errors: `{"error": {"code": "SCOPE_DENIED", "message": "…", "details": {…}, "request_id": "…"}}`
  with HTTP status 400/401/403/404/409/422/429/500.
- Idempotency: `Idempotency-Key` header on POSTs that start work. Assessment
  creation binds the cached request to the project URL and payload, serializes
  overlapping same-user/key starts in PostgreSQL, and returns the same assessment
  and job IDs. Reusing a key for a different project/payload returns 409. Failed
  transactions do not reserve a key.
- Every state-changing endpoint writes an `AuditEvent`.
- Authorization: every resource is resolved through `org_id` membership; no
  endpoint takes `org_id` from the client without checking membership.

## Endpoints

### Auth & session
| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/github/login` | Start OAuth |
| GET | `/auth/github/callback` | Complete OAuth, set session |
| POST | `/auth/logout` | End session |
| GET | `/me` | Current user + memberships |

### Projects & repositories
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/projects` | List / create |
| GET/PATCH | `/projects/{project_id}` | Read / update settings |
| GET/POST | `/projects/{project_id}/repositories` | List / connect (GitHub App installation) |
| POST | `/repositories/{repo_id}/snapshots` | Request indexing at `commit_sha` |
| GET | `/repositories/{repo_id}/snapshots?status=&limit=&cursor=` | List, newest first (`status=ready` for usable indexes) |
| GET | `/snapshots/{snapshot_id}` | Status and stats |
| GET | `/github/repositories` | Repos the caller can reach through App installations |
| GET | `/repositories/{repo_id}/commits` | Recent commits, read live via the installation token |
| GET | `/github/commit?repo=owner/name&sha=` | One commit + per-file diff via the requester's installation (used by live analysis) |

### Intelligence (read)
| Method | Path | Purpose |
|---|---|---|
| GET | `/snapshots/{id}/entry-points` | Attack surface |
| GET | `/snapshots/{id}/symbols?query=` | Search |
| GET | `/snapshots/{id}/expectations` | List |
| PATCH | `/expectations/{exp_id}` | Confirm / edit / reject (writes `human_input` evidence) |

### Scope & approvals
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/projects/{id}/scopes` | List / request scope |
| POST | `/scopes/{scope_id}/approve` | Approve (approver ≠ requester rule) |
| POST | `/scopes/{scope_id}/revoke` | Revoke; running assessments stop |
| GET | `/projects/{id}/approvals?status=pending` | Inbox |
| POST | `/approvals/{apv_id}/decision` | Approve/decline remediation or PR |

### Assessments
| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/assessments` | Start `{snapshot_id or commit_sha, scope_id, budget?}` |
| GET | `/assessments/{asm_id}` | State, usage, summary |
| GET | `/assessments/{asm_id}/events` | **SSE** progress stream |
| GET | `/assessments/{asm_id}/events/log?after=&limit=` | Durable JSON event log, oldest first; `next_cursor` = last `seq` when more exist (pass as `after`), `null` at the end |
| POST | `/assessments/{asm_id}/cancel` | Cancel |
| GET | `/assessments/{asm_id}/runs` | AgentRuns (no raw prompts by default) |
| GET | `/assessments/{asm_id}/report` | Final report JSON (Markdown export later) |
| GET | `/assessments/{asm_id}/rules-of-engagement` | RoE issued at SCOPE_CHECK (NIST SP 800-115 App. B alignment); digest re-checked; 404 when none |

### Findings & remediation
| Method | Path | Purpose |
|---|---|---|
| GET | `/projects/{id}/findings?status=&kind=` | List |
| GET | `/findings/{fnd_id}` | Detail incl. evidence chain |
| GET | `/findings/{fnd_id}/patches` | PatchCandidates + VerificationResults |
| GET | `/artifacts/{artifact_id}` | Download diff/log (authz-checked, redacted) |
| POST | `/patches/{pat_id}/pull-request` | Open PR (requires approved `pr` approval) |
| POST | `/findings/{fnd_id}/accept-risk` | Mark accepted risk with reason |

### Monitoring & RL
| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/github` | GitHub App webhook (signature-verified, no session) |
| POST | `/ingest/runtime/{project_id}` | Runtime events (ingest token) |
| GET | `/projects/{id}/events?limit=&cursor=` | Monitoring feed, newest first; `next_cursor` when older events exist |
| GET | `/projects/{id}/defense-recommendations?limit=&cursor=` | RL recommendations (labeled), newest first; `next_cursor` when older exist |
| POST | `/projects/{id}/applications` | Register a sandbox or owned-test staging app (owner; shadow mode; optional `rl_gated_pilot`) |
| GET | `/projects/{id}/applications` | Connected staging apps (no secrets) |
| POST | `/applications/{id}/enable-rl-gated-pilot` | Server-enforced shadow-only; 409 if leftover controls remain |
| GET | `/projects/{id}/rl-readiness` | Backend RL-gated assessment (A–J). `ready` is not a frontend flag |
| POST | `/applications/{id}/ingest-tokens` | Mint an ingest token bound to that app (shown once) |
| GET | `/projects/{id}/ingest-tokens` | Ingest-token metadata (owner; secret never returned) |
| GET | `/projects/{id}/defense-health` | Recorded worker/ingest timestamps; `status` always `unknown` |
| GET | `/projects/{id}/defense-graph` | Recorded apps and ingest routes; missing checks stay unknown |
| POST | `/defense-recommendations/{id}/reviews` | Analyst accept/reject/needs-investigation (does not execute) |
| GET | `/defense-recommendations/{id}/reviews` | Review history for one stored recommendation |
| GET | `/projects/{id}/defense-reviews` | Review history for the project |

### Ops
| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness (`git_sha`, `schema_version`) |
| GET | `/readyz` | Readiness: db/queue/worker-heartbeats + per-capability assessment readiness (`capabilities_ready`, 503 on `not_ready`/`degraded`) |
| GET | `/diagnostics` | `/readyz` + verified GitHub App call, sandbox SDK/daemon detail, migration head vs `alembic_version`, queue counts, worker rows |
| GET | `/audit?project_id=&limit=&cursor=` | Audit log (owners), newest first; `next_cursor` = last `seq` when older exist; chain verification covers the whole org |

## SSE event shape
```
event: assessment.progress
data: {"assessment_id":"asm_…","state":"VALIDATION","step":"probe","finding_id":"fnd_…",
       "message":"Validating hypothesis 2/5","at":"…"}
```
Event types: `assessment.state_changed`, `assessment.progress`, `finding.updated`,
`approval.requested`, `assessment.completed`, `assessment.failed`.

## Rate limits (MVP)
Per user: authenticated mutations are limited to 60 req/min; GET and HEAD reads use
a separate limit of 600 req/min. Automatic starts count all project triggers toward
10 assessments per rolling hour and 50 per rolling day. Manual starts count only
manual-trigger assessments against the same limits. The global daily spend cap is
$50 across all projects and triggers. Manual starts blocked by a quota return 429
`RATE_LIMITED` with a reset time. Automatic starts blocked by a quota are recorded
as skipped and are not queued or retried. Webhooks are limited to 100/min per
installation. API and webhook limiters are in-process (no Redis) for MVP.

## Not in the API by design
- No endpoint executes arbitrary commands or accepts code to run.
- No endpoint adds a scope target that is not a sandbox build (MVP).
- No endpoint merges or deploys.
- No endpoint applies, isolates, rate-limits, or blocks a customer system. Analyst `accept` of a recommendation is a recorded opinion.
