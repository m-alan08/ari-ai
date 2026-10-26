"""RL-gated pilot readiness. Backend is the source of truth.

Open PRs, unit tests, and research docs never flip a category to PASS.
Numeric thresholds are not invented. ready is true only if every mandatory
category is PASS.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.live_defense.serving import hosted_report
from shared import SCHEMA_VERSION
from shared.base import utcnow
from shared.live_defense import (
    EvidenceCheckStatus,
    PilotVerdict,
    PolicyServingOutput,
    RlCategoryResult,
    RlEvaluationMetricSpec,
    RlEvaluationPlan,
    RlEvidenceCategory,
    RlReadinessAssessment,
)

# Live GitHub observed when this slice started. Merged research is not readiness.
RECORDED_ORIGIN_MAIN_SHA = "74a0a3c202fec943e78c8ca03b0c0ba445639c7a"
PHASE6_PR_NOTE = (
    "PR #411 merged as 5d1e7b91: Phase 6 CONDITIONAL GO is rules-based shadow, "
    "not RL-gated-pilot readiness."
)
CODEX_PR_NOTES = (
    "Codex PR #388 merged: constrained-RL Phase 1 foundation. Research, not model readiness.",
    "Codex PR #394 merged: deterministic payment-read feasibility. Research, not model readiness.",
    "Codex PR #396 merged: owned payments context binding. Research, not model readiness.",
    "Codex PR #407 merged: owned-read response integrity. Research, not model readiness.",
    "Codex PR #410 merged as 74a0a3c2: fixture audit with explicit inconclusive readiness; "
    "rl_training_ready and promotion_ready stay false.",
)

_CATEGORY_META: tuple[tuple[RlEvidenceCategory, str, str], ...] = (
    (RlEvidenceCategory.A_frozen_scenarios, "A", "Frozen evaluation scenarios"),
    (RlEvidenceCategory.B_holdout, "B", "Holdout unused in training"),
    (RlEvidenceCategory.C_independent_seeds, "C", "Independent evaluation seeds"),
    (RlEvidenceCategory.D_baselines, "D", "Rules / fixed-block / v6 baselines"),
    (
        RlEvidenceCategory.E_safety_metrics,
        "E",
        "Compromise, availability, delegated, disruption, cost",
    ),
    (RlEvidenceCategory.F_per_scenario, "F", "Per-scenario results (not total reward)"),
    (RlEvidenceCategory.G_abstention, "G", "Abstention behavior"),
    (RlEvidenceCategory.H_uncertainty, "H", "Uncertainty (Q is not probability)"),
    (RlEvidenceCategory.I_provenance, "I", "Provenance of an approved checkpoint"),
    (RlEvidenceCategory.J_hosted_serving, "J", "Hosted serving assumptions"),
)

_SUMMARIES: dict[RlEvidenceCategory, str] = {
    RlEvidenceCategory.A_frozen_scenarios: (
        "No RL-owner-approved frozen scenario set for this gate."
    ),
    RlEvidenceCategory.B_holdout: (
        "Codex/v6 historical holdout is not this gate's unused holdout."
    ),
    RlEvidenceCategory.C_independent_seeds: (
        "No independent-seed evaluation bundle approved for the gated pilot."
    ),
    RlEvidenceCategory.D_baselines: (
        "Required baselines are specified; no comparison under this plan has been approved."
    ),
    RlEvidenceCategory.E_safety_metrics: (
        "Required metrics are listed; numeric thresholds remain unset pending RL-owner approval."
    ),
    RlEvidenceCategory.F_per_scenario: (
        "Pass-by-total-reward is forbidden; per-scenario evidence is missing."
    ),
    RlEvidenceCategory.G_abstention: (
        "Abstention is required; no approved evaluation of abstention on this gate."
    ),
    RlEvidenceCategory.H_uncertainty: (
        "Serving contract states Q is not probability; model uncertainty evidence is missing."
    ),
    RlEvidenceCategory.I_provenance: (
        "Frozen v6 hash exists as a compatibility fixture, not an approved gated-pilot policy."
    ),
    RlEvidenceCategory.J_hosted_serving: (
        "Hosted config is declared; no checkpoint has been verified against the new criteria."
    ),
}

_J_STATUS = EvidenceCheckStatus.not_verified


def evaluation_plan() -> RlEvaluationPlan:
    return RlEvaluationPlan(
        metrics=[
            RlEvaluationMetricSpec(
                name="compromise",
                description="Unauthorized access remaining after the candidate acts.",
            ),
            RlEvaluationMetricSpec(
                name="availability",
                description="Legitimate own-object requests that still succeed.",
            ),
            RlEvaluationMetricSpec(
                name="delegated",
                description="Explicit valid delegates that still succeed on covered resources.",
            ),
            RlEvaluationMetricSpec(
                name="false_disruption",
                description="Legitimate traffic disrupted by the candidate.",
            ),
            RlEvaluationMetricSpec(
                name="cost",
                description="Measured intervention / collateral cost. Units unset.",
            ),
        ]
    )


def _notes_for(category: RlEvidenceCategory) -> list[str]:
    shared = [
        PHASE6_PR_NOTE,
        *CODEX_PR_NOTES,
        "Unit tests and research docs are not model readiness.",
    ]
    if category is RlEvidenceCategory.J_hosted_serving:
        return [
            *shared,
            "Hash-pin, tamper refuse, abstention, and no-auto-serve are configured.",
            "Timeout and restart remain unset pending RL-owner approval.",
            "v6 is not claimed to have passed the new hosted criteria.",
        ]
    if category is RlEvidenceCategory.I_provenance:
        return [
            *shared,
            "Do not treat v6 candidate_online.pt as the approved gated-pilot checkpoint.",
        ]
    return shared


def assess_categories(
    *,
    pretend_prs_merged: Sequence[int] = (),
    pretend_unit_tests_exist: bool = False,
    pretend_research_docs_exist: bool = False,
) -> list[RlCategoryResult]:
    """PRs / tests / docs, even when present, cannot produce PASS."""
    _ = (pretend_prs_merged, pretend_unit_tests_exist, pretend_research_docs_exist)
    results: list[RlCategoryResult] = []
    for category, letter, title in _CATEGORY_META:
        status = (
            _J_STATUS
            if category is RlEvidenceCategory.J_hosted_serving
            else EvidenceCheckStatus.blocked
        )
        results.append(
            RlCategoryResult(
                category=category,
                letter=letter,  # type: ignore[arg-type]
                title=title,
                status=status,
                summary=_SUMMARIES[category],
                evidence_notes=_notes_for(category),
            )
        )
    return results


def compute_ready(categories: Sequence[RlCategoryResult]) -> bool:
    mandatory = [item for item in categories if item.mandatory]
    return bool(mandatory) and all(item.status is EvidenceCheckStatus.passed for item in mandatory)


def compute_verdict(
    *,
    ready: bool,
    infrastructure_complete: bool,
    owner_approved: bool = False,
) -> PilotVerdict:
    if owner_approved and ready:
        return PilotVerdict.pilot_approved
    if ready:
        return PilotVerdict.rl_validated_pilot_ready_for_owner_approval
    if infrastructure_complete:
        return PilotVerdict.infrastructure_ready_rl_blocked
    return PilotVerdict.infrastructure_ready_rl_blocked


def assess(
    *,
    origin_main_sha: str | None = RECORDED_ORIGIN_MAIN_SHA,
    gated_application_count: int = 0,
    infrastructure_complete: bool = True,
    github: Mapping[str, str] | None = None,
) -> RlReadinessAssessment:
    """Build the assessment. github extras are recorded notes, never PASS evidence."""
    categories = assess_categories()
    if github:
        extra = [f"{key}: {value}"[:500] for key, value in github.items()]
        categories = [
            item.model_copy(update={"evidence_notes": [*item.evidence_notes, *extra]})
            for item in categories
        ]
    ready = compute_ready(categories)
    return RlReadinessAssessment(
        generated_at=utcnow(),
        schema_version=SCHEMA_VERSION,
        origin_main_sha=origin_main_sha,
        categories=categories,
        evaluation_plan=evaluation_plan(),
        serving_contract=PolicyServingOutput(),
        hosted_inference=hosted_report(),
        ready=ready,
        verdict=compute_verdict(ready=ready, infrastructure_complete=infrastructure_complete),
        infrastructure_complete=infrastructure_complete,
        gated_application_count=gated_application_count,
    )
