# Defense Lab Evaluation Report

*Author: Adam's session (Defense Lab owner audit). Date: 2026-10-24.
Scope: `rl-defense/`, `backend/backend/live_defense/`, runtime ingestion,
`docs/LIVE_DEFENSE_*`, PRs #412 and #458, incident/control UI.
Verdict language follows the repo's evidence conventions: only executed tests
and code paths are called "verified".*

## 1. Go / No-Go

**GO — for a recommendation-only pilot plus the owned-test staging loop, with
the conditions in §10.** The safety story is enforced in deterministic code, not
documentation: no model output can grant permissions, unsafe containment kinds
are hard-denied, every enforcement attempt passes a 15-check gate and a
loopback-only signed adapter, and frozen-checkpoint inference abstains rather
than guesses.

**NO-GO — for any production enforcement, autonomous protection claim, or
merging PR #412's RL-gated posture as "ready".** Category J (hosted serving) is
NOT VERIFIED; verdict stays `infrastructure_ready_rl_blocked`.

## 2. Feature inventory by mode

| Lane | Implementation | Execution? |
|---|---|---|
| **Simulation-only (research)** | `rl-defense/` Gymnasium env, DDQN training, baselines, eval harness, frozen v6 checkpoint | Never touches real systems; emits `simulation_derived` recommendations |
| **Shadow mode** | `backend/backend/live_defense/engine.py` `process_shadow`; rules + optional v6 producers | Writes `LivePolicyDecision` only; `enforcement=NOT_EXECUTED`; no control rows, no adapter calls |
| **Owned staging (enforcement)** | `controls.py` gate → `adapters.py` HMAC loopback → verify/rollback/expire/emergency-stop jobs | Real HTTP, but only to `127.0.0.1` `CONTROL_PATH`, against applications registered `owned_test`, behind an unrevoked grant |
| **Assisted response (First Responder)** | `orchestrator.py` deterministic `decide`; `first_responder.py` jobs; OFF/MONITOR/SHADOW/ASSISTED_RESPONSE | On `main`: records honest `skipped_*` statuses, `provider_mode="none"`. PR #458 (open) executes real Scout/Diagnose loops under a budget+ledger; Probe/Patch stay gated; Patch deploy refused |
| **Production enforcement** | **Does not exist.** `environment_owned_test` check fails any non-`owned_test` application | — |

## 3. Reproduced evidence (this session, on `main`)

| Evidence | Command | Result |
|---|---|---|
| rl-defense suite | `pytest tests` (569 tests) | **569 passed** (~740s) |
| v6 artifact integrity | `verify` over `results/live-retrain-v6/EVIDENCE.json` | OK — 3 artifact hashes incl. pinned `candidate_online.pt` sha256 `8113a37f…` |
| Live loop vs frozen v6 | `live.run` 5 episodes | 0 compromised reads; blocks only on `cus_bob` scenario |
| backend live-defense suite | `pytest tests/test_live_defense_*.py` | **96 passed** (22.8s) |
| New pilot safety suite | `pytest tests/test_live_defense_pilot_safety.py` | **37 passed** (this PR) |

v6 frozen report (`results/live-retrain-v6/REPORT.md`): paired gain +9.51
CI[8.17, 10.79], compromise 0.185, availability 0.952, false-disruption 0.051,
beats rules baseline −5.30 vs −7.58 — verdict `recommend_promote` **requires
human approval**; earlier v3-gen verdict was `reject`. Lineage/holdout caveats
are disclosed in the report's own PROVENANCE section.

> These are **simulator** numbers. They measure the RL environment's internal
> attack/defense model, not observed prevention of real customer attacks.
> Reported here only as model-selection evidence, per task rules.

## 4. Ingestion verification (`backend/backend/api/runtime.py`)

Verified in code and via `test_runtime_ingest.py` + `test_live_defense_phase3.py`:

- **Authentication**: Bearer token → SHA-256 lookup, `secrets.compare_digest`
  project binding; revoked and disconnected tokens rejected; application-bound
  tokens required for `owned_staging` source.
- **Ownership/tenancy**: token is bound to project (and optionally application);
  cross-project tokens 401; outsider reads 404.
- **Dedup**: per-window idempotency — duplicate `(source, window)` submissions
  collapse; same window from two applications is not a replay.
- **Replay detection**: `replay`/`drill`-style sources are labelled
  `simulated=true` and never feed v6 (`skipped_source`); `latest_route_*`
  helpers exclude `drill`.
