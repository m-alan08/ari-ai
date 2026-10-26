"""Canonical cross-subsystem contracts (single source of truth, ADR-0004).

Import models from their modules (``from shared.findings import Finding``) or from
``shared.registry`` for the full list. Owner: Srihith. Change procedure:
coordination/CONTRACTS.md.
"""

SCHEMA_VERSION = (
    "0.26.0"  # 0.2 RLExplanation; 0.3 runtime signals; 0.4 ids allow _-; 0.5 early REPORTING;
    # 0.6 intelligence memory (PR #39); 0.7 security standards + incidents (PR #49);
    # 0.8 FindingDetail / AssessmentReport response DTOs (additive);
    # 0.9 CheckCategory enum (Probe ids + V0 fixture aliases);
    # 0.10 SeverityAssessment.cvss_v31 (CVSS31Assessment, additive, ADR-0012);
    # 0.11 Assessment.repo_id (additive; findings link to the assessed repo);
    # 0.12 RuntimeSource "drill" (additive; attack drills get their own scoring history);
    # 0.13 live RL lab (additive): DefenseActionName "block_principal" (#324),
    #      "block_principal_long", "block_top_principal_long"; PlatformFacts principal-block
    #      and v3 detector fields with defaults (#334, #335; old payloads validate unchanged);
    # 0.15 repository automatic assessment mode and owner update (additive).
    # 0.14 runtime principal detection (additive): RouteWindow.principals (PrincipalCounts,
    #      pseudonymous PrincipalRef) and DefenseRecommendation.principal_targets;
    # 0.16 live-defense shadow pilot (additive): connected staging apps, ingest-token
    #      metadata, recorded defense health/graph, analyst recommendation reviews.
    #      Reviews never execute. Health status is always unknown without a recorded
    #      health-check producer (HTTP 200 is not a healthy verdict).
    # 0.17 live-defense Phase 3 shadow runtime (additive): RuntimeSource "owned_staging";
    #      LivePolicyDecision (NOT EXECUTED); AnalystReview may target a live decision.
    # 0.18 live-defense Phase 4 controlled enforcement (additive): LiveControl lifecycle,
    #      ControlGrant, ControlChannel, authorization DTO, verification records.
    #      AnalystReview and LivePolicyDecision stay non-executing. Production is still
    #      not a StagingEnvironment value.
    # 0.18 live-defense Phase 5 shadow-pilot readiness (additive): disconnect,
    #      ingest-token rotate, worker/telemetry/policy health remain unknown,
    #      exportable PilotReport. No executable enforcement. Phase 4 (#393) and
    #      Phase 5 (#395) both merged; later delivery-mode work is 0.19.0.
    # 0.18.1 automation opt-in: Repository.automatic_assessments and TriggerPolicy.enabled
    #      default to false (owner must explicitly enable; PR #385).
    # 0.19 ProjectSettings.delivery_mode (auto | pull_request | manual, pilot default pull_request) and
    #      server-set delivery_authorized_by (additive, ADR-0021).
    # 0.20 live-defense Phase 6.5 RL-gated pilot infrastructure (additive):
    #      ConnectedApplication.rl_gated_pilot; RL readiness categories A–J;
    #      evaluation plan with unset thresholds; policy serving contract;
    #      hosted-inference readiness config. ready=true only if every mandatory
    #      category is PASS. Merged research PRs / unit tests / docs are not
    #      model readiness. No new executable action.
    # 0.21 live-defense Phase 7A first-responder (additive): OFF/MONITOR|SHADOW/
    #      ASSISTED RESPONSE modes, deterministic orchestrator decisions,
    #      seven-agent capability registry, runtime↔code correlation,
    #      analyst handoff, versioned decision records for future RL.
    #      No AUTO-EXECUTE. LLM prose is never proof of a confirmed threat.
    #      evidence_state=verified requires independently executed verification
    #      evidence, not Verify eligibility.
    # 0.22 ops readiness (additive, upstream): ReadinessStatus gains
    #      workers/capabilities/capabilities_ready/git_sha/schema_version;
    #      HealthStatus gains git_sha/schema_version; new DiagnosticsReport.
    #      /readyz consumers only.
    # 0.23 live-defense Phase 7C live agent execution (additive): genuine
    #      first-responder agent-loop records (cost, latency, transcript,
    #      typed output, execution integrity). predicted_probability stays
    #      null. executed investigative output is not threat confirmation.
    #      Probe/Patch/Verify privileges are not expanded. Execution lives in
    #      invocation JSONB plus the durable billable ProviderAttempt ledger,
    #      invocation deadline_at and idempotent provider redelivery (unknown
    #      outcomes never replay).
    # 0.24 first-responder observability (additive, read-only): investigation
    #      timeline/summary DTOs, invocation view with queue state, execution
    #      view mirroring the 7C record (all fields optional), diagnostics.
    # 0.25 customer onboarding (additive): computed OnboardingStatus steps and
    #      the owner-triggered OnboardingTestEvent (real owned_staging ingest).
    #      MonitoringEvent.test_event (additive). Refreshed 7C ProviderAttempt
    #      ledger + invocation.deadline_at are optional fields; old payloads
    #      still validate.
    # 0.26 Repository.disconnected/disconnected_at (additive): canonical
    #      disconnect state for the repository kill-switch; installation_id is
    #      kept as the access record. Alembic: 0016 tenant_rls + 0017
    #      worker_heartbeats (upstream), then 0018 execution_reliability +
    #      0019 repository_disconnect (release workstreams).
)
