"""Versioned deterministic first-responder orchestrator (Phase 7A).

LLM prose is never proof of a confirmed threat. None of these actions execute a
``LiveControl``. AUTO-EXECUTE is rejected, not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from backend.live_defense.capabilities import load_registry
from shared.assessment import AgentName
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    FIRST_RESPONDER_DEADLINE_S,
    FIRST_RESPONDER_MAX_USD,
    MAX_FIRST_RESPONDER_LOOPS,
    ORCHESTRATOR_VERSION,
    AgentCapabilityClass,
    BlockedAgent,
    ConfirmationState,
    CorrelationConfidence,
    EvidenceLifecycleState,
    FirstResponderDecision,
    FirstResponderIntent,
    OrchestratorAction,
    RuntimeCodeCorrelation,
)
from shared.rl import ActionScore, DefenseActionName

INVESTIGATIVE = frozenset({AgentName.scout, AgentName.trace, AgentName.diagnose, AgentName.fortify})
DISRUPTIVE = frozenset({AgentName.probe, AgentName.verify})
CODE_CHANGING = frozenset({AgentName.patch})


@dataclass(frozen=True)
class OrchestratorFacts:
    project_id: str
    application_id: str | None
    intent: FirstResponderIntent
    effective: FirstResponderIntent
    application_connected: bool
    telemetry_authenticated: bool
    secret_like: bool
    trusted_mapping: bool
    mapped_commit: str | None
    mapping_id: str | None
    mapping_fresh: bool
    authorized_scope_active: bool
    remediation_approved: bool
    patch_candidate_id: str | None
    kill_switch_active: bool
    budget_usd_remaining: float
    deadline_passed: bool
    already_decided_id: str | None
    retry_count: int
    loop_count: int
    simulated: bool
    trigger_event_ids: tuple[str, ...] = ()
    live_policy_decision_id: str | None = None
    incident_id: str | None = None
    finding_ids_at_commit: tuple[str, ...] = ()
    shadow_action: DefenseActionName | None = None
    q_values: tuple[ActionScore, ...] | None = None
    auto_execute_requested: bool = False
    independent_verification_id: str | None = None


def _correlation(facts: OrchestratorFacts) -> RuntimeCodeCorrelation:
    if not facts.trusted_mapping or not facts.mapped_commit:
        confidence = (
            CorrelationConfidence.none
            if facts.mapping_id and not facts.trusted_mapping
            else CorrelationConfidence.unknown
        )
        note = (
            "No owner-attested deploy-to-commit mapping. Correlation is UNKNOWN. "
            "A vulnerability at some commit is not proof this request exploited it."
        )
        return RuntimeCodeCorrelation(
            application_id=facts.application_id,
            mapping_id=facts.mapping_id,
            commit_sha=None,
            confidence=confidence,
            finding_ids_at_commit=list(facts.finding_ids_at_commit),
            note=note,
        )
    confidence = CorrelationConfidence.medium if facts.mapping_fresh else CorrelationConfidence.low
    return RuntimeCodeCorrelation(
        application_id=facts.application_id,
        mapping_id=facts.mapping_id,
        commit_sha=facts.mapped_commit,
        confidence=confidence,
        finding_ids_at_commit=list(facts.finding_ids_at_commit),
        note=(
            "Owner-attested mapping only. Finding ids at this commit are not proof "
            "that this runtime request exploited them."
        ),
    )


def _blocked(facts: OrchestratorFacts) -> list[BlockedAgent]:
    registry = load_registry()
    blocked: list[BlockedAgent] = []
    for cap in registry.agents:
        if cap.name in INVESTIGATIVE:
            if facts.effective is FirstResponderIntent.off:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason="first-responder mode is OFF",
                        capability_class=cap.capability_class,
                    )
                )
            elif cap.name in {AgentName.scout, AgentName.diagnose, AgentName.fortify} and not (
                facts.trusted_mapping and facts.mapped_commit
            ):
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason="no trusted deploy-to-commit mapping",
                        capability_class=cap.capability_class,
                    )
                )
            elif cap.name is AgentName.trace:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason="Trace follows a confirmed Probe result; none exists",
                        capability_class=cap.capability_class,
                    )
                )
        elif cap.name is AgentName.probe:
            if facts.effective is not FirstResponderIntent.assisted_response:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason="Probe requires ASSISTED RESPONSE",
                        capability_class=cap.capability_class,
                    )
                )
            elif not facts.authorized_scope_active:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason="Probe requires an active AuthorizedScope; no external probes",
                        capability_class=cap.capability_class,
                    )
                )
        elif cap.name is AgentName.patch:
            reason = None
            if facts.effective is not FirstResponderIntent.assisted_response:
                reason = "Patch requires ASSISTED RESPONSE"
            elif not facts.remediation_approved:
                reason = "Patch requires a human remediation approval"
            if reason:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason=reason,
                        capability_class=AgentCapabilityClass.code_changing,
                    )
                )
        elif cap.name is AgentName.verify:
            reason = None
            if facts.effective is not FirstResponderIntent.assisted_response:
                reason = "Verify requires ASSISTED RESPONSE"
            elif not facts.patch_candidate_id:
                reason = "Verify is independent of Patch and needs a PatchCandidate"
            if reason:
                blocked.append(
                    BlockedAgent(
                        name=cap.name,
                        reason=reason,
                        capability_class=cap.capability_class,
                    )
                )
    return blocked


def _selected(facts: OrchestratorFacts, blocked: list[BlockedAgent]) -> list[AgentName]:
    denied = {item.name for item in blocked}
    chosen: list[AgentName] = []
    if facts.effective is FirstResponderIntent.off:
        return []
    for name in (AgentName.scout, AgentName.diagnose, AgentName.fortify):
        if name not in denied:
            chosen.append(name)
    if facts.effective is FirstResponderIntent.assisted_response and AgentName.probe not in denied:
        chosen.append(AgentName.probe)
    if facts.effective is FirstResponderIntent.assisted_response and AgentName.patch not in denied:
        chosen.append(AgentName.patch)
    if facts.effective is FirstResponderIntent.assisted_response and AgentName.verify not in denied:
        chosen.append(AgentName.verify)
    return chosen


def decide(facts: OrchestratorFacts) -> FirstResponderDecision:
    """Pure decision. Callers persist the result; this function has no side effects."""
    now = utcnow()
    correlation = _correlation(facts)
    blocked = _blocked(facts)
    selected = _selected(facts, blocked)
    q_values = list(facts.q_values) if facts.q_values else None

    def build(
        action: OrchestratorAction,
        *,
        state: EvidenceLifecycleState,
        transition: str,
        cancelled: bool = False,
        cancel_reason: str | None = None,
        selected_override: list[AgentName] | None = None,
    ) -> FirstResponderDecision:
        return FirstResponderDecision(
            id=new_id(IdPrefix.frd),
            project_id=facts.project_id,
            application_id=facts.application_id,
            created_at=now,
            orchestrator_version=ORCHESTRATOR_VERSION,
            intent_mode=facts.intent,
            effective_mode=facts.effective,
            action=action,
            evidence_state=state,
            trigger_event_ids=list(facts.trigger_event_ids),
            live_policy_decision_id=facts.live_policy_decision_id,
            incident_id=facts.incident_id,
            selected_agents=selected_override if selected_override is not None else selected,
            blocked_agents=blocked,
            correlation=correlation,
            confirmation=ConfirmationState.unverified,
            recommended_control=facts.shadow_action,
            simulated=facts.simulated,
            budget_usd_remaining=facts.budget_usd_remaining,
            deadline_at=now + timedelta(seconds=FIRST_RESPONDER_DEADLINE_S),
            retry_count=facts.retry_count,
            loop_count=facts.loop_count,
            cancelled=cancelled,
            cancel_reason=cancel_reason,
            transition=transition,
            q_values=q_values,
        )

    if facts.auto_execute_requested:
        return build(
            OrchestratorAction.reject_auto_execute,
            state=EvidenceLifecycleState.observed,
            transition="reject_auto_execute",
            selected_override=[],
        )
    if facts.already_decided_id:
        return build(
            OrchestratorAction.record_observation,
            state=EvidenceLifecycleState.observed,
            transition=f"idempotent:{facts.already_decided_id}",
            selected_override=[],
        )
    if facts.loop_count >= MAX_FIRST_RESPONDER_LOOPS:
        return build(
            OrchestratorAction.cancel_run,
            state=EvidenceLifecycleState.observed,
            transition="loop_prevention",
            cancelled=True,
            cancel_reason="loop prevention",
            selected_override=[],
        )
    if facts.deadline_passed:
        return build(
            OrchestratorAction.defer_budget,
            state=EvidenceLifecycleState.observed,
            transition="deadline",
            cancelled=True,
            cancel_reason="deadline exceeded",
            selected_override=[],
        )
    if facts.budget_usd_remaining <= 0:
        return build(
            OrchestratorAction.defer_budget,
            state=EvidenceLifecycleState.observed,
            transition="budget",
            cancelled=True,
            cancel_reason="budget exhausted",
            selected_override=[],
        )
    if facts.effective is FirstResponderIntent.off or not facts.application_connected:
        return build(
            OrchestratorAction.cancel_run,
            state=EvidenceLifecycleState.observed,
            transition="mode_off_or_disconnected",
            cancelled=True,
            cancel_reason="first-responder OFF or application disconnected",
            selected_override=[],
        )
    if facts.secret_like or not facts.telemetry_authenticated:
        return build(
            OrchestratorAction.ignore,
            state=EvidenceLifecycleState.observed,
            transition="untrusted_telemetry",
            selected_override=[],
        )
    if correlation.confidence is CorrelationConfidence.unknown:
        action = (
            OrchestratorAction.escalate_to_analyst
            if facts.effective is FirstResponderIntent.assisted_response
            else OrchestratorAction.correlate
        )
        state = (
            EvidenceLifecycleState.suspected
            if facts.effective is FirstResponderIntent.assisted_response
            else EvidenceLifecycleState.observed
        )
        return build(action, state=state, transition="correlate_unknown")
    if AgentName.probe in selected:
        return build(
            OrchestratorAction.confirm_with_probe,
            state=EvidenceLifecycleState.suspected,
            transition="assisted_probe_eligible",
        )
    if AgentName.patch in selected:
        return build(
            OrchestratorAction.propose_worktree_patch,
            state=EvidenceLifecycleState.recommended,
            transition="assisted_patch_worktree",
        )
    if AgentName.verify in selected:
        if facts.independent_verification_id:
            return build(
                OrchestratorAction.verify_independently,
                state=EvidenceLifecycleState.verified,
                transition="independent_verification_executed",
            )
        return build(
            OrchestratorAction.verify_independently,
            state=EvidenceLifecycleState.recommended,
            transition="verify_eligible_not_executed",
        )
    if selected:
        return build(
            OrchestratorAction.investigate,
            state=EvidenceLifecycleState.observed,
            transition="monitor_investigate",
        )
    if facts.shadow_action and facts.shadow_action != "noop":
        return build(
            OrchestratorAction.recommend_control,
            state=EvidenceLifecycleState.recommended,
            transition="recommend_not_executed",
        )
    if facts.effective is FirstResponderIntent.assisted_response:
        return build(
            OrchestratorAction.escalate_to_analyst,
            state=EvidenceLifecycleState.suspected,
            transition="assisted_escalate",
        )
    return build(
        OrchestratorAction.record_observation,
        state=EvidenceLifecycleState.observed,
        transition="record",
    )


@dataclass
class TransitionLog:
    entries: list[str] = field(default_factory=list)

    def add(self, item: str) -> None:
        self.entries.append(item)


def budget_defaults() -> tuple[float, int]:
    return FIRST_RESPONDER_MAX_USD, FIRST_RESPONDER_DEADLINE_S
