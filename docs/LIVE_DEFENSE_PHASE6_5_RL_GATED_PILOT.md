# Live Defense Phase 6.5 — RL-gated pilot engineering

Status: **observed on `feat/live-defense-phase6-5-rl-gated-pilot` from live `origin/main`**. PR [#412](https://github.com/adamissac/ari-ai/pull/412) is **open** for human review (authz tightening). Do not treat this as merge authorization.
This is surrounding product infrastructure. It is not customer-production
evidence, not RL validation, and not authorization to start Phase 7 or contact
a customer.

**Verdict: INFRASTRUCTURE READY — RL BLOCKED.**

Phase 6 CONDITIONAL GO (rules-based recommendation-only shadow, PR #411) is a
different gate. Completing this infrastructure does **not** convert that into
a company pilot.

## Live GitHub (do not trust stale handoffs)

Checked with `gh pr view` and `git fetch origin main` during this session.

| Fact | Observed |
|---|---|
| `origin/main` SHA (branch base) | `74a0a3c202fec943e78c8ca03b0c0ba445639c7a` (merge of PR #410) |
| PR [#411](https://github.com/adamissac/ari-ai/pull/411) | **MERGED** 2026-10-23T15:48:57Z as `5d1e7b91a84aa9d867d15a45d067a563f5749ace` |
| PR [#388](https://github.com/adamissac/ari-ai/pull/388) | **MERGED** 2026-10-23T04:52:13Z as `09e8cabf` |
| PR [#394](https://github.com/adamissac/ari-ai/pull/394) | **MERGED** 2026-10-23T05:49:54Z as `cb40ca6b` |
| PR [#396](https://github.com/adamissac/ari-ai/pull/396) | **MERGED** 2026-10-23T06:16:03Z as `7adf509c` |
| PR [#407](https://github.com/adamissac/ari-ai/pull/407) | **MERGED** 2026-10-23T14:21:34Z as `235ef3f2` |
| PR [#410](https://github.com/adamissac/ari-ai/pull/410) | **MERGED** 2026-10-23T16:04:50Z as `74a0a3c2` — fixture audit with **explicit inconclusive readiness**; `rl_training_ready` and `promotion_ready` stay false |
| `SCHEMA_VERSION` | `0.20.0` (additive) |
| Alembic heads | unchanged (`0014`); gated flag lives in application `doc` JSON |
| Codex / v6 / datasets / holdout / historical evals | **not trained, promoted, or edited** |

Merged Codex PRs and unit tests are recorded as notes. They are not model
readiness.

## RL evidence matrix (categories A–J)

Backend source: `GET /api/v1/projects/{id}/rl-readiness`. Frontend flags cannot
set `ready=true`. `ready` is true only if every mandatory category is `PASS`.

| Cat | Title | Status | Why |
|---|---|---|---|
| A | Frozen evaluation scenarios | BLOCKED | No RL-owner-approved frozen set for this gate |
| B | Holdout unused in training | BLOCKED | Codex/v6 historical holdout is not this gate |
| C | Independent seeds | BLOCKED | No approved independent-seed bundle |
| D | Rules / fixed-block / v6 baselines | BLOCKED | Plan exists; comparison not approved |
| E | Compromise, availability, delegated, false-disruption, cost | BLOCKED | Metrics listed; **thresholds unset** |
| F | Per-scenario (no pass-by-total-reward) | BLOCKED | Evidence missing |
| G | Abstention | BLOCKED | No approved abstention eval on this gate |
| H | Uncertainty (Q ≠ probability) | BLOCKED | Contract states the rule; model evidence missing |
| I | Provenance of an approved checkpoint | BLOCKED | v6 hash is a compatibility fixture only |
| J | Hosted serving assumptions | NOT VERIFIED | Config declared; no checkpoint verified against new criteria |

`ready`: **false**. `verdict`: **INFRASTRUCTURE READY — RL BLOCKED**.

## Missing acceptance criteria (RL owner)

Numeric thresholds for compromise / availability / delegated / false-disruption /
cost; frozen scenarios; unused holdout; independent seeds; baseline comparison
under this plan; per-scenario results; abstention eval; uncertainty evidence;
approved checkpoint provenance; hosted timeout/restart values. Only the owner
can later authorize a company pilot.

## Shadow verification (server-enforced)

| Surface | Expected | Actual | Evidence |
|---|---|---|---|
| Register `rl_gated_pilot=true` | No grant/channel/authorize | HTTP 403 `LIVE_CONTROL_DENIED`; zero `LiveControl` rows | `test_gated_app_refuses_grant_channel_and_authorize` |
| Enable with leftover grant | Fail closed | HTTP 409 `CONFLICT_STATE` | `test_enable_fails_closed_until_leftovers_are_removed` |
| Enable after revoke | Shadow-only | `rl_gated_pilot=true`; later grant 403 | same test |
| Leftover execute | Worker fail-closed | Authorized leftover → `rejected`; job `denied=true`; no adapter | `test_worker_refuses_leftover_execute_on_gated_app` |
| Analyst accept | Non-executing | `executes=false`; decisions `NOT_EXECUTED` | `test_gated_ingest_analyst_accept_and_health_stay_recorded_only` |
| Authz list | Still 15 checks | `rl_gated_pilot` fails `application_registered` (tightening, not allowlist expansion) | `test_rl_gated_pilot_is_denied_without_new_check` |
| Health | Not fabricated | `status=unknown` | phase6_5 health assertion |
| Tenant | Outsider 404 | readiness and events 404 | phase6_5 API test |

UI distinguishes RL-gated apps (`RL-gated shadow-only`) from the owned-test
Controls tab. Executable surface is not expanded.

Authz tightening is left in this PR for human review (trust-boundary change).
Do not merge as “ordinary isolated infra” without that review if the reviewer
treats the gate change as serving/authz behavior.

## Hosted RL compatibility

- Serving contract: `PolicyServingOutput`. Missing fields stay missing.
- Q-values are not probabilities (`q_values_are_not_probabilities: true`).
- Frozen v6 SHA `8113a37f…` is a compatibility fixture only.
- `v6_passed_new_criteria` is **false**. Newer research checkpoints are not
  auto-served. `approved_checkpoint_sha256` is null.

## Tests (executed)

```
uv run pytest -q backend/tests/test_live_defense_phase6_5.py backend/tests/test_live_defense_authorize.py
25 passed
npm run lint --prefix frontend        # pass
npm run typecheck --prefix frontend   # pass
npm test --prefix frontend            # 331 passed / 78 files
npm test --prefix frontend/transport  # 35 passed
```

Playwright: spec added (`RL-gated readiness dashboard stays BLOCKED from the backend`).
The full `frontend/tests/e2e/stack.py` run was **not executed** here (honest skip).

## Codex research status

Untouched in this work. PRs #388, #394, #396, #407, #410 are merged research
or fixture audits. #410’s own receipt is **inconclusive** and does not approve
training or promotion.

## Customer infra status

**Not provisioned.** No real customer credentials, hosts, or activation.

## Remaining tasks

1. RL owner supplies A–J evidence and thresholds (not invented here).
2. Human review of the authz tightening before merge if required.
3. Owner-only decision to authorize a company pilot after `ready=true`.
4. Do not start Phase 7 / autonomous defense / customer contact.

Plan: [LIVE_DEFENSE_PHASE6_5_PLAN.md](planning/LIVE_DEFENSE_PHASE6_5_PLAN.md).
Eval plan: [LIVE_DEFENSE_RL_EVALUATION_PLAN.md](LIVE_DEFENSE_RL_EVALUATION_PLAN.md).
Serving: [LIVE_DEFENSE_POLICY_SERVING_CONTRACT.md](LIVE_DEFENSE_POLICY_SERVING_CONTRACT.md).
Staging package: [LIVE_DEFENSE_FIRST_COMPANY_STAGING.md](LIVE_DEFENSE_FIRST_COMPANY_STAGING.md).
