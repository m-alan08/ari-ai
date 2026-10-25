# Feature matrix — pilot truth table

Status labels (use exactly these in customer material):

- **Live-verified** — executed on the hosted production deployment with
  retained evidence.
- **Implemented** — code on `main` (`d0131167`), covered by executed tests;
  not yet exercised end-to-end on production for this claim.
- **Staging / branch-only** — exists on an open PR or non-production branch;
  not part of what customers get.
- **Simulation-only** — runs in the RL/test environment only; numbers are
  simulated, not observed customer outcomes.
- **Partial** — works for a defined subset; limits stated.
- **Unavailable** — does not exist; must not appear in customer material.

## Pillar 1 — functional code reviewer

| Capability | Status | Evidence / limit |
|---|---|---|
| Commit review vs. intended behavior | Live-verified | 45-case live baseline; functional validations recorded |
| Generated behavioral tests for reported defects | Implemented | `code-review/` suite; trusted test replay |
| Approval-gated repair proposals + fix delivery as PR | Live-verified | Eval fix PRs #499–511 opened and closed by the platform |
| Independent verification of fixes | Implemented | Verify runs fresh sandbox; regression artifacts persisted |
| Non-Python review | Partial | LLM reads text index; no symbol/route/expectation support |
| `auto` fix-delivery to default branch | Implemented, **excluded from pilot** | `DeliveryMode.auto`; settings UI warns before enabling |

## Pillar 2 — seven security agents

| Capability | Status | Evidence / limit |
|---|---|---|
| Seven distinct agent runners + deterministic orchestrator | Implemented | `security-agents/`, `workflow/`; 581 tests passing |
| Live pipeline on owned repos | Live-verified | 45-case baseline: IDOR 8/8 flagged→validated→fixed; vuln 23/24 flagged; median 153 s; ~$1.26 total |
| Executable Probe checks | **Partial — 3 templates** | `access_control.ownership`, `input_validation.bounds`, `access_control.authentication` (`checks/library.py`); other CWEs end inconclusive |
| Scout specialist (trained model) | Implemented | Promoted checkpoint, macro F1 0.349 vs 0.084 static baseline |
| Sandboxed execution | Implemented | Vercel microVM driver (prod) + hardened Docker driver (`--network none` default); local-process driver refuses outside dev/test |
| Scope authorization before any network-capable tool | Implemented | `scope_guard.py`; checks run as code before calls |
| Strict all-seven-roles live success | Partial | Last recorded strict attempt: 6/7 roles (Fortify `SCHEMA_INVALID`); eval IDOR cases traverse the full chain successfully |
| Adversarial paths (timeout/cancel/invalid schema/provider outage) | Implemented | Failure codes + tests; genuine failures stay failed |

## Pillar 3 — monitoring and Defense Lab

| Capability | Status | Evidence / limit |
|---|---|---|
| Signed GitHub webhooks + dedupe + durable queue | Live-verified | Push→assessment ~1–2 min on production |
| Opt-in gates (repo switch + project monitoring + approved scope + author allowlist) | Live-verified | All four are code gates; verified end-to-end |
| Monitoring budgets + global $50/day cap + deferral | Implemented | `auto_commits.py`; deferred runs queue, never drop |
| Authenticated runtime telemetry ingest (per-project `hkt_ing_` tokens) | Implemented | `live_defense` ingest endpoints; secret-like payloads quarantined, not stored |
| Rules-based defense recommendations (shadow, NOT EXECUTED) | Implemented | `LivePolicyDecision` rows, analyst review, pilot-report export |
| RL policy recommendations | Simulation-only | Gymnasium + DDQN, v6 deployed checkpoint; latest retrain **rejected** by promotion gate (availability < floor, did not beat rules) |
| First-responder assisted mode (Phase 7A) | Implemented (staging) | On main; owner-attested deploy mapping; no auto-execute by design |
| Phase 7C authorized Scout/Diagnose execution | **Branch-only** (PR #458 open) | Not part of pilot claims |
| Production enforcement / containment | **Unavailable — excluded** | Never offered in pilot |

## Platform and account capabilities

| Capability | Status | Evidence / limit |
|---|---|---|
| GitHub OAuth login + App install + repo connect | Live-verified | `readyz`: `github_app_configured: true` |
| Org/project/repo/findings/approvals/incidents APIs | Implemented | `require_project` membership on every scoped route; 404 for non-members |
| Scope approve / revoke; assessment cancel | Implemented | `POST /scopes/{id}/approve|revoke`, `POST /assessments/{id}/cancel` |
| Automatic-assessment + monitoring opt-out switches | Live-verified | UI switches backed by `automatic_assessments`, `monitoring_policy.enabled` |
| Usage/cost accounting per run | Implemented | `usage.usd` persisted per assessment; caps enforced in worker |
| Hash-chained audit log | Implemented | `audit_events` per org, owner-only reads, chain verified on read |
| Secret redaction before persistence/model | Implemented | `backend/redaction.py` recursive patterns |
| Incident records (NIST SP 800-61 aligned states) | Implemented | `incidents.py` + `docs/security/NIST_INCIDENT_HANDLING.md` |
| Repo disconnect endpoint | **Unavailable** | No API; access revoked via GitHub-side uninstall; Ari row remains (ops cleanup) |
| Self-serve data deletion | **Unavailable** | No DELETE endpoints; operator-run deletion only |
| Retention/TTL jobs | **Unavailable** | Artifacts, clones, index payloads persist indefinitely until ops removal |
| Subscriptions / billing / metering enforcement | **Unavailable** | Pricing page is a proposal; nothing purchasable |
| Multi-user org invitations | **Unavailable** | Personal org per login only |
| Postgres row-level security | **Unavailable** | Application-level checks only (defense-in-depth gap) |

## Deployment facts customers may ask about

- Frontend: Vercel (`ari-ai-lake.vercel.app`). API + worker + Postgres:
  Railway, artifacts + repo checkouts on a service volume.
- Model provider: **Anthropic** (`llm_mode: live` on `readyz`); repository
  code context is sent to Anthropic — that is the product.
- Execution sandboxes run on Vercel Sandbox microVMs — never on the API
  host.
- GitHub App holds **Contents read+write and Pull requests read+write**
  (write is required for fix delivery; per-call tokens are least-privilege
  and write tokens are pinned to one repository). The deploy doc's
  "Contents: read-only" line is stale — see RISK_REGISTER R-11.
