# Devin — implementation status

Owner: Devin session (workstream: Agent Execution Observability and Investigation UI)
Branch: `feat/devin-investigation-observability` (pushed to `m-alan08/ari-ai` fork; upstream push denied)
Current task: Read-only first-responder investigation timeline, agent execution detail, evidence navigation, operational diagnostics
Last remote sync: 2026-10-24, `origin/main` @ `64bb24a2` (merged into branch)
API/contract dependencies: `shared` 0.23.0 observability DTOs (additive; 0.20.0 reserved for #412, 0.22.0 reserved for #458); Phase 7A persisted decisions/invocations/jobs/handoffs on main; optional Phase 7C `invocation.doc["execution"]` mirror — works without PR #458; forward-compatible with the execution-reliability job statuses (`waiting`/`timed_out`/`recovery_required` — read whenever persisted, widened `ck_jobs_status` arrives with migration 0016)
Recent PR/commit: N4 read-side redaction (Codex RC2) pushed to fork PR https://github.com/m-alan08/ari-ai/pull/3 — `/jobs/{id}` + `/audit` + observability error codes redact stored secrets at read; coordination for Reliability's `/jobs/{id}/history` wrap in `coordination/messages/20261025T063000Z-devin-to-reliability-n4-read-side-redaction-split.md`
Blockers: none known; PR review pending (do not merge). F9 depends on Grok updating `test_live_defense_phase7c.py:130`'s `0.22.0` pin to a capability/`>=` check — coordinated in `coordination/messages/20261024T230000Z-devin-to-all-f6-state-precedence-f9-schema-compat.md`
Next action: keep PR open; if #458 lands, execution fields populate through `AgentExecutionView` with no UI changes

## What exists now

| Piece | What exists | Evidence |
|---|---|---|
| Contracts | `AgentExecutionView`, `InvocationJobView`, `FirstResponderInvocationView` (+`effective_state`), `InvestigationSnapshotView`, `InvestigationTimeline`, `InvestigationSummary` (+`job_statuses`), `InvestigationJobError`, `JobLatencyStats`, `WaitingJobView` (+`status`), `FirstResponderDiagnostics` (+`jobs_waiting`/`jobs_timed_out`/`jobs_recovery_required`) in `shared/live_defense.py` | schema 0.23.0, generated JSON Schema + TS |
| Read APIs | 4 GET endpoints in `backend/api/live_defense_investigations.py`, project-scoped, keyset-bounded, redacted; canonical job→invocation state precedence (`_effective_state`/`_final_state`) | `test_live_defense_investigations.py` (15 tests) |
| Frontend | `Executions` tab: investigations list, timeline, invocation detail, diagnostics panel; renders `effective_state` + job-state notes | `live-defense-executions.test.tsx` (9 tests) |
| Transport | `firstResponderInvestigations/Investigation/InvocationDetail/Diagnostics` | transport build + 35 node tests |
| E2E | `live-defense-investigations.spec.ts` against the real stack | Playwright spec |
