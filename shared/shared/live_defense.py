"""Live Defense contracts (Phase 2–7A).

Phase 2/3 types describe recorded evidence for a customer shadow deployment:
connected staging applications, ingest-token metadata, timestamps from the
job worker and runtime ingest, a topology derived from those records,
analyst reviews, and genuine ``LivePolicyDecision`` rows produced only when
deterministic rules or validated frozen-v6 inference actually ran.

``AnalystReview.executes`` is always false. ``LivePolicyDecision.enforcement``
is always ``NOT_EXECUTED``. Those rows never become a live control.

Phase 4 adds a **separate** control lifecycle (``LiveControl``) and a
deterministic authorization DTO. Executable actions exist only after
``authorize.check``, only against a registered owned-test/sandbox app, and
only for the four allowlisted kinds. Production is still not a
``StagingEnvironment`` value. ``DefenseHealth.status`` is always ``unknown``:
HTTP 200 from this API is not a healthy verdict.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator, model_validator

from shared.assessment import AgentName
from shared.base import CommitSha, Sha256Hex, ShortText, StrictModel, UtcDatetime
from shared.ids import (
    AnalystHandoffId,
    AnalystReviewId,
    ArtifactId,
    ConnectedApplicationId,
    ControlChannelId,
    ControlGrantId,
    DeployCommitMappingId,
    EmergencyStopId,
    FindingId,
    FirstResponderDecisionId,
    FirstResponderInvocationId,
    IncidentId,
    IngestTokenId,
    JobId,
    LiveControlId,
    LivePolicyDecisionId,
    MonitoringEventId,
    ProjectId,
    RecommendationId,
    RepositoryId,
    UserId,
)
from shared.monitoring import MonitoringEvent, PrincipalRef, RuntimeSource
from shared.rl import ActionScore, DefenseActionName, NodeName


class StagingEnvironment(StrEnum):
    """Environments a shadow-pilot app may declare. Production is not allowed."""

    sandbox = "sandbox"
    owned_test = "owned_test"


class ApplicationVerification(StrEnum):
    unverified = "unverified"
    ingest_seen = "ingest_seen"
    token_revoked = "token_revoked"
    disconnected = "disconnected"


class ConnectedApplication(StrictModel):
    """Owner-registered staging app under watch. Never includes secrets."""

    id: ConnectedApplicationId
    project_id: ProjectId
    name: ShortText
    environment: StagingEnvironment
    ingest_configured: bool = Field(
        description="True when a non-revoked ingest token exists; the token itself is never returned"
    )
    last_ingest_at: UtcDatetime | None = None
    verification: ApplicationVerification
    shadow_mode: Literal[True] = True
    control_channel_configured: bool = Field(
        default=False,
        description="True when a control channel exists; the HMAC secret is never returned",
    )
    disconnected_at: UtcDatetime | None = None
    created_by: UserId
    created_at: UtcDatetime


class ConnectedApplicationCreate(StrictModel):
    name: ShortText
    environment: StagingEnvironment = StagingEnvironment.sandbox


class IngestTokenView(StrictModel):
    """Metadata for an ingest token. The secret is never included after issuance."""

    id: IngestTokenId
    project_id: ProjectId
    application_id: ConnectedApplicationId | None = None
    created_at: UtcDatetime
    revoked_at: UtcDatetime | None = None
    last_used_at: UtcDatetime | None = None


class DefenseHealth(StrictModel):
    """Recorded timestamps for ingest and the job worker. Not a liveness verdict."""

    project_id: ProjectId
    worker_seen_at: UtcDatetime | None = Field(
        default=None, description="Latest jobs.heartbeat_at; null if no worker has heartbeated"
    )
    queue_depth: int | None = Field(
        default=None, ge=0, description="Queued jobs; null if the queue was not readable"
    )
    last_ingest_at: UtcDatetime | None = Field(
        default=None, description="Latest runtime_windows.window_end for this project"
    )
    last_runtime_event_at: UtcDatetime | None = Field(
        default=None, description="Latest monitoring event with source=runtime"
    )
    status: Literal["unknown"] = "unknown"
    status_reason: ShortText = (
        "No recorded health-check producer. Timestamps are evidence only; "
        "HTTP 200 is not a healthy verdict."
    )
    worker_status: Literal["unknown"] = "unknown"
    worker_status_reason: ShortText = (
        "No worker liveness producer. Heartbeat timestamps are evidence only."
    )
    telemetry_status: Literal["unknown"] = "unknown"
    telemetry_status_reason: ShortText = "Ingest timestamps are not a telemetry health verdict."
    policy_status: Literal["unknown"] = "unknown"
    policy_status_reason: ShortText = (
        "No live policy-health producer. Shadow decisions stay NOT_EXECUTED; "
        "HTTP 200 is not policy health."
    )


class ComponentNodeKind(StrEnum):
    application = "application"
    route = "route"


class ComponentHealthState(StrEnum):
    unknown = "unknown"
    checking = "checking"
    healthy = "healthy"
    attention = "attention"


class ComponentObservationSource(StrEnum):
    ingest = "ingest"
    github = "github"
    none = "none"


class ComponentNode(StrictModel):
    id: ShortText
    label: ShortText
    kind: ComponentNodeKind
    critical: bool = False


class ComponentLink(StrictModel):
    source_id: ShortText
    target_id: ShortText


class ComponentObservation(StrictModel):
    node_id: ShortText
    state: ComponentHealthState
    last_checked_at: UtcDatetime | None = None
    source: ComponentObservationSource
    simulated: bool
    note: ShortText | None = None


class ComponentGraph(StrictModel):
    """Topology from registered apps and observed ingest routes. Missing checks stay unknown."""

    project_id: ProjectId
    nodes: list[ComponentNode] = Field(default_factory=list)
    links: list[ComponentLink] = Field(default_factory=list)
    observations: list[ComponentObservation] = Field(default_factory=list)
    generated_at: UtcDatetime
    note: ShortText = (
        "healthy is never inferred from HTTP 200. attention is only set when the "
        "latest recorded runtime score was anomalous; every other node is unknown."
    )


class AnalystReviewDecision(StrEnum):
    accept = "accept"
    reject = "reject"
    needs_investigation = "needs_investigation"


class AnalystReview(StrictModel):
    """Human review of a stored recommendation or live shadow decision.

    Accept does not execute anything and is never converted into an RL label.
    """

    id: AnalystReviewId
    project_id: ProjectId
    recommendation_id: RecommendationId | None = None
    live_policy_decision_id: LivePolicyDecisionId | None = None
    decision: AnalystReviewDecision
    reason: ShortText | None = None
    reviewed_by: UserId
    reviewed_at: UtcDatetime
    executes: Literal[False] = False
    enforcement: Literal["none"] = "none"

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> AnalystReview:
        rec = self.recommendation_id is not None
        live = self.live_policy_decision_id is not None
        if rec == live:
            raise ValueError(
                "exactly one of recommendation_id or live_policy_decision_id is required"
            )
        return self


class AnalystReviewCreate(StrictModel):
    decision: AnalystReviewDecision
    reason: ShortText | None = None


class ShadowProducer(StrEnum):
    """What actually produced the persisted shadow decision."""

    rules = "rules"
    v6_shadow = "v6_shadow"
    abstention = "abstention"


class AuthorizationContext(StrEnum):
    """Whether the reporter supplied ownership classification with the window."""

    verified = "verified"
    unverified = "unverified"
    missing = "missing"


class InferenceStatus(StrEnum):
    """Whether frozen v6 inference ran. Q-values exist only when ``ran``."""

    ran = "ran"
    skipped_quiet_window = "skipped_quiet_window"
    skipped_incomplete = "skipped_incomplete"
    skipped_stale = "skipped_stale"
    skipped_source = "skipped_source"
    skipped_unavailable = "skipped_unavailable"
    skipped_schema = "skipped_schema"
    skipped_mask = "skipped_mask"
    skipped_authorization = "skipped_authorization"
    skipped_target = "skipped_target"


SHADOW_DECISION_CAVEAT = (
    "Shadow recommendation from authenticated staging telemetry. Not executed. "
    "Q-values appear only when frozen v6 inference actually ran."
)


class LivePolicyDecision(StrictModel):
    """One shadow decision for one ingest window. Never an executed control."""

    id: LivePolicyDecisionId
    project_id: ProjectId
    application_id: ConnectedApplicationId | None = None
    created_at: UtcDatetime
    window_end: UtcDatetime
    telemetry_source: RuntimeSource
    simulated: bool
    producer: ShadowProducer
    rules_ran: bool
    inference_ran: bool
    inference_status: InferenceStatus
    action: DefenseActionName | None = None
    node: NodeName | None = None
    top_actions: list[ActionScore] | None = Field(
        default=None,
        description="Q ranking from frozen v6; null when inference did not run",
    )
    q_margin: float | None = Field(
        default=None, description="Null when inference did not run; never synthesized"
    )
    observation: list[float] | None = Field(
        default=None, description="Null when inference did not run; never synthesized"
    )
    policy_version: ShortText | None = None
    checkpoint_sha256: Sha256Hex | None = None
    abstention_reason: ShortText | None = None
    observation_complete: bool
    feature_schema_ok: bool
    action_mask_valid: bool
    freshness_ok: bool
    authorization_context: AuthorizationContext
    enforcement: Literal["NOT_EXECUTED"] = "NOT_EXECUTED"
    executed: Literal[False] = False
    evidence_event_ids: list[MonitoringEventId] = Field(default_factory=list)
    rule_hits: list[ShortText] = Field(default_factory=list)
    principal_targets: list[PrincipalRef] = Field(default_factory=list)
    feature_mapping_note: ShortText | None = None
    caveat: ShortText = SHADOW_DECISION_CAVEAT


# ---------------------------------------------------------------------------
# Phase 4 — controlled enforcement (owned test only)
# ---------------------------------------------------------------------------

ISOLATED_FIXTURE_SOURCE = "198.51.100.66"
"""The only address the gate and adapter accept as a ``block_source`` target.