- **Rate/size limits**: `ingest:{token}` 60 req/60 s; body ≤ 256 KB.
- **Provenance**: every event records `source`, `simulated` flag, and status
  reason; shadow enqueues only for `AUTHENTICATED_RUNTIME_SOURCES`.

**Finding + fix (this PR)**: `quarantine.secret_like_reason` scanned dict keys
and Bearer/JWT/email *values*, but a credential-looking value under a benign
key (`{"note": "password=hunter2"}`) was persisted. Added `_SECRET_VALUE`
(`password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key = value`)
matching on string values. Quarantine records the reason, never the value.

## 5. Rules-based recommendations and safe abstention

`policy.py`: `decide_inference` returns explicit `InferenceStatus` —
`skipped_source` (unauthenticated/simulated), `skipped_incomplete`,
`skipped_stale`, `skipped_quiet_window` (ADR-0017), `skipped_authorization`,
`skipped_unavailable` (checkpoint load/hash failure), `skipped_schema`,
`skipped_mask`, `skipped_target`. The shadow producer then falls back to rules
(`block_principal`/`escalate_to_human`/`raise_monitoring`/`noop` ordering) or a
pure abstention decision — **all still `NOT_EXECUTED`**. The checkpoint itself
is hash-pinned (`RL_CHECKPOINT_SHA256` required outside dev/test) and loaded
with same-bytes verification.

## 6. Containment audit — "no unapproved enforcement"

Layered proof, all in deterministic code:

1. **Kind allowlist** (`authorize.py`): `isolate`, `block_principal` (and long
   variants), `credential_rotation`, `firewall`, `deploy`, `db_mutation`,
   `patch`, `disable_application` → `allowed=False, abstain=True` even with a
   perfect grant/channel.
2. **15-check gate**: every control re-checks session (server-provided only —
   `claimed_actor_id` rejected), project, registered+connected `owned_test`
   app, grant (exact app+kind, duration cap, hourly budget, unrevoked,
   unexpired), channel allowlist, kill-switch, unresolved control.
3. **Adapter** (`adapters.py`): re-derives URL from parts — literal
   `127.0.0.1`, port 1024–65535, `CONTROL_PATH`; rejects DNS/userinfo/query/
   fragment/scheme≠http; re-checks fixture IP for `block_source`; HMAC
   `ts.nonce.id.sha256(body)`; 2 s timeout.
4. **Worker re-gates** before execute; failure marks control failed + triggers
   rollback (409 conflict aside). Verify/rollback/expire/emergency-stop are
   separate audited jobs.
5. **API surface**: no browser-callable enforcement route exists
   (`test_no_browser_callable_enforcement_routes`); every control endpoint is
   owner-scoped.
6. **First responder OFF** cancels only first-responder jobs
   (`cancel_first_responder_jobs` filters FR kinds) — it cannot touch control
   jobs, and OFF aborts in-flight agent steps.

The new `test_live_defense_pilot_safety.py` (37 tests) parametrizes each of
these independently, including "perfect grant + unsafe kind" and "typed actor
≠ session" cases.

## 7. PR #458 review — first-responder Scout/Diagnose execution

Branch `feat/alan/live-defense-phase7c-agent-execution`. **Open, merge
conflict** (contracts schema 0.22.0 vs #412's 0.20.0; orchestrator bumps to
7c.1.0).

Verified properties:

- **Authorization**: `run_investigative` pins the snapshot to the authorized
  `(repo_id, commit_sha, snapshot_id)` before the loop; `_assert_authorized_tree`
  and canonical-intel binding raise `SNAPSHOT_NOT_READY` on mismatch.
- **Evidence honesty**: never sets `confirmation=confirmed`; typed output is
  persisted as `ExecutionIntegrity.checked|failed` hypotheses — labelled "not
  independent verification and not a confirmed threat".
- **Provider errors**: `_BackendGateway` maps shared error codes; refuses
  silent `$0` live responses; `LLM_UNAVAILABLE`/`BUDGET_EXHAUSTED`/`CANCELLED`
  surfaced, not swallowed.
- **Cost accounting**: `provider_ledger` reserves every provider `complete()`
  before send, settles with response_id+usage+cost; unresolved attempts block
  replay (`should_refuse_replay`); budget `min(FIRST_RESPONDER_MAX_USD, 1.5)`,
  80k tokens.
