"""Assessments, the orchestrator state machine and agent run records.

The transition table here is the single definition used by the backend
(persistence refuses illegal transitions with ``CONFLICT_STATE``) and by the
security-agents orchestrator (which decides *which* legal transition to take).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from shared.base import JsonObject, Sha256Hex, ShortText, StrictModel, UtcDatetime
from shared.errors import ErrorBody, ErrorCode
from shared.evidence import EvidenceRef
from shared.ids import (
    AgentRunId,
    ArtifactId,
    AssessmentId,
    ContextPackId,
    ProjectId,
    ReassessmentRequestId,
    RepositoryId,
    ScopeId,
    SnapshotId,
    UserId,
)
from shared.intelligence import SnapshotRef
from shared.tenancy import Budget, Usage


class AssessmentState(StrEnum):
    CREATED = "CREATED"
    SCOPE_CHECK = "SCOPE_CHECK"
    INDEXING = "INDEXING"
    FUNCTIONAL_REVIEW = "FUNCTIONAL_REVIEW"
    RECON = "RECON"
    VALIDATION = "VALIDATION"
    ANALYSIS = "ANALYSIS"
    DIAGNOSIS = "DIAGNOSIS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATION = "REMEDIATION"
    HARDENING = "HARDENING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


S = AssessmentState
TERMINAL_STATES: frozenset[AssessmentState] = frozenset(
    {S.COMPLETED, S.FAILED, S.CANCELLED, S.BUDGET_EXHAUSTED}
)

_FORWARD: dict[AssessmentState, frozenset[AssessmentState]] = {
    S.CREATED: frozenset({S.SCOPE_CHECK}),
    S.SCOPE_CHECK: frozenset({S.INDEXING}),
    S.INDEXING: frozenset({S.FUNCTIONAL_REVIEW}),
    S.FUNCTIONAL_REVIEW: frozenset({S.RECON}),
    # Each analysis phase may end early in REPORTING when it has nothing to hand on
    # (no hypotheses, nothing confirmed, no path, nothing to remediate). 0.5.0.
    S.RECON: frozenset({S.VALIDATION, S.REPORTING}),
    S.VALIDATION: frozenset({S.ANALYSIS, S.REPORTING}),
    S.ANALYSIS: frozenset({S.DIAGNOSIS, S.REPORTING}),
    S.DIAGNOSIS: frozenset({S.AWAITING_APPROVAL, S.REPORTING}),
    S.AWAITING_APPROVAL: frozenset({S.REMEDIATION, S.REPORTING}),
    S.REMEDIATION: frozenset({S.HARDENING, S.REPORTING}),
    S.HARDENING: frozenset({S.REPORTING}),
    S.REPORTING: frozenset({S.COMPLETED}),
}

ALLOWED_TRANSITIONS: dict[AssessmentState, frozenset[AssessmentState]] = {
    state: (
        frozenset()
        if state in TERMINAL_STATES
        else _FORWARD.get(state, frozenset())
        | {S.FAILED, S.CANCELLED, S.BUDGET_EXHAUSTED}
        | ({S.SCOPE_CHECK} if state not in (S.CREATED, S.SCOPE_CHECK) else set())
    )
    for state in AssessmentState
}


def can_transition(current: AssessmentState, target: AssessmentState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


class AssessmentTrigger(StrEnum):
    manual = "manual"
    monitoring = "monitoring"


class Assessment(StrictModel):
    id: AssessmentId
    project_id: ProjectId
    snapshot_id: SnapshotId | None = Field(
        default=None, description="Set once the commit is indexed; commit_sha is always set"
    )
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    repo_id: RepositoryId | None = Field(
        default=None,
        description="The one repository this assessment ran on (always set by the backend); "
        "findings link source lines to it, so multi-repository scopes need no guessing",
    )
    scope_id: ScopeId
    trigger: AssessmentTrigger
    reassessment_request_id: ReassessmentRequestId | None = None
    state: AssessmentState
    budget: Budget
    usage: Usage = Field(default_factory=Usage)
    error: ErrorBody | None = None
    created_by: UserId | None = Field(default=None, description="None for monitoring triggers")
    created_at: UtcDatetime
    updated_at: UtcDatetime
    completed_at: UtcDatetime | None = None


class AgentName(StrEnum):
    scout = "scout"
    probe = "probe"
    trace = "trace"
    diagnose = "diagnose"
    patch = "patch"
    fortify = "fortify"
    verify = "verify"
    reviewer = "reviewer"


SECURITY_AGENTS: tuple[AgentName, ...] = (
    AgentName.scout,
    AgentName.probe,
    AgentName.trace,
    AgentName.diagnose,
    AgentName.patch,
    AgentName.fortify,
    AgentName.verify,
)


class AgentRunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ToolCallOutcome(StrEnum):
    ok = "ok"
    error = "error"
    denied = "denied"


class ToolCallRecord(StrictModel):
    """One tool invocation requested by an agent, as enforced by the tool registry."""

    turn: int = Field(ge=0)
    tool_name: ShortText
    input_sha256: Sha256Hex
    outcome: ToolCallOutcome
    error_code: ErrorCode | None = None
    duration_ms: int = Field(ge=0)
    output_artifact_id: ArtifactId | None = None


class AgentRun(StrictModel):
    id: AgentRunId
    assessment_id: AssessmentId
    agent: AgentName
    step_key: ShortText = Field(description="Orchestrator step, e.g. 'recon' or 'patch:fnd_…'")
    attempt: int = Field(ge=1)
    status: AgentRunStatus
    provider_mode: ShortText = Field(
        description=(
            "'live' (real provider), 'replay' (recorded), 'fake' (test transport), "
            "or 'none' (deterministic / no model call)"
        )
    )
    model_id: ShortText | None = None
    prompt_version: ShortText | None = None
    input_artifact_id: ArtifactId | None = None
    output_artifact_id: ArtifactId | None = None
    transcript_artifact_id: ArtifactId | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: ErrorBody | None = None
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None


class AgentStepInput(StrictModel):
    """What the orchestrator hands an agent runner for one step."""

    assessment_id: AssessmentId
    agent: AgentName
    step_key: ShortText
    attempt: int = Field(ge=1)
    snapshot_ref: SnapshotRef
    scope_id: ScopeId
    context_pack_id: ContextPackId | None = None
    payload: JsonObject = Field(
        default_factory=dict,
        description="Role-specific typed input (security_agents defines per-role models)",
    )
    budget: Budget


class AgentStepResult(StrictModel):
    agent_run_id: AgentRunId
    agent: AgentName
    status: AgentRunStatus
    output: JsonObject = Field(
        default_factory=dict, description="Role-specific typed output, validated by the runner"
    )
    evidence: list[EvidenceRef] = Field(default_factory=list)
    handoff_to: list[AgentName] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: ErrorBody | None = None
