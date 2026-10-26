"""Deterministic authorization gate for Live Defense Phase 4.

No LLM, neural net, or typed ``usr_*`` string is the final authority. Every
allowlisted and rules-originated action goes through :func:`check`. Fail closed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from shared.live_defense import (
    AUTHORIZATION_CHECKS,
    CONTROL_PATH,
    ISOLATED_FIXTURE_SOURCE,
    MAX_CONTROL_DURATION_S,
    MIN_CONTROL_DURATION_S,
    RATE_LIMIT_MIN_REQUESTS,
    AuthorizationCheckResult,
    LiveControlKind,
    LiveControlTargetType,
    StagingEnvironment,
    is_owned_test_block_source,
)

_USER_ID = re.compile(r"^usr_[0-9A-Za-z_-]{8,64}$")
_ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD) /[A-Za-z0-9_{}/.\-]*$")

UNSAFE_KINDS = frozenset(
    {
        "isolate",
        "block_principal",
        "block_principal_long",
        "block_top_principal_long",
        "credential_rotation",
        "firewall",
        "deploy",
        "db_mutation",
        "patch",
        "disable_application",
    }
)


@dataclass(frozen=True)
class GrantView:
    id: str
    application_id: str
    allowed_kinds: tuple[str, ...]
    max_duration_s: int
    hourly_budget: int
    revoked_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class ChannelView:
    host: str
    port: int
    path: str
    revoked_at: datetime | None


@dataclass(frozen=True)
class AuthorizationInput:
    """Everything the gate may read. Model output is not a field here."""

    session_user_id: str | None
    claimed_actor_id: str | None
    project_id: str
    application_id: str | None
    application_exists: bool
    application_environment: str | None
    kind: str
    target_type: str
    target_value: str
    duration_s: int
    max_requests: int | None
    grant: GrantView | None
    channel: ChannelView | None
    emergency_stop_active: bool
    unresolved_control: bool
    actions_last_hour: int
    now: datetime
    application_disconnected: bool = False
    rl_gated_pilot: bool = False


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    abstain: bool
    checks: tuple[AuthorizationCheckResult, ...]
    binding_sha256: str
    reason: str

    @property
    def results(self) -> list[AuthorizationCheckResult]:
        return list(self.checks)


def binding_hash(
    *,
    kind: str,
    target_type: str,
    target_value: str,
    duration_s: int,
    application_id: str,
    project_id: str,
    max_requests: int | None = None,
    grant_id: str = "",
) -> str:
    rate = "" if max_requests is None else str(max_requests)
    material = (
        f"{kind}|{target_type}|{target_value}|{duration_s}|{rate}|"
        f"{application_id}|{project_id}|{grant_id}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _ok(name: str, passed: bool, reason: str) -> AuthorizationCheckResult:
    return AuthorizationCheckResult(name=name, passed=passed, reason=reason[:500])


def _session(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.claimed_actor_id is not None:
        return _ok(
            "authenticated_session",
            False,
            "Typed usr_* (or any body actor) is not authenticated identity.",
        )
    if not inp.session_user_id or not _USER_ID.match(inp.session_user_id):
        return _ok("authenticated_session", False, "No authenticated session.")
    return _ok("authenticated_session", True, "Actor is the authenticated session.")


def _allowlist(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.kind in UNSAFE_KINDS:
        return _ok(
            "action_allowlist",
            False,
            f"{inp.kind} is not an allowlisted Phase 4 action; abstain and escalate.",
        )
    try:
        LiveControlKind(inp.kind)
    except ValueError:
        return _ok("action_allowlist", False, f"{inp.kind} is not allowlisted.")
    return _ok("action_allowlist", True, f"{inp.kind} is allowlisted.")


def _app_registered(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.application_disconnected:
        return _ok("application_registered", False, "Application is disconnected.")
    if inp.rl_gated_pilot:
        return _ok(
            "application_registered",
            False,
            "RL-gated pilot is shadow-only; control authorization is denied.",
        )
    if not inp.application_id or not inp.application_exists:
        return _ok("application_registered", False, "Application is not registered.")
    return _ok("application_registered", True, "Application is registered in the project.")


def _environment(inp: AuthorizationInput) -> AuthorizationCheckResult:
    try:
        env = StagingEnvironment(inp.application_environment or "")
    except ValueError:
        return _ok(
            "environment_owned_test",
            False,
            "Production and unknown environments are denied.",
        )
    if env not in (StagingEnvironment.owned_test, StagingEnvironment.sandbox):
        return _ok("environment_owned_test", False, "Environment is not owned-test/sandbox.")
    return _ok("environment_owned_test", True, f"Environment is {env.value}.")


def _grant_kind(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.grant is None:
        return _ok("owner_grant_exact_kind", False, "No owner grant.")
    if inp.kind not in inp.grant.allowed_kinds:
        return _ok("owner_grant_exact_kind", False, "Grant does not include this kind.")
    return _ok("owner_grant_exact_kind", True, "Grant includes this kind.")


def _grant_app(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.grant is None or not inp.application_id:
        return _ok("owner_grant_exact_app", False, "Grant is not bound to this application.")
    if inp.grant.application_id != inp.application_id:
        return _ok("owner_grant_exact_app", False, "Grant application_id mismatch.")
    return _ok("owner_grant_exact_app", True, "Grant is bound to this application.")


def _grant_active(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.grant is None:
        return _ok("grant_active", False, "No owner grant.")
    if inp.grant.revoked_at is not None:
        return _ok("grant_active", False, "Grant is revoked.")
    if inp.grant.expires_at is not None and inp.grant.expires_at <= inp.now:
        return _ok("grant_active", False, "Grant is expired.")
    return _ok("grant_active", True, "Grant is active.")


def _duration(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.duration_s < MIN_CONTROL_DURATION_S or inp.duration_s > MAX_CONTROL_DURATION_S:
        return _ok("duration_within_grant", False, "Duration outside platform bounds.")
    if inp.grant is None:
        return _ok("duration_within_grant", False, "No grant to bound duration.")
    if inp.duration_s > inp.grant.max_duration_s:
        return _ok("duration_within_grant", False, "Duration exceeds grant maximum.")
    return _ok("duration_within_grant", True, "Duration is within the grant.")


def _budget(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.grant is None:
        return _ok("hourly_budget", False, "No grant budget.")
    if inp.actions_last_hour >= inp.grant.hourly_budget:
        return _ok("hourly_budget", False, "Hourly grant budget exhausted.")
    return _ok("hourly_budget", True, "Hourly budget remaining.")


def _binding(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if not inp.application_id or not inp.kind or not inp.target_type:
        return _ok("approval_binding", False, "Incomplete kind/target/duration/app tuple.")
    expected_type = {
        LiveControlKind.rate_limit.value: LiveControlTargetType.route.value,
        LiveControlKind.block_source.value: LiveControlTargetType.source_ip.value,
        LiveControlKind.increase_logging.value: LiveControlTargetType.application.value,
        LiveControlKind.escalate.value: LiveControlTargetType.none.value,
    }.get(inp.kind)
    if expected_type is None or inp.target_type != expected_type:
        return _ok(
            "approval_binding",
            False,
            "Target type is not bound to this kind (principal-wide and free-form denied).",
        )
    if inp.kind == LiveControlKind.rate_limit.value:
        if inp.max_requests is None or inp.max_requests < RATE_LIMIT_MIN_REQUESTS:
            return _ok("approval_binding", False, "rate_limit requires max_requests >= 1.")
        if not _ROUTE.match(inp.target_value):
            return _ok("approval_binding", False, "rate_limit target must be METHOD /path.")
    if inp.kind == LiveControlKind.block_source.value and not inp.target_value:
        return _ok("approval_binding", False, "block_source requires an exact address.")
    return _ok("approval_binding", True, "Approval is bound to kind/target/duration/app.")


def _blast(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.kind == LiveControlKind.block_source.value:
        if "/" in inp.target_value or "-" in inp.target_value.split(":")[0]:
            return _ok("blast_radius", False, "CIDRs and ranges are denied.")
        if inp.target_type != LiveControlTargetType.source_ip.value:
            return _ok("blast_radius", False, "Principal-wide block is denied.")
        return _ok("blast_radius", True, "Exact-source block; no CIDR.")
    if inp.kind == LiveControlKind.rate_limit.value:
        if inp.target_type != LiveControlTargetType.route.value:
            return _ok("blast_radius", False, "Rate limit must be request/route scoped.")
        return _ok("blast_radius", True, "Request-scoped rate limit.")
    if inp.kind in (
        LiveControlKind.increase_logging.value,
        LiveControlKind.escalate.value,
    ):
        return _ok("blast_radius", True, "No traffic-blocking blast radius.")
    return _ok("blast_radius", False, "Unknown kind; fail closed.")


def _address(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.kind != LiveControlKind.block_source.value:
        return _ok("address_policy", True, "Not a source block.")
    if is_owned_test_block_source(inp.target_value):
        return _ok(
            "address_policy",
            True,
            f"Owned-test fixture source {ISOLATED_FIXTURE_SOURCE}.",
        )
    return _ok(
        "address_policy",
        False,
        "Only the owned-test fixture IP is allowed; public, private, "
        "loopback, link-local, CIDR, ranges, malformed, and protected "
        "addresses are denied.",
    )


def _channel(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.kind == LiveControlKind.escalate.value:
        return _ok("control_channel_allowlist", True, "Escalate does not call an adapter.")
    if inp.channel is None or inp.channel.revoked_at is not None:
        return _ok("control_channel_allowlist", False, "No active control channel.")
    if inp.channel.host != "127.0.0.1":
        return _ok("control_channel_allowlist", False, "Adapter host is not the loopback fixture.")
    if inp.channel.path != CONTROL_PATH:
        return _ok(
            "control_channel_allowlist", False, "Adapter path is not the fixed control path."
        )
    if inp.channel.port < 1024 or inp.channel.port > 65535:
        return _ok(
            "control_channel_allowlist", False, "Adapter port is not in the registered range."
        )
    return _ok("control_channel_allowlist", True, "Channel is the registered loopback fixture.")


def _kill(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.emergency_stop_active:
        return _ok("kill_switch_clear", False, "Emergency stop is active.")
    return _ok("kill_switch_clear", True, "Emergency stop is not active.")


def _unresolved(inp: AuthorizationInput) -> AuthorizationCheckResult:
    if inp.unresolved_control:
        return _ok(
            "no_unresolved_control",
            False,
            "An unresolved failed control blocks further actions.",
        )
    return _ok("no_unresolved_control", True, "No unresolved control.")


_IMPLS = (
    _session,
    _allowlist,
    _app_registered,
    _environment,
    _grant_kind,
    _grant_app,
    _grant_active,
    _duration,
    _budget,
    _binding,
    _blast,
    _address,
    _channel,
    _kill,
    _unresolved,
)


def check(inp: AuthorizationInput) -> GateDecision:
    """Run all 15 checks. Exceptions become denials. Never skip the list."""
    if len(_IMPLS) != 15:
        return GateDecision(
            allowed=False,
            abstain=True,
            checks=tuple(
                _ok(name, False, "Gate is misconfigured; fail closed.")
                for name in AUTHORIZATION_CHECKS
            ),
            binding_sha256="0" * 64,
            reason="Gate is misconfigured; fail closed.",
        )
    results: list[AuthorizationCheckResult] = []
    try:
        for fn in _IMPLS:
            results.append(fn(inp))
    except Exception as exc:  # noqa: BLE001 — fail closed
        results.append(_ok("fail_closed", False, f"Gate exception: {type(exc).__name__}"))
    by_name = {item.name: item for item in results if item.name != "fail_closed"}
    ordered = []
    for name in AUTHORIZATION_CHECKS:
        item = by_name.get(name)
        if item is None:
            ordered.append(_ok(name, False, "Check did not run; fail closed."))
        else:
            ordered.append(item)
    allowed = bool(ordered) and all(item.passed for item in ordered)
    abstain = (
        inp.kind in UNSAFE_KINDS
        or not allowed
        and inp.kind not in {k.value for k in LiveControlKind}
    )
    if inp.kind in UNSAFE_KINDS:
        abstain = True
        allowed = False
    digest = binding_hash(
        kind=inp.kind,
        target_type=inp.target_type,
        target_value=inp.target_value,
        duration_s=inp.duration_s,
        application_id=inp.application_id or "",
        project_id=inp.project_id,
        max_requests=inp.max_requests,
        grant_id="" if inp.grant is None else inp.grant.id,
    )
    reason = (
        "authorized"
        if allowed
        else next((item.reason for item in ordered if not item.passed), "denied")
    )
    return GateDecision(
        allowed=allowed,
        abstain=abstain,
        checks=tuple(ordered),
        binding_sha256=digest,
        reason=reason[:500],
    )
