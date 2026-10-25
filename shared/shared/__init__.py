"""Canonical cross-subsystem contracts (single source of truth, ADR-0004).

Import models from their modules (``from shared.findings import Finding``) or from
``shared.registry`` for the full list. Owner: Srihith. Change procedure:
coordination/CONTRACTS.md.
"""

SCHEMA_VERSION = (
    "0.24.0"  # 0.2 RLExplanation; 0.3 runtime signals; 0.4 ids allow _-; 0.5 early REPORTING;
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
    # 0.19 ProjectSettings.delivery_mode (auto | pull_request | manual, default auto) and
    #      server-set delivery_authorized_by (additive, ADR-0021).
    # 0.20 reserved by open PR #412 (Phase 6.5 RL-gated pilot). This branch does
    #      not claim 0.20.0 so both PRs can land independently.
    # 0.21 live-defense Phase 7A first-responder (additive): OFF/MONITOR|SHADOW/
    #      ASSISTED RESPONSE modes, deterministic orchestrator decisions,
    #      seven-agent capability registry, runtime↔code correlation,
    #      analyst handoff, versioned decision records for future RL.
    #      No AUTO-EXECUTE. LLM prose is never proof of a confirmed threat.
    #      evidence_state=verified requires independently executed verification
    #      evidence, not Verify eligibility.
    # 0.22 reserved by open PR #458 (live-defense Phase 7C). 0.23 reserved by the
    #      fork investigation-observability workstream. This branch claims 0.24.
    # 0.24 customer onboarding (additive): computed OnboardingStatus steps and
    #      the owner-triggered OnboardingTestEvent (real owned_staging ingest).
)
