# MSG: Phase 7C first-responder live agent execution (schema 0.22.0)

- ID: `20261024T064500Z-alan-to-all-live-defense-phase7c-contracts`
- From: Alan
- To: all
- Kind: contract-proposal
- Timestamp (UTC): 2026-10-24T06:45:00Z
- Branch / commit / PR or issue link: `feat/alan/live-defense-phase7c-agent-execution`
- Related subsystem(s): Live Defense first-responder; `shared/` 0.22.0; Alembic stays `0015`; seven security agents (Scout/Diagnose execute); Codex RL records (consume later, do not train)
- Summary (facts, not assumptions): Phase 7C adds additive `AgentExecutionRecord` on `FirstResponderInvocation.execution` and statuses `executed` / `skipped_no_prerequisite`. Scout and Diagnose run the real `security_agents` loop against an owner-attested mapped snapshot. Probe/Patch/Verify privileges are **not** expanded. Trace still waits for confirmed Probe. Fortify still waits for independently executed `verification_results`. `predicted_probability` stays null. Decision `executed` stays false (no LiveControl). Open PR #412 keeps **0.20.0**. This branch is **0.22.0** (0.21.0 is merged Phase 7A). No Alembic 0016 — execution is JSONB on the existing invocation row. Orchestrator `7c.1.0`. This is an execution trust-boundary expansion: leave the PR open for security review; do not merge from the implementing agent.
- Interface requested/proposed: `AgentExecutionRecord` (`provider_mode` fake|live|none, cost/latency/turns/tools, transcript/output artifact ids, `execution_integrity` not_run|checked|failed, `evidence_verified=false`, `confirmation="unverified"`, `predicted_probability=null`). `AgentInvocationStatus.executed` and `skipped_no_prerequisite`. `ORCHESTRATOR_VERSION="7c.1.0"`. `SCHEMA_VERSION="0.22.0"`.
- Breaking change? No. Additive 0.22.0. Independent landing vs #412: that PR must not write 0.20.0 over 0.22.0; rebase #412 to 0.23.0+ if this lands first.
- Action required by recipient: Codex — do not train; consume genuine execution records later. Srihith — interim shared ack. #412 owner — keep 0.20.0 until rebase. Vihaan — invocations UI shows cost/latency and “not confirmed”. Do not treat this as AUTO-EXECUTE, Probe expansion, or production enforcement.
- Response-by milestone: human/security approval of the Phase 7C PR; leave open (do not merge from this agent)
- Evidence: `backend/tests/test_live_defense_phase7c.py`; `docs/LIVE_DEFENSE_PHASE7C_AGENT_EXECUTION.md`
- In-Reply-To: `coordination/messages/20261024T050000Z-alan-to-all-live-defense-phase7a-schema-0-21.md`