RFC 5737 TEST-NET-2. Exact string match only — no CIDR, ranges, parsing, or
public/private/loopback/link-local unicast. Authz and the HMAC adapter share
:func:`is_owned_test_block_source`. This is the isolated owned-test fixture,
not a production attribution identity.
"""

OWNED_TEST_OTHER_PROBE_SOURCE = "203.0.113.50"
"""RFC 5737 TEST-NET-3. Fixture-only *other-source* probe, never a block target."""

OWNED_TEST_BLOCK_SOURCES = frozenset({ISOLATED_FIXTURE_SOURCE})
OWNED_TEST_PROBE_SOURCES = frozenset({ISOLATED_FIXTURE_SOURCE, OWNED_TEST_OTHER_PROBE_SOURCE})
FIXTURE_SOURCE_HEADER = "x-ari-source"
"""Owned-test probe header. Spoofable; never production identity or auth."""


def is_owned_test_block_source(value: str) -> bool:
    """Exact allowlist membership. Authz, adapter, and fixture must all use this."""
    return value in OWNED_TEST_BLOCK_SOURCES


def is_owned_test_probe_source(value: str) -> bool:
    """Whether ``x-ari-source`` may be honored on the owned-test fixture."""
    return value in OWNED_TEST_PROBE_SOURCES


CONTROL_PATH: Literal["/live-defense/control"] = "/live-defense/control"
MIN_CONTROL_DURATION_S = 5
MAX_CONTROL_DURATION_S = 3600
RATE_LIMIT_MIN_REQUESTS = 1
AUTHORIZATION_CHECKS: tuple[str, ...] = (
    "authenticated_session",
    "action_allowlist",
    "application_registered",
    "environment_owned_test",
    "owner_grant_exact_kind",
    "owner_grant_exact_app",
    "grant_active",
    "duration_within_grant",
    "hourly_budget",
    "approval_binding",
    "blast_radius",
    "address_policy",
    "control_channel_allowlist",
    "kill_switch_clear",
    "no_unresolved_control",
)

CONTROL_LIFECYCLE_CAVEAT = (
    "Owned-test control. Not customer production. Adapter HTTP 200 is not "
    "effectiveness. Shadow recommendations stay NOT_EXECUTED."
)


class LiveControlKind(StrEnum):
    """The only actions that have an adapter or a recorded human escalation."""

    rate_limit = "rate_limit"
    block_source = "block_source"
    increase_logging = "increase_logging"
    escalate = "escalate"


class LiveControlTargetType(StrEnum):
    route = "route"
    source_ip = "source_ip"
    application = "application"
    none = "none"


class LiveControlStatus(StrEnum):
    """Control lifecycle. Distinct from NIST SP 800-61r3 incident phases."""

    pending_authorization = "pending_authorization"
    authorized = "authorized"
    executing = "executing"
    verifying = "verifying"
    verified = "verified"
    failed = "failed"
    expired = "expired"
    rolled_back = "rolled_back"
    escalated = "escalated"
    rejected = "rejected"
    emergency_stopped = "emergency_stopped"


class LiveControlOrigin(StrEnum):
    """Who proposed the action. Origin never bypasses the gate."""

    analyst = "analyst"
    rules = "rules"
    v6_shadow = "v6_shadow"


class AuthorizationCheckResult(StrictModel):
    name: ShortText
    passed: bool
    reason: ShortText


class ControlTarget(StrictModel):
    type: LiveControlTargetType
    value: ShortText = ""


class ControlGrant(StrictModel):
    """Owner grant for exact kinds on one owned-test/sandbox application."""

    id: ControlGrantId
    project_id: ProjectId
    application_id: ConnectedApplicationId
    allowed_kinds: list[LiveControlKind] = Field(min_length=1)
    max_duration_s: int = Field(ge=MIN_CONTROL_DURATION_S, le=MAX_CONTROL_DURATION_S)
    hourly_budget: int = Field(ge=1, le=20)
    created_by: UserId
    created_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    revoked_at: UtcDatetime | None = None


class ControlGrantCreate(StrictModel):
    application_id: ConnectedApplicationId
    allowed_kinds: list[LiveControlKind] = Field(min_length=1)
    max_duration_s: int = Field(default=300, ge=MIN_CONTROL_DURATION_S, le=MAX_CONTROL_DURATION_S)
    hourly_budget: int = Field(default=5, ge=1, le=20)
    expires_at: UtcDatetime | None = None


class ControlChannelView(StrictModel):
    """Registered adapter destination. Secret is never included after issuance."""

    id: ControlChannelId
    project_id: ProjectId
    application_id: ConnectedApplicationId
    host: Literal["127.0.0.1"]
    port: int = Field(ge=1024, le=65535)
    path: Literal["/live-defense/control"] = CONTROL_PATH
    created_at: UtcDatetime
    revoked_at: UtcDatetime | None = None


class ControlChannelCreate(StrictModel):
    """Owner registers a loopback port. Arbitrary URLs are rejected by construction."""

    port: int = Field(ge=1024, le=65535)


class ControlChannelCreated(ControlChannelView):
    hmac_secret: ShortText = Field(description="Shown once; never re-returned")


class ControlAuthorizationCreate(StrictModel):
    """Analyst authorization request. Actor identity is the session, not this body."""

    kind: LiveControlKind
    target: ControlTarget
    duration_s: int = Field(ge=MIN_CONTROL_DURATION_S, le=MAX_CONTROL_DURATION_S)
    rationale: ShortText | None = None
    live_policy_decision_id: LivePolicyDecisionId | None = None
    max_requests: int | None = Field(
        default=None,
        ge=RATE_LIMIT_MIN_REQUESTS,
        le=10_000,
        description="Required for rate_limit; ignored otherwise",
    )


class LiveControlVerification(StrictModel):
    observed_effect: bool
    adapter_http_status: int | None = None
    probe_http_statuses: list[int] = Field(default_factory=list)
    method: ShortText
    detail: ShortText
    verified_at: UtcDatetime
    adapter_http_200_is_not_effectiveness: Literal[True] = True


class LiveControl(StrictModel):
    """One authorized (or denied) owned-test control. Not a NIST incident."""

    id: LiveControlId
    project_id: ProjectId
    application_id: ConnectedApplicationId
    live_policy_decision_id: LivePolicyDecisionId | None = None
    grant_id: ControlGrantId | None = None
    kind: LiveControlKind
    target: ControlTarget
    duration_s: int = Field(ge=MIN_CONTROL_DURATION_S, le=MAX_CONTROL_DURATION_S)
    status: LiveControlStatus
    origin: LiveControlOrigin
    binding_sha256: Sha256Hex
    requested_by: UserId
    requested_at: UtcDatetime
    authorized_at: UtcDatetime | None = None
    executing_at: UtcDatetime | None = None
    verified_at: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    ended_at: UtcDatetime | None = None
    checks: list[AuthorizationCheckResult] = Field(default_factory=list)
    verification: LiveControlVerification | None = None
    unresolved: bool = False
    simulated: Literal[False] = False
    environment_label: Literal["OWNED TEST ENVIRONMENT"] = "OWNED TEST ENVIRONMENT"
    caveat: ShortText = CONTROL_LIFECYCLE_CAVEAT
    max_requests: int | None = None
    rationale: ShortText | None = None
    denial_reason: ShortText | None = None


class EmergencyStop(StrictModel):
    id: EmergencyStopId
    project_id: ProjectId
    application_id: ConnectedApplicationId | None = None
    active: bool
    requested_by: UserId
    requested_at: UtcDatetime
    cleared_at: UtcDatetime | None = None
    verified_cleared: bool = False
    note: ShortText | None = None


class EmergencyStopCreate(StrictModel):
    application_id: ConnectedApplicationId | None = None
    note: ShortText | None = None


class LiveControlRollbackCreate(StrictModel):
    reason: ShortText | None = None


# Phase 5 — customer shadow-pilot readiness (no enforcement)
# ---------------------------------------------------------------------------

PILOT_REPORT_CAVEATS: tuple[str, ...] = (
    "Shadow mode records ingest and recommendations. It does not execute controls.",
    "HTTP 200 from this API is not attack success and is not a healthy verdict.",
    "This report does not claim that shadow mode prevented breaches.",
    "Q-values are stored scores, not a probability that an attack is happening now.",
    "Analyst accept is a recorded opinion and does not execute.",
)

PILOT_UNSUPPORTED_CLAIMS: tuple[str, ...] = (
    "shadow prevented breaches",
    "HTTP 200 means the attack succeeded",
    "health is healthy because this endpoint returned 200",
)


class IngestTokenRotated(StrictModel):
    """New ingest secret shown once, with ids of tokens revoked in the same rotation."""

    id: IngestTokenId
    project_id: ProjectId
    application_id: ConnectedApplicationId
    token: str = Field(description="Shown once; only its SHA-256 is stored")
    created_at: UtcDatetime
    revoked_ids: list[IngestTokenId] = Field(default_factory=list)


class PilotTrailEntry(StrictModel):
    """One recorded audit or ingest fact. Labels are not invented."""

    at: UtcDatetime
    action: ShortText
    subject_type: ShortText
    subject_id: ShortText
    detail: ShortText | None = None


class PilotReport(StrictModel):
    """Exportable shadow-pilot snapshot. Not an effectiveness claim."""

    project_id: ProjectId
    generated_at: UtcDatetime
    shadow_mode: Literal[True] = True
    applications: list[ConnectedApplication] = Field(default_factory=list)
    ingest_windows: int = Field(ge=0)
    shadow_decisions: int = Field(ge=0)
    reviews: int = Field(ge=0)
    incidents: int = Field(ge=0)
    health: DefenseHealth
    investigation_trail: list[PilotTrailEntry] = Field(default_factory=list)
    caveats: list[ShortText] = Field(default_factory=lambda: list(PILOT_REPORT_CAVEATS))
    unsupported_claims: list[ShortText] = Field(
        default_factory=lambda: list(PILOT_UNSUPPORTED_CLAIMS)
    )


# ---------------------------------------------------------------------------
# Phase 7A — deterministic first-responder orchestration (no AUTO-EXECUTE)
# ---------------------------------------------------------------------------

ORCHESTRATOR_VERSION = "7a.1.1"
FIRST_RESPONDER_JOB_KINDS: tuple[str, ...] = (
    "live_defense_first_responder",
    "live_defense_agent_step",
)
CONTROL_JOB_KINDS: tuple[str, ...] = (
    "live_defense_execute",
    "live_defense_verify",
    "live_defense_rollback",
    "live_defense_expire",
    "live_defense_emergency_stop",
)
SEVEN_AGENT_NAMES: tuple[AgentName, ...] = (
    AgentName.scout,
    AgentName.probe,
    AgentName.trace,
    AgentName.diagnose,
    AgentName.patch,
    AgentName.fortify,
    AgentName.verify,
)
MAX_FIRST_RESPONDER_LOOPS = 1
FIRST_RESPONDER_DEADLINE_S = 900
FIRST_RESPONDER_MAX_USD = 3.0

FIRST_RESPONDER_CAVEAT = (
    "Deterministic first-responder decision. LLM prose is never proof of a "
    "confirmed threat. RL recommendations are not executed defense. "
    "AUTO-EXECUTE does not exist."
)


class FirstResponderIntent(StrEnum):
    """Server-enforced modes. MONITOR and SHADOW are the same mode.

    There is no AUTO-EXECUTE value. Unknown or auto-execute input fails closed.
    """

    off = "off"
    monitor = "monitor"
    assisted_response = "assisted_response"


def parse_first_responder_intent(value: object) -> FirstResponderIntent:
    """Normalize client aliases. AUTO-EXECUTE and unknown values are rejected."""
    if isinstance(value, FirstResponderIntent):
        return value
    if not isinstance(value, str):
        raise ValueError("first-responder intent must be a string")
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    if key in {"auto_execute", "autoexecute", "auto"}:
        raise ValueError("AUTO-EXECUTE is not a permitted first-responder mode")
    if key in {"off", "disabled", "none"}:
        return FirstResponderIntent.off
    if key in {"monitor", "monitoring", "shadow"}:
        return FirstResponderIntent.monitor
    if key in {"assisted_response", "assisted"}:
        return FirstResponderIntent.assisted_response
    raise ValueError(f"unknown first-responder intent {value!r}")


IntentField = Annotated[FirstResponderIntent, BeforeValidator(parse_first_responder_intent)]


class StopKind(StrEnum):
    """OFF is stop-monitoring. Kill-switch is stop-enforcement. They are not the same."""

    stop_monitoring = "stop_monitoring"
    stop_enforcement = "stop_enforcement"
    none = "none"


class OrchestratorAction(StrEnum):
    """Deterministic first-responder actions. None of these execute a LiveControl."""

    ignore = "ignore"
    record_observation = "record_observation"
    correlate = "correlate"
    investigate = "investigate"
    confirm_with_probe = "confirm_with_probe"
    propose_worktree_patch = "propose_worktree_patch"
    verify_independently = "verify_independently"
    recommend_control = "recommend_control"
    escalate_to_analyst = "escalate_to_analyst"
    defer_budget = "defer_budget"
    cancel_run = "cancel_run"
    reject_auto_execute = "reject_auto_execute"


class CorrelationConfidence(StrEnum):
    """Explicit correlation confidence. Never a probability. UNKNOWN without a mapping."""

    unknown = "unknown"
    none = "none"
    low = "low"
    medium = "medium"


class AgentCapabilityClass(StrEnum):
    investigative_readonly = "investigative_readonly"
    code_changing = "code_changing"
    disruptive_scoped = "disruptive_scoped"


class AgentInvocationStatus(StrEnum):
    eligible = "eligible"
    blocked = "blocked"
    queued = "queued"
    skipped_no_mapping = "skipped_no_mapping"
    skipped_no_snapshot = "skipped_no_snapshot"
    skipped_mode = "skipped_mode"
    skipped_no_scope = "skipped_no_scope"
    skipped_no_approval = "skipped_no_approval"
    skipped_no_patch = "skipped_no_patch"
    completed_unverified = "completed_unverified"
    failed = "failed"
    cancelled = "cancelled"


class AnalystHandoffState(StrEnum):
    draft = "draft"
    queued = "queued"
    delivery_pending = "delivery_pending"
    delivered = "delivered"
    delivery_failed = "delivery_failed"
    acknowledged = "acknowledged"
    investigating = "investigating"
    confirmed = "confirmed"
    unverified = "unverified"
    resolved = "resolved"
    cancelled = "cancelled"


class EvidenceLifecycleState(StrEnum):
    """UI case states. Recommended is not executed. RL rec is never Executed."""

    observed = "observed"
    suspected = "suspected"
    verified = "verified"
    recommended = "recommended"
    authorized = "authorized"
    executed = "executed"
    failed = "failed"
    rolled_back = "rolled_back"


class ConfirmationState(StrEnum):
    """Confirmed vs unverified stay distinct. LLM prose cannot set confirmed."""

    unverified = "unverified"
    confirmed = "confirmed"


class FirstResponderModeUpdate(StrictModel):
    """Owner-attributable mode change. AUTO-EXECUTE is rejected by parse."""

    intent: IntentField
    note: ShortText | None = None


class FirstResponderEffectivePermissions(StrictModel):
    """Computed app permissions. Org intent is never copied as effective rights."""

    intent: FirstResponderIntent
    effective: FirstResponderIntent
    application_connected: bool
    monitoring_active: bool
    enforcement_active: bool
    kill_switch_active: bool
    authorized_scope_active: bool
    trusted_mapping: bool
    reasons: list[ShortText] = Field(default_factory=list)
    org_intent_is_not_app_permissions: Literal[True] = True
    auto_execute: Literal[False] = False


class FirstResponderModeView(StrictModel):
    project_id: ProjectId
    intent: FirstResponderIntent
    updated_by: UserId | None = None
    updated_at: UtcDatetime | None = None
    previous_intent: FirstResponderIntent | None = None
    stop_kind: StopKind = StopKind.none
    cancelled_first_responder_jobs: int | None = Field(default=None, ge=0)
    independently_authorized_control_jobs: int | None = Field(default=None, ge=0)
    retention_policy_unchanged: Literal[True] = True
    auto_execute: Literal[False] = False
    caveat: ShortText = (
        "OFF cancels first-responder jobs only. Independently authorized "
        "owned-test controls keep their own expiry, rollback, and kill-switch."
    )


class AgentCapability(StrictModel):
    """One runtime agent, derived from the real AgentSpec — not a prompt persona."""

    name: AgentName
    spec_version: ShortText
    capability_class: AgentCapabilityClass
    tools: list[ShortText] = Field(default_factory=list)
    sandbox_profile: ShortText
    network: Literal["none", "target-only"]
    write_access: Literal["none", "out_dir", "worktree"]
    silent_customer_deploy: Literal[False] = False
    notes: ShortText

    @field_validator("name")
    @classmethod
    def _seven_only(cls, value: AgentName) -> AgentName:
        if value not in SEVEN_AGENT_NAMES:
            raise ValueError("first-responder registry covers the seven security agents only")
        return value


class AgentCapabilityRegistry(StrictModel):
    orchestrator_version: ShortText = ORCHESTRATOR_VERSION
    complete: bool
    agents: list[AgentCapability] = Field(default_factory=list)
    missing_names: list[ShortText] = Field(default_factory=list)
    source: ShortText = "security_agents.agents.ALL_SPECS"
    auto_execute: Literal[False] = False


class DeployCommitMappingCreate(StrictModel):
    """Owner-attested deploy-to-commit mapping. Unsigned CI guesses are rejected."""

    repo_id: RepositoryId
    commit_sha: CommitSha
    deployed_at: UtcDatetime
    source: Literal["owner_attested"] = "owner_attested"


class DeployCommitMapping(StrictModel):
    id: DeployCommitMappingId
    project_id: ProjectId
    application_id: ConnectedApplicationId
    repo_id: RepositoryId
    commit_sha: CommitSha
    deployed_at: UtcDatetime
    source: Literal["owner_attested"] = "owner_attested"
    created_by: UserId
    created_at: UtcDatetime
    revoked_at: UtcDatetime | None = None


class RuntimeCodeCorrelation(StrictModel):
    application_id: ConnectedApplicationId | None = None
    mapping_id: DeployCommitMappingId | None = None
    commit_sha: CommitSha | None = None
    confidence: CorrelationConfidence
    finding_ids_at_commit: list[FindingId] = Field(default_factory=list)
    finding_is_not_proof_of_this_request: Literal[True] = True
    note: ShortText


class BlockedAgent(StrictModel):
    name: AgentName
    reason: ShortText
    capability_class: AgentCapabilityClass


class FirstResponderDecision(StrictModel):
    """Versioned decision record. Future RL must beat this baseline. No invented probabilities."""

    id: FirstResponderDecisionId
    project_id: ProjectId
    application_id: ConnectedApplicationId | None = None
    created_at: UtcDatetime
    orchestrator_version: ShortText = ORCHESTRATOR_VERSION
    intent_mode: FirstResponderIntent
    effective_mode: FirstResponderIntent
    action: OrchestratorAction
    evidence_state: EvidenceLifecycleState
    trigger_event_ids: list[MonitoringEventId] = Field(default_factory=list)
    live_policy_decision_id: LivePolicyDecisionId | None = None
    incident_id: IncidentId | None = None
    selected_agents: list[AgentName] = Field(default_factory=list)
    blocked_agents: list[BlockedAgent] = Field(default_factory=list)
    correlation: RuntimeCodeCorrelation
    confirmation: ConfirmationState = ConfirmationState.unverified
    llm_prose_is_not_confirmation: Literal[True] = True
    recommended_control: DefenseActionName | None = None
    recommended_is_not_executed: Literal[True] = True
    executed: Literal[False] = False
    auto_execute: Literal[False] = False
    simulated: bool = False
    budget_usd_remaining: float | None = None
    deadline_at: UtcDatetime | None = None
    retry_count: int = Field(default=0, ge=0)
    loop_count: int = Field(default=0, ge=0)
    cancelled: bool = False
    cancel_reason: ShortText | None = None
    transition: ShortText
    outcome_label: ShortText | None = Field(
        default=None, description="Human later; never auto-assigned as an RL label"
    )
    predicted_probability: None = None
    q_values: list[ActionScore] | None = Field(
        default=None, description="Copied only when frozen v6 inference actually ran"
    )
    caveat: ShortText = FIRST_RESPONDER_CAVEAT


class FirstResponderInvocation(StrictModel):
    """One attempted agent step. Missing execution is skipped, never faked as success."""

    id: FirstResponderInvocationId
    decision_id: FirstResponderDecisionId
    project_id: ProjectId
    agent: AgentName
    capability_class: AgentCapabilityClass
    status: AgentInvocationStatus
    provider_mode: Literal["fake", "live", "none"] = "none"
    independent_of_patch: bool = False
    worktree_only: bool = False
    silent_deploy: Literal[False] = False
    authorized_scope_id: ShortText | None = None
    job_id: JobId | None = None
    reason: ShortText | None = None
    created_at: UtcDatetime


class AnalystHandoffCreate(StrictModel):
    title: ShortText
    summary: ShortText
    assigned_to: UserId | None = None


class AnalystHandoffDeliveryAck(StrictModel):
    """Provider confirmation that an alert was received. Required before state=delivered."""

    provider: ShortText
    provider_message_id: ShortText
    received_at: UtcDatetime


class AnalystHandoff(StrictModel):
    id: AnalystHandoffId
    project_id: ProjectId
    application_id: ConnectedApplicationId | None = None
    decision_id: FirstResponderDecisionId
    state: AnalystHandoffState
    confirmation: ConfirmationState = ConfirmationState.unverified
    title: ShortText
    summary: ShortText
    evidence_event_ids: list[MonitoringEventId] = Field(default_factory=list)
    recommended_actions: list[ShortText] = Field(default_factory=list)
    threat_confirmed: bool = False
    delivery_provider: ShortText | None = None
    delivery_provider_message_id: ShortText | None = None
    delivery_attempted_at: UtcDatetime | None = None
    delivery_confirmed_at: UtcDatetime | None = None
    created_by: UserId
    created_at: UtcDatetime
    assigned_to: UserId | None = None
    caveat: ShortText = (
        "An alert is not delivered until the provider confirms receipt. "
        "Confirmed and unverified remain distinct. LLM prose cannot confirm a threat."
    )

    @model_validator(mode="after")
    def _delivery_requires_provider(self) -> AnalystHandoff:
        if self.state is AnalystHandoffState.delivered:
            if self.delivery_confirmed_at is None or not self.delivery_provider:
                raise ValueError("delivered requires provider confirmation")
        if self.confirmation is ConfirmationState.confirmed and not self.threat_confirmed:
            raise ValueError("confirmed requires executed Probe evidence, not LLM prose")
        if self.threat_confirmed and self.confirmation is not ConfirmationState.confirmed:
            raise ValueError("threat_confirmed requires confirmation=confirmed")
        return self


# ---------------------------------------------------------------------------
# Phase 7 observability — read-only investigation views (additive, no mutation)
# ---------------------------------------------------------------------------
#
# These DTOs are assembled by read endpoints from persisted records only.
# ``AgentExecutionView`` mirrors the Phase 7C ``AgentExecutionRecord`` payload
# stored inside ``FirstResponderInvocation.execution``; every field is optional
# so missing values render as "unavailable" rather than being fabricated.


class AgentExecutionView(StrictModel):
    """Recorded agent-loop outcome. Absent fields stay unknown; never invented."""

    provider_mode: Literal["fake", "live", "none"] | None = None
    model_id: ShortText | None = None
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    llm_turns: int | None = Field(default=None, ge=0)
    tool_call_count: int | None = Field(default=None, ge=0)
    started_at: UtcDatetime | None = None
    ended_at: UtcDatetime | None = None
    transcript_artifact_id: ArtifactId | None = None
    output_artifact_id: ArtifactId | None = None
    error_code: ShortText | None = None
    typed_output: bool | None = None
    hypothesis_count: int | None = Field(default=None, ge=0)
    execution_integrity: ShortText | None = None
    independent_verification_id: ShortText | None = None
    evidence_verified: bool | None = None
    confirmation: ShortText | None = None
    predicted_probability: None = None
    execution_is_not_confirmation: Literal[True] = True


class InvocationJobView(StrictModel):
    """Durable queue state joined onto one invocation. Retry = attempts > 1."""

    job_id: JobId
    kind: ShortText
    status: ShortText
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=0)
    run_after: UtcDatetime | None = None
    heartbeat_at: UtcDatetime | None = None
    cancel_requested: bool = False
    error_code: ShortText | None = None
    error_message: ShortText | None = None
    enqueued_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    stalled: bool = False
    wall_time_ms: int | None = Field(default=None, ge=0)


class FirstResponderInvocationView(StrictModel):
    """One invocation plus its queue state and optional execution record.

    ``effective_state`` is the canonical combined state: a terminal invocation
    status is authoritative; while the invocation is non-terminal the joined
    job row — which records what actually ran — wins, so a timed-out,
    recovery-required, waiting or cancelled job is never shown as in-progress.
    """

    invocation: FirstResponderInvocation
    job: InvocationJobView | None = None
    execution: AgentExecutionView | None = None
    execution_recorded: bool = False
    effective_state: ShortText = "eligible"
    token_usage_available: Literal[False] = False
    token_usage_note: ShortText = (
        "Per-invocation token usage is not persisted yet; shown as unavailable, not estimated."
    )


class InvestigationSnapshotView(StrictModel):
    """Whether the decision's mapped commit resolved to an indexed snapshot."""

    repo_id: RepositoryId | None = None
    commit_sha: CommitSha | None = None
    mapping_id: DeployCommitMappingId | None = None
    snapshot_id: ShortText | None = None
    resolved: bool = False
    note: ShortText


