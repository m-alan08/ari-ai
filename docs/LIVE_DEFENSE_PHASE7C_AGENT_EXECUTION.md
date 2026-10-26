# Live Defense Phase 7C — live authorized agent execution

Status: **implemented** on `feat/alan/live-defense-phase7c-agent-execution` from live
`origin/main` (PR #439 / Phase 7A merged). This is **not** Codex Phase 7B RL
research, **not** RL training, **not** AUTO-EXECUTE, and **not** customer
production enforcement.

**Verdict for re-review: ENGINEERING GO FOR SECURITY RE-REVIEW, MERGE NO-GO
until independent review accepts the six fixes.** Leave the PR open. Do not
merge from this agent. This is still an execution trust-boundary expansion.

## Return fields

| Item | Status |
|---|---|
| PR | https://github.com/adamissac/ari-ai/pull/458 on `feat/alan/live-defense-phase7c-agent-execution` |
| Merge | **left open** — execution trust-boundary expansion; explicit security review required |
| Branch | `feat/alan/live-defense-phase7c-agent-execution` |
| Modes | OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected; fail closed |
| What executed for real | Scout and Diagnose run `security_agents.runtime.run_agent` against an owner-attested mapped snapshot when FakeLLM (tests/e2e) or a live Anthropic key is present |
| Skipped / gated | Probe is not expanded (AuthorizedScope eligibility stays `completed_unverified`, `provider_mode=none`). Patch worktree-only, never deployed. Verify eligibility is not verification. Trace waits for confirmed Probe. Fortify waits for independently executed `verification_results` |
| Confirmation | Always `unverified`. `executed` on the decision stays `false` (no LiveControl). `predicted_probability` stays null |
| Schema | **0.23.0** additive (main claimed 0.22.0 for ops readiness). Open PR #412 keeps **0.20.0**. Inherits main Alembic **0017**; no competing repository-disconnect migration |
| Orchestrator | `7c.1.0` |
| RL | Decision-time records include genuine cost/latency/tools/transcript. **Do not train** |

## Architecture (reuse Phase 7A)

```
INGEST (existing)
  POST /ingest/runtime/{project}  → runtime_windows + MonitoringEvent
  job live_defense_shadow         → LivePolicyDecision { enforcement: NOT_EXECUTED }

FIRST RESPONDER (Phase 7A decide + Phase 7C execute)
  POST /projects/{id}/first-responder/mode   owner, audited
  job live_defense_first_responder           deterministic orchestrator 7c.1.0
      → FirstResponderDecision (versioned; predicted_probability=null; executed=false)
      → FirstResponderInvocation
      → AnalystHandoff (delivered only after provider ack)
  job live_defense_agent_step
      Scout   — real agent loop when mapping+snapshot exist
      Diagnose — real agent loop after Scout hypotheses
      Fortify — skipped_no_prerequisite without verification_results
      Trace   — skipped_no_prerequisite until confirmed Probe
      Probe   — not expanded; scope eligibility ≠ execution
      Patch   — worktree-only; silent deploy refused
      Verify  — eligibility is not verification

ENFORCEMENT (existing Phase 4; not started by first-responder)
  authorize.check → LiveControl → live_defense_execute
  OFF does not cancel these jobs

LLM calls run **outside** the first-responder advisory lock. Artifacts persist
in short transactions. OFF / cancel is re-checked before the loop and before
writing the invocation. A running loop sees `cancelled()` / `kill_switch`.

Security-review fixes (six findings, existing branch, not merged):

1. **Canonical intelligence.** Indexed repos use `snapshot_ref` + `CodeIntelligenceAdapter` bound to the authorized project/repo/commit. `LocalIntel` only when `SECURITY_AGENTS_LABELS` is an explicit fixture. Missing authoritative context fails closed (`SNAPSHOT_NOT_READY`).
2. **Partial evidence.** `backend.llm.LLMError` is normalized to `security_agents` `LLMUnavailable` with a catalog code. Successful prior turns, usage, cost, model and `provider_mode` persist. Live calls never record `provider_mode=none`.
3. **Deadlines.** Remaining wall-clock is passed as `timeout_s`. Provider retries stop when a retry would miss the deadline. Cancel, deadline (`BUDGET_EXHAUSTED` / `DEADLINE_EXCEEDED`) and provider failure stay distinct. OFF remains authoritative.
4. **Cost.** Adapter USD uses `backend.llm.cost_usd` including input, output, cache-read and **cache-creation**. Missing live usage fails closed rather than silent `$0`.
5. **Diagnose scheduling.** Diagnose is not enqueued until Scout is terminal. Waiting uses `jobs.defer` (does not burn failure attempts). Failed/cancelled Scout skips Diagnose.
6. **Idempotent redelivery.** Terminal invocations return the persisted result. Successful `executed` rows cannot be overwritten by a later failed retry. Crash-in-progress is `queued` + `execution.started_at` without a transcript.

## What executed vs skipped

| Agent | Phase 7C path | Confirmation |
|---|---|---|
| Scout | `run_agent` + `scout.to_hypotheses` when mapping and snapshot exist | unverified; integrity `checked` means cited snapshot spans, not a confirmed threat |
| Diagnose | `run_agent` after Scout hypotheses; input is labelled a hypothesis | unverified |
| Fortify | `skipped_no_prerequisite` | n/a |
| Trace | `skipped_no_prerequisite` (orchestrator still blocks until confirmed Probe) | n/a |
| Probe | `completed_unverified` if AuthorizedScope else `skipped_no_scope`; no probe loop | unverified |
| Patch | skipped / blocked; `worktree_only=true`; `silent_deploy=false` | n/a |
| Verify | `completed_unverified` without independently executed `verification_results` | unverified |

Missing LLM key → invocation `failed` with `error_code=LLM_UNAVAILABLE`, not a
faked success. Missing snapshot → honest `skipped_no_snapshot`.

## Control boundaries (unchanged)

- No AUTO-EXECUTE route.
- OFF cancels first-responder jobs only. Advisory lock serializes mode writes with dispatch.
- Owner-only mode writes. Outsiders 404; members 403.
- Org intent ≠ app effective permissions.
- Probe is not silently expanded. Patch is not silently expanded.
- LLM prose cannot confirm a threat.
- `evidence_state=verified` still requires independently executed `verification_results`.

## Schema vs open #412

| Branch | SCHEMA_VERSION | Alembic |
| main after #439 | 0.21.0 | 0015 |
|---|---|---|
| main (ops readiness) | 0.22.0 | 0017 |
| this PR (Phase 7C) | **0.23.0** additive | **0017** (no competing disconnect migration) |
| integration after repository disconnect | 0.26.0 | 0019 |
| open #412 | 0.20.0 reserved | none |

#412 must rebase past **0.23.0** if it lands after this PR, not overwrite 0.23.0.
This PR does not claim 0.20.0.

## RL-data readiness

`FirstResponderInvocation.execution` (`AgentExecutionRecord`) records
`provider_mode`, `model_id`, `cost_usd`, `latency_ms`, `llm_turns`,
`tool_call_count`, transcript and output artifact ids, `execution_integrity`,
and `error_code`. `predicted_probability` is always null.
`evidence_verified` is always false. **Do not train.**

## Tests actually run (security-review follow-up)

FakeLLM/FakeTransport only. Not live Anthropic proof.

| Suite | Result |
| `make check` | pass (schema 0.22.0, 155 files) |
| `uv run mypy` | 40 files, no issues |
| `uv run pytest -q` | **806 passed**, 15 skipped |
| `security-agents` pytest | **592 passed**, 23 skipped |
| frontend lint + typecheck | pass |
| frontend vitest | **330 passed** / 78 files |
| transport | **35 passed** |
| Playwright 7A+7C | **2 passed** (15.4s) |

## Next

Independent security **re-review** of PR #458. Do not merge from the implementing
agent. Do not enable autonomous containment, production controls, or unrestricted
probing. Live Anthropic Scout/Diagnose remains opt-in (issue #10). Re-review
**GO**, merge **NO-GO** until that review accepts.











6. **Idempotent redelivery.** Terminal invocations return the persisted result. Successful `executed` rows cannot be overwritten by a later failed retry. Crash-in-progress without a billable send is `queued` + `execution.started_at` without a transcript.

Codex combined-release follow-up (existing branch, not merged; six findings above preserved):

- **F1 unknown billable outcome.** Each provider `complete()` reserves a durable `ProviderAttempt` (`in_flight`) before send. Crash after send marks `uncertain`. Recovery never starts a second billable request while the previous result is unknown. In-flight concurrent claims `JobDeferred`. Anthropic `Idempotency-Key` is sent when the live transport is used. Read-only intel queries are not ledgered. Partial usage stays on the invocation JSONB.
- **F7 overall deadline.** `FirstResponderDecision.deadline_at` is the single overall deadline (now + `FIRST_RESPONDER_DEADLINE_S`). Copied onto each invocation and agent-step payload. Worker retry does not reset it. Expired deadline refuses another provider call (`BUDGET_EXHAUSTED` / `DEADLINE_EXCEEDED`), distinct from cancel and from provider failure. Stored in JSONB + payload; Alembic 0016 (`jobs.deadline_at`) remains Devin Reliability's.
- **F8 cancel precedence.** OFF and `cancel_requested` are rechecked after the agent loop and immediately before committing finals. That commit is not rolled back by the subsequent `JobCancelled`. Cancelled invocations keep partial transcripts/costs and cannot become `executed`.
- **F4/F5.** This branch does not rewrite `jobs.py` / worker lifecycle. Diagnose still uses `JobDeferred` / `jobs.defer`. Devin Reliability owns generic `waiting` / `depends_on` / `ctx.effect()`.













| `make check` | pass (schema 0.22.0, 156 files) |
| focused 7A+7C+review+jobs+llm | **76 passed**, 1 skipped (opt-in live smoke) |