- **Cancellation**: OFF/cancel checked before send, between steps via
  `check_cancelled`, and before persist (`_commit_invocation` cannot upgrade a
  cancelled or executed row); deadline refuses new provider calls under
  `MIN_PROVIDER_START_S`.
- **Prerequisite ordering**: Diagnose requires executed Scout hypotheses;
  `PrerequisiteNotReady` → `JobDeferred` without burning attempts.
- **Scope non-expansion**: Trace/Fortify/Probe/Patch reasons documented;
  Fortify requires independently executed verification_results; Patch deploy
  refused.

Caveats for reviewers: (a) 1,551-line review test file covers FakeLLM paths —
the live provider path is untested by design (no paid calls in CI); (b) merge
conflict must be resolved before CI proves it; (c) recommend merge **after**
#412's contract ordering is reconciled, or land #458 first and rebase #412.

## 8. PR #412 review — RL-gated pilot infrastructure

Branch `feat/live-defense-phase6-5-rl-gated-pilot`. **Open.** Its own readiness
report says categories A–I BLOCKED-evidence-wise and J NOT VERIFIED; verdict
`infrastructure_ready_rl_blocked` — accurate and self-consistent.

- `pilot_guard.py`: for `rl_gated_pilot` applications, grant/channel/authorize/
  execute **deny at server level** and workers re-check independently —
  shadow-only is enforced, not documented. `leftover_inventory` fails closed
  on any in-effect control.
- `readiness.py`: categories cannot PASS from PRs/tests/docs; `ready` requires
  every mandatory category; verdict honestly stays blocked.
- `serving.py`: `PolicyServingOutput` contract for hosted inference.
- Author's own flag: "Do not merge as ordinary isolated infra without human
  review of the authz tightening" — concur. The deny-behavior itself is the
  safe direction; merging it only *narrows* what gated apps can do.
- Conflict surface with #458: both bump contracts; sequence matters.

**Merge recommendation**: safe to merge after human review of the authz
tightening and contract-schema reconciliation with #458 — it adds guardrails,
not capability.

## 9. False-disruption / false-alarm / missed-event tradeoffs

- **False disruption**: structurally zero for customers — recommendations never
  execute outside `owned_test`; the only executable kinds are rate-limit,
  single-fixture-IP block, increase-logging, escalate, all time-boxed,
  budgeted, revocable, and rolled back on failure.
- **False alarms**: quiet-window abstention + `skipped_*` statuses mean low-
  evidence windows produce abstentions, not alerts; simulated/replay traffic
  is labelled rather than counted.
- **Missed events**: blind spots are the customer's unposted windows (§"cannot
  observe" in the capability matrix). Dedup/replay labelling trades a possible
  under-count for honest accounting — accepted for pilot.
- **Uptime**: shadow path is in-process + idempotent worker jobs; ingest
  failures cannot take the API down beyond a 4xx; enforcement adapter timeout
  is 2 s and failure triggers rollback.

## 10. Conditions of the GO

1. Pilot messaging uses the capability matrix verbatim — no "autonomous
   protection" language; `PILOT_UNSUPPORTED_CLAIMS` stays in reports.
2. #458 and #412 merge only after human review + conflict resolution (both
   already flagged by their author).
3. No customer application is ever registered as `owned_test` as a workaround;
   the environment value is the safety boundary.
4. Keep `RL_CHECKPOINT_SHA256` pinning mandatory outside dev/test.
5. Operator demo must label which signals are real (ingested telemetry,
   shadow decisions, owned-test controls) vs synthetic (sample-layout UI,
   fake provider runs, replay/simulated sources).

## 11. Operator-facing demo (approved staging traffic)

Suggested flow, all on `main` today:

1. Register a `payments-lite staging` app (`owned_test`), mint ingest token.
2. Post 4–6 one-minute windows with an auth-failure spike → watch monitoring
   events appear with `simulated=false`.
3. Open Live Policy Decisions → show `producer=rules|v6_shadow`,
   `enforcement=NOT_EXECUTED`, abstention statuses where applicable.
4. Create grant (rate_limit, 5–60 min, hourly cap) + loopback channel →
   authorize → owned-test adapter applies on the fixture app → `verified`
   after independent check → expiry/rollback visible in trail.
5. Emergency-stop → show subsequent authorize calls denied
   (`kill_switch_clear` fails).
6. Set First Responder intent SHADOW → post windows → show decision rows and
   the honest `provider_mode=none` invocation record (until #458 merges).
