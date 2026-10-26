# Cross-system contract coordination

The **schema source of truth is `shared/`**, initially maintained by Srihith, with tests. This file is a negotiation record, *not* a second implementation source. Always read `docs/12_DATA_MODELS_AND_CONTRACTS.md` and the actual `shared/` schemas first.

## Required V0 types

`SnapshotRef(project_id, repo_id, commit_sha, snapshot_id, index_version)`; `EvidenceRef(path, sha, span, symbol, artifact_id, provenance)`; `BehavioralExpectation(id, snapshot_id, source, confidence, status, statement)`; `ContextPack(id, snapshot_ref, chunks[], token_budget, provenance[])`; `ReviewRequest`/`ReviewResult`; `AgentRun`/`AgentStepInput`/`AgentStepResult`; `Finding`/`ValidationResult`/`PatchCandidate`/`VerificationResult`; `AuthorizedScope`/`Approval`; `MonitoringEvent`/`Assessment`; `RLRecommendation`/`RLEvaluation`; `AuditEvent`/`ErrorResponse`.

## Evolution procedure

1. Consumer writes message with field-by-field proposed schema and exact reason.
2. Producer/owner acknowledges with acceptance, counterproposal or versioned alternative.
3. The `shared/` owner updates canonical Pydantic model and generated JSON Schema/TS types in PR; impacted owners review. (Srihith originally; since 2026-10-19 Alan is interim approver — see OWNERSHIP.md.)
4. Contract tests run in each consumer. Breaking change: require schema version bump and migration/compatibility plan. No unreviewed unilateral shared model edits.
5. New contract is in force only after agreed PR merges and relevant branches sync.

## Freeze target

Publish V0 early (M0), then minimize field/enum churn. Use adapters rather than divergent parallel definitions if an upstream API is temporarily absent. Every network-facing payload must validate strict schema and reject unsupported enum values. Never persist plaintext secrets in event payloads.

## Decision record

| Date (UTC) | Version | Change | Status |
|---|---|---|---|
| 2026-10-24 | 0.22.0 | Live Defense Phase 7C live authorized agent execution (additive): `AgentExecutionRecord` on first-responder invocations; statuses `executed` / `skipped_no_prerequisite`; orchestrator `7c.1.0`. Scout/Diagnose may run the real agent loop. Probe/Patch not expanded. `predicted_probability` null. Alembic stays `0015`. Open PR #412 keeps 0.20.0; if this lands first, #412 rebases to 0.23.0+. Message: `20261024T064500Z-alan-to-all-live-defense-phase7c-contracts`. | Proposed on `feat/alan/live-defense-phase7c-agent-execution`; in force after merge + owner acks. Leave PR open for security review. |
| 2026-10-24 | 0.21.0 | Live Defense Phase 7A first-responder (additive): modes, orchestrator decisions, seven-agent capability registry, window-scoped deploy mapping, analyst handoff, versioned RL-ready decision records. No AUTO-EXECUTE. `evidence_state=verified` requires independently executed verification evidence. `20261024T050000Z-alan-to-all-live-defense-phase7a-schema-0-21`. Open PR #412 keeps 0.20.0 (no Alembic). This PR uses Alembic `0015`. If #439 merges first, #412 rebases to 0.22.0. | Proposed on `cursor/live-defense-first-responder-0044` / PR #439; in force after merge + owner acks |
| 2026-10-24 | 0.20.0 | Reserved by open PR #412 (Phase 6.5 RL-gated pilot). Not used by Phase 7A. | Proposed on #412; do not overwrite from #439 |
| 2026-10-17 | 0.1.0 (V0) | Initial `shared/` contracts, generated JSON Schema/TS, fixtures (`20261017T010331Z-srihith-to-all-contracts-v0`). RL types adopted from Adam's proposal (#2). | Proposed in Srihith PR; in force after merge + owner acks |
