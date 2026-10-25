# Defense Lab Capability Matrix (customer-facing)

*Version: pilot evaluation, October 2026. Every row below reflects code that
exists on `main` today. "Synthetic" rows are clearly flagged; nothing here is a
claim of autonomous production protection.*

## What Defense Lab does today

| Capability | Mode | Signal provenance | What the customer sees |
|---|---|---|---|
| Ingest runtime telemetry from a registered application | Live (authenticated) | **Real** — Bearer-token authenticated, project-scoped HTTP batches | Monitoring events and incident-like groupings |
| Window dedup / replay detection | Live | **Real** | Duplicate windows collapse to a single decision, labelled as replay where applicable |
| Secret-like telemetry quarantine | Live | **Real** | Payloads containing credential-like keys/values are dropped with a reason recorded, never persisted |
| Anomaly detection (per-route stats, principals, auth failures) | Live on ingested telemetry | **Real** — rule-based detectors over the customer's own windows | Monitoring events with `simulated=false` for authenticated sources |
| Shadow policy decisions | Shadow | **Real** decisions, **simulated** execution (nothing executes) | `LivePolicyDecision` rows: rules-based recommendation, or frozen-checkpoint v6 recommendation, or abstention — always `enforcement=NOT_EXECUTED` |
| RL policy inference (frozen v6 checkpoint) | Shadow only | **Simulation-derived** — checkpoint trained/evaluated offline in Gymnasium env | Recommendation action + typed `simulation_derived` provenance + abstention status when inputs are insufficient |
| Quiet-window abstention (ADR-0017) | Shadow | **Real** | When traffic lacks verified ownership context or is too quiet to score, decisions abstain instead of guessing |
| Owned-test enforcement loop | Owned staging only | **Real** — loopback adapter to an application registered as `owned_test` | Rate-limit / increase-logging / block-single-fixture-IP controls with grant, expiry, verify, rollback, emergency-stop |
| First Responder intent | OFF / MONITOR / SHADOW / ASSISTED_RESPONSE | **Real** state machine | AUTO-EXECUTE does not exist and cannot be enabled |
| Scout/Diagnose investigative agents | Shadow → Assisted (PR #458, open) | **Mixed** — real agent loop over the authorized pinned snapshot; typed output persisted as *unverified* evidence | Invocation records with provider mode, cost ledger, cancellation; confirmation never becomes "confirmed" from LLM output |
| Pilot report + unsupported-claims disclaimer | Live | **Real** | Report carries `PILOT_UNSUPPORTED_CLAIMS` so the demo cannot overstate protection |

## What Defense Lab does NOT do

| Area | Status | Honest label |
|---|---|---|
| Production network containment (isolate, firewall, block ranges/CIDRs) | Not implemented — hard-denied in code | UNSAFE kinds return `abstain=True` and cannot be authorized |
| Account/principal disabling | Not implemented — hard-denied in code | `block_principal`, `disable_application`, `credential_rotation` denied |
| Production infrastructure modification (deploy, db_mutation, patch) | Not implemented — hard-denied in code | Denied at the 15-check gate regardless of grants |
| Enforcement outside `owned_test` applications | Not possible | `environment_owned_test` check; `production`/`staging` environments fail the gate |
| Enforcement to non-loopback destinations | Not possible | Channel host must be literal `127.0.0.1`, port 1024–65535, fixed `CONTROL_PATH`; adapter re-checks before send |
| Blocking real customer IPs | Not possible | `block_source` accepts only the isolated fixture IP; no CIDR/ranges |
| Model-granted permissions | Not possible | `AuthorizationInput` has no field for model output; a typed `usr_*` `claimed_actor_id` fails `authenticated_session` |
| Confirmed threat verdicts | Not produced | Verification requires independently executed evidence; LLM text stays `unverified` |
| Autonomous first-responder execution | Not available | `AUTO-EXECUTE` is rejected by contract validation |
| Live Claude agent loop on `main` | Not wired | `provider_mode="none"` until PR #458 merges (fake/scripted provider only) |

## What a pilot customer must install/configure

| Step | What | Where |
|---|---|---|
| 1 | Register the application as `owned_test` (the only environment enforcement can target) | `POST /api/v1/projects/{id}/applications` |
| 2 | Create an application-bound ingest token (one-time display) | `POST /api/v1/applications/{id}/ingest-tokens` |
| 3 | Send one-minute aggregated windows (route, request counts, status classes, auth failures, principals) with `Authorization: Bearer` | `POST /api/v1/ingest/runtime/{projectId}` |
| 4 | (Owned-test enforcement only) create a control grant naming exact kinds + duration cap + hourly budget | Controls API, owner role required |
| 5 | (Owned-test enforcement only) register the loopback channel + confirm the one-time channel secret | Controls API |
| 6 | (Optional) set First Responder intent to MONITOR or SHADOW | First-responder API |

## What Ari cannot yet observe

- **Traffic it never sees**: only windows the customer posts are analyzed; there is
  no passive tap, agent, or eBPF collector. Gaps in reporting are blind spots.
- **Payloads/bodies**: ingestion is aggregate counters per route — no request
  bodies, headers, or per-request data by design (quarantine drops anything that
  looks like a secret, including credential-looking *values*).
- **Principal identity beyond the window's principal counts**: no session,
  account, or user-object visibility.
- **Environment/context ownership** unless the reporter supplies it — unverified
  or missing ownership context forces the quiet-window abstention.
- **Pre-ingestion provenance**: windows are trusted aggregates; tampering before
  the POST boundary is outside the trust model (tokens are revocable; replay is
  detected as duplicate windows, not adversarial forgery).

## Signal provenance legend

- **Real**: derived from authenticated customer/staging telemetry or deterministic
  platform code.
- **Simulation-derived**: produced by the frozen offline RL checkpoint; labelled
  `simulation_derived=true` and never executes.
- **Synthetic**: scripted/fake provider or fixture content (marked in UI as
  "Sample layout only" / `provider_mode=fake`) — shown for layout/demo only.
