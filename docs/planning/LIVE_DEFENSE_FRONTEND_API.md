# Live Defense frontend — API contracts (Phases 1–7A, incl. 6.5 RL-gated infra)
# Live Defense frontend — API contracts (Phase 6.5)
# Live Defense frontend — API contracts (Phase 6)
# Live Defense frontend — API contracts (Phase 7A)
# Live Defense frontend — API contracts (Phase 7A + 6.5)

Status: **Phase 7C live authorized agent execution + Phase 6.5 RL-gated infrastructure** integrated on `integration/2026-11-02-release`. Shadow-pilot stays recommendation-only; RL-gated apps are **server-enforced** shadow-only. Owned-test controls remain a **separate** authorization path. First-responder modes are OFF / MONITOR|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE is rejected. Scout and Diagnose may execute a real authorized agent loop; that is not confirmation. Producer: control plane (`backend/`, `shared/` 0.22.0+). Phases 1–6 are extended, not rebuilt. Readiness verdict stays **INFRASTRUCTURE READY — RL BLOCKED**. An RL recommendation is not executed defense. Do not use Controls during a customer shadow-pilot. Isolate is never offered.
Status: **Phase 6.5 RL-gated infrastructure** on
`feat/live-defense-phase6-5-rl-gated-pilot` from `origin/main` `74a0a3c2`.
Shadow-pilot is recommendation-only. RL-gated apps are **server-enforced**
shadow-only. Owned-test controls remain a **separate** authorization path.
Producer: control plane (`backend/`, `shared/` 0.20.0). Verdict stays
**INFRASTRUCTURE READY — RL BLOCKED**. Do not use Controls during a customer
shadow-pilot. Isolate is never offered.
Status: **Phase 6 post-merge audit** on `feat/live-defense-phase6-pilot-audit`
from `origin/main` (PRs #393 and #395 both merged). Shadow-pilot is
recommendation-only. Owned-test controls remain a **separate** authorization
path. Producer: control plane (`backend/`, `shared/` 0.19.0). Phases 1–5 are
extended, not rebuilt: live mode still refuses to mix layout-preview fixtures
with records; Phase 2/3 accept remains non-executing; isolate is never offered.
Authorization uses a **separate** endpoint. Controls are labelled **OWNED TEST
ENVIRONMENT**. Do not use Controls during a customer shadow-pilot.
Status: **Phase 7A first-responder** on `cursor/live-defense-first-responder-0044`
from live `origin/main`. Shadow-pilot stays recommendation-only. Owned-test
Status: **Phase 7C live authorized agent execution** on
`feat/alan/live-defense-phase7c-agent-execution` from live `origin/main`
(Phase 7A merged as #439). Shadow-pilot stays recommendation-only. Owned-test
controls remain a **separate** authorization path. First-responder modes are
OFF / MONITOR|SHADOW / ASSISTED RESPONSE. AUTO-EXECUTE is rejected. Producer:
control plane (`backend/`, `shared/` 0.21.0; 0.20.0 reserved for open #412). Phases 1–6 are extended, not
rebuilt. An RL recommendation is not executed defense.
Status: **Phase 7A first-responder + Phase 6.5 RL-gated infrastructure** merged
on `feat/live-defense-phase6-5-rl-gated-pilot` from live `origin/main`.
First-responder modes are OFF / MONITOR|SHADOW / ASSISTED RESPONSE.
AUTO-EXECUTE is rejected. Producer: control plane (`backend/`, `shared/`
0.21.0 carrying the Phase 6.5 additive set). Phases 1–6 are extended, not
rebuilt. An RL recommendation is not executed defense. Verdict stays
OFF / MONITOR|SHADOW / ASSISTED RESPONSE. AUTO-EXECUTE is rejected. Scout and
Diagnose may execute a real authorized agent loop; that is not confirmation.
Producer: control plane (`backend/`, `shared/` 0.22.0; 0.20.0 reserved for
open #412; 0.21.0 is merged Phase 7A). Phases 1–6 are extended, not rebuilt.
An RL recommendation is not executed defense.

Canonical types: `shared/shared/monitoring.py`, `shared/shared/incident.py`,
`shared/shared/rl.py`, `shared/shared/live_defense.py`, generated TS via
`frontend/transport`.

## Integration audit (Phase 1 on main `53bf03c`, extended here)

| Surface | Real backend | Sample layout (`?preview=layout` / `/live-defense-pilot`) | Empty / missing evidence | Unimplemented |
|---|---|---|---|---|
| Project + 24/7 switch | `GET/PATCH /projects/{id}` | Not used | Switch off is an explicit recorded setting | — |
| Events | `GET /projects/{id}/events` | Preview fixtures, labelled SAMPLE LAYOUT | Empty feed copy; no invented events | — |
| Reassessments | `GET /projects/{id}/reassessments` | Not shown as live counts | Zero is zero | — |
| NIST incidents + timeline | `GET /projects/{id}/incidents`, `GET /incidents/{id}` | Preview incidents | Empty list; unknown correlation when no linked ids | Live DETECTED→EXECUTING machine |
| Stored recommendations + Q ranking | `GET /projects/{id}/defense-recommendations` | Preview ranking | Empty list | — |
| Shadow `LivePolicyDecision` | `GET /projects/{id}/live-policy-decisions` | Not mixed with live | Empty list | Q-values when inference did not run (never synthesized) |
| Occlusion explanation | `GET /defense-recommendations/{id}/explanation` | Preview contributions | Button loads stored explanation; missing fields = unavailable | — |
| RL evaluations | `GET /projects/{id}/rl-evaluations` | Not used | Empty; Lab reads committed bundle | — |
| Staging apps | `GET/POST /projects/{id}/applications` | Sample chrome only | Empty onboarding | Production environment (rejected) |
| Ingest tokens | mint shown once; `GET` metadata never includes secret | Not minted | last-used unavailable until ingest | Re-showing the secret |
| Defense health | `GET /projects/{id}/defense-health` | Not used | `status` always `unknown` | Inferring healthy from HTTP 200 |
| Recorded topology | `GET /projects/{id}/defense-graph` | Illustrative map, labelled sample | List of recorded apps/routes; no decorative chart | Simulator node map as customer topology |
| Analyst review | `POST/GET .../reviews` | Not used | History empty | Accept executing a control |

Unknown correlations (event not on any incident, incident with no recommendation ids) are labelled **unknown**, never invented.

## Honesty rules the API must keep

1. Telemetry is never invented. `MonitoringEvent.simulated` / `Incident.simulated` stay accurate.
2. `DefenseRecommendation.simulation_derived` is `Literal[True]`. Genuine shadow rows are `LivePolicyDecision` (`enforcement: "NOT_EXECUTED"`). Q-values on a live decision are null unless frozen v6 inference actually ran.
3. Q-values are stored scores, not a probability that an attack is happening now.
4. `AnalystReview.accept` still sets `executes: false` and `enforcement: "none"`. It is not the NIST two-person incident approval and is not converted into an RL label. Browser-callable `/apply`, `/isolate`, `/rate-limit`, and `/block` do not exist. Owned-test rate-limit and exact-source block go through `POST /applications/{id}/control-authorizations` and the deterministic gate.
5. `DefenseHealth.status` is always `"unknown"`. HTTP 200 is not a healthy verdict.

## Phase 2 resources (implemented)

### Connected applications

`ConnectedApplication` — sandbox or owned_test only. No production. Shadow mode is always true. Secrets never returned.

- `POST /projects/{id}/applications` (owner)
- `GET /projects/{id}/applications`
- `GET /applications/{id}`
- `POST /applications/{id}/ingest-tokens` → `IngestTokenCreated` (token shown once)
- `POST /applications/{id}/ingest-tokens/rotate` → `IngestTokenRotated` (new secret shown once; prior ids listed)
- `POST /applications/{id}/disconnect` → `ConnectedApplication` (`verification=disconnected`)
- `POST /applications/{id}/shadow-verify` → `ConnectedApplication` from recorded ingest (not a healthy verdict)
- `GET /projects/{id}/ingest-tokens` → page of `IngestTokenView` (owner; no secret)
- `GET /projects/{id}/pilot-report` → `PilotReport` (HTTP 200 is not attack success; never claims shadow prevented breaches)
- `GET /projects/{id}/rl-readiness` → `RlReadinessAssessment` (backend PASS/FAIL/BLOCKED/NOT VERIFIED; `ready` true only if every mandatory category is PASS)
- `POST /applications/{id}/enable-rl-gated-pilot` → `PilotEnablement` (409 if leftover grants/channels/controls remain)

### Recorded health and topology

- `GET /projects/{id}/defense-health` → `DefenseHealth` (`status` always `unknown`)
- `GET /projects/{id}/defense-graph` → `ComponentGraph` from registered apps and ingest routes. `healthy` is never inferred from HTTP 200. `attention` only when the latest recorded runtime score was anomalous.

### Analyst review

- `POST /defense-recommendations/{id}/reviews` → `AnalystReview` (member+; accept does not enqueue jobs)
- `GET /defense-recommendations/{id}/reviews`
- `GET /projects/{id}/defense-reviews`

## Phase 3 resources (implemented)

- `GET /projects/{id}/live-policy-decisions` — cursor-paginated shadow decisions
- `GET /live-policy-decisions/{id}`
- `POST/GET /live-policy-decisions/{id}/reviews` — accept does not execute
- Authenticated ingest source `owned_staging` (application-bound token)
- Deterministic rules in `monitoring/detect.py`; frozen v6 only when serving assumptions hold

## Phase 4 resources (implemented, owned-test only)

- `POST/GET /projects/{id}/control-grants`, `POST /control-grants/{id}/revoke`
- `POST/GET /applications/{id}/control-channel` (HMAC shown once; loopback port only)
- `POST /applications/{id}/control-authorizations` — separate from review; gate is final authority
- `GET /projects/{id}/live-controls`, `GET /live-controls/{id}`
- `POST /live-controls/{id}/rollback` — success only after verify
- `GET/POST /projects/{id}/emergency-stop`, `POST /emergency-stop/{id}/clear`

## Phase 7C resources (implemented, investigative only)

- `GET /projects/{id}/first-responder/invocations` includes `execution` when Scout/Diagnose actually ran (`cost_usd`, `latency_ms`, `tool_call_count`, `execution_integrity`). Missing execution is a skip, never a faked success.
- `executed` on a `FirstResponderDecision` remains false (no LiveControl). Invocation status `executed` is an agent loop, not enforcement.
- Probe/Patch/Verify/Trace/Fortify are still gated; the UI must not label them confirmed.

## Still missing (do not fake)

- Live-defense incident machine (DETECTED → … → EXECUTING on NIST incidents)
- Autonomous enforcement (no human authorization)
- Production enforcement grants

## Authorization

Project members can read. Owners register apps, mint/revoke ingest tokens, and toggle 24/7 defense. Members can record analyst reviews. Non-members get 404 (no existence leak). All mutations are audited.

## Out of scope (still)

- Apply / isolate / rate-limit / block adapters callable from the browser
- Changes under `rl-defense/` training, eval, or checkpoints
- Fake backend that echoes layout-preview fixtures as `/api/v1` JSON