class InvestigationTimeline(StrictModel):
    """Event → decision → invocations → artifacts → handoff, from persisted rows."""

    decision: FirstResponderDecision
    trigger_events: list[MonitoringEvent] = Field(default_factory=list)
    application: ConnectedApplication | None = None
    mapping: DeployCommitMapping | None = None
    snapshot: InvestigationSnapshotView | None = None
    invocations: list[FirstResponderInvocationView] = Field(default_factory=list)
    handoff: AnalystHandoff | None = None
    final_state: ShortText
    caveat: ShortText = (
        "Persisted first-responder records only. Investigative execution is not "
        "a confirmed compromise and not executed enforcement. Missing fields are "
        "unavailable, never estimated."
    )


class InvestigationSummary(StrictModel):
    """One row of the bounded investigations list."""

    decision_id: FirstResponderDecisionId
    created_at: UtcDatetime
    action: ShortText
    evidence_state: ShortText
    confirmation: ShortText
    application_id: ConnectedApplicationId | None = None
    simulated: bool = False
    cancelled: bool = False
    trigger_event_count: int = Field(default=0, ge=0)
    invocations_total: int = Field(default=0, ge=0)
    invocations_queued_or_running: int = Field(default=0, ge=0)
    invocations_completed: int = Field(default=0, ge=0)
    invocations_failed: int = Field(default=0, ge=0)
    invocations_skipped: int = Field(default=0, ge=0)
    invocations_cancelled: int = Field(default=0, ge=0)
    handoff_state: ShortText | None = None
    # Persisted queue-state rollup for this decision's jobs (e.g. timed_out,
    # recovery_required) so stopped/recovery work is visible in the list.
    job_statuses: dict[str, int] = Field(default_factory=dict)


class InvestigationJobError(StrictModel):
    """A recent persisted job error, redacted before it leaves the API."""

    job_id: JobId
    kind: ShortText
    decision_id: FirstResponderDecisionId | None = None
    invocation_id: FirstResponderInvocationId | None = None
    agent: ShortText | None = None
    code: ShortText | None = None
    message: ShortText | None = None
    at: UtcDatetime | None = None


class JobLatencyStats(StrictModel):
    """Wall-clock job durations (queue + run). Not model latency."""

    samples: int = Field(ge=0)
    avg_ms: int | None = Field(default=None, ge=0)
    max_ms: int | None = Field(default=None, ge=0)


class WaitingJobView(StrictModel):
    """A first-responder job still waiting: queued on its backoff window or
    parked on a prerequisite (``waiting``, execution-reliability)."""

    job_id: JobId
    kind: ShortText
    status: ShortText = "queued"
    run_after: UtcDatetime | None = None
    enqueued_at: UtcDatetime | None = None
    attempts: int = Field(default=0, ge=0)


class FirstResponderDiagnostics(StrictModel):
    """Operational aggregate over persisted first-responder jobs and invocations.

    ``stalled`` means a running job whose lease expired (another worker may
    reclaim it). Cost is unavailable until execution records exist (Phase 7C).
    """

    generated_at: UtcDatetime
    investigations_total: int = Field(ge=0)
    decisions_cancelled: int = Field(ge=0)
    invocations_by_status: dict[str, int] = Field(default_factory=dict)
    failures_by_agent: dict[str, int] = Field(default_factory=dict)
    missing_prerequisites: dict[str, int] = Field(default_factory=dict)
    jobs_queued: int = Field(ge=0)
    jobs_waiting: int = Field(default=0, ge=0)
    jobs_running: int = Field(ge=0)
    jobs_stalled: int = Field(ge=0)
    jobs_succeeded: int = Field(ge=0)
    jobs_failed: int = Field(ge=0)
    jobs_cancelled: int = Field(ge=0)
    jobs_dead: int = Field(ge=0)
    jobs_timed_out: int = Field(default=0, ge=0)
    jobs_recovery_required: int = Field(default=0, ge=0)
    waiting_jobs: list[WaitingJobView] = Field(default_factory=list)
    recent_errors: list[InvestigationJobError] = Field(default_factory=list)
    agent_step_latency: JobLatencyStats | None = None
    cost_usd_total: float | None = Field(default=None, ge=0)
    cost_note: ShortText = (
        "Per-invocation cost requires persisted execution records (Phase 7C). "
        "Unavailable is reported, not estimated."
    )
    notes: list[ShortText] = Field(default_factory=list)
