"""Pure authorization gate: 15 checks, fail closed, no model authority."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.live_defense.authorize import AuthorizationInput, ChannelView, GrantView, check
from backend.live_defense.state import ALLOWED_TRANSITIONS, IllegalControlTransition, transition
from shared.live_defense import AUTHORIZATION_CHECKS, ISOLATED_FIXTURE_SOURCE, LiveControlStatus

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
USER = "usr_01JFIXTURE0000000000000USR"
APP = "app_01JFIXTURE0000000000000APP"
PRJ = "prj_01JFIXTURE0000000000000PRJ"


def _grant() -> GrantView:
    return GrantView(
        id="cgr_01JFIXTURE0000000000000CGR",
        application_id=APP,
        allowed_kinds=("rate_limit", "block_source", "increase_logging", "escalate"),
        max_duration_s=300,
        hourly_budget=5,
        revoked_at=None,
        expires_at=None,
    )


def _channel() -> ChannelView:
    return ChannelView(host="127.0.0.1", port=18080, path="/live-defense/control", revoked_at=None)


def _inp(**overrides: object) -> AuthorizationInput:
    base = dict(
        session_user_id=USER,
        claimed_actor_id=None,
        project_id=PRJ,
        application_id=APP,
        application_exists=True,
        application_environment="owned_test",
        kind="rate_limit",
        target_type="route",
        target_value="GET /payments/pay_fixture",
        duration_s=60,
        max_requests=2,
        grant=_grant(),
        channel=_channel(),
        emergency_stop_active=False,
        unresolved_control=False,
        actions_last_hour=0,
        now=NOW,
    )
    base.update(overrides)
    return AuthorizationInput(**base)  # type: ignore[arg-type]


def test_all_fifteen_checks_run_and_happy_path_allows() -> None:
    decision = check(_inp())
    assert [c.name for c in decision.checks] == list(AUTHORIZATION_CHECKS)
    assert len(decision.checks) == 15
    assert decision.allowed
    assert all(c.passed for c in decision.checks)


def test_disconnected_application_is_denied_without_new_check() -> None:
    decision = check(_inp(application_disconnected=True))
    assert not decision.allowed
    assert [c.name for c in decision.checks] == list(AUTHORIZATION_CHECKS)
    assert any(
        c.name == "application_registered" and not c.passed and "disconnected" in c.reason
        for c in decision.checks
    )


def test_rl_gated_pilot_is_denied_without_new_check() -> None:
    decision = check(_inp(rl_gated_pilot=True))
    assert not decision.allowed
    assert len(decision.checks) == 15
    assert [c.name for c in decision.checks] == list(AUTHORIZATION_CHECKS)
    assert any(
        c.name == "application_registered" and not c.passed and "shadow-only" in c.reason
        for c in decision.checks
    )


def test_typed_usr_in_claimed_actor_is_not_identity() -> None:
    decision = check(_inp(claimed_actor_id=USER))
    assert not decision.allowed
    assert any(c.name == "authenticated_session" and not c.passed for c in decision.checks)


def test_missing_session_fails_closed() -> None:
    decision = check(_inp(session_user_id=None))
    assert not decision.allowed


def test_isolate_and_principal_block_are_not_allowlisted() -> None:
    for kind in ("isolate", "block_principal", "credential_rotation"):
        decision = check(
            _inp(kind=kind, target_type="source_ip", target_value=ISOLATED_FIXTURE_SOURCE)
        )
        assert not decision.allowed
        assert decision.abstain


def test_cidr_and_loopback_and_link_local_denied() -> None:
    for value in ("198.51.100.0/24", "127.0.0.1", "::1", "169.254.1.1", "10.0.0.5", "0.0.0.0"):
        decision = check(
            _inp(
                kind="block_source",
                target_type="source_ip",
                target_value=value,
                max_requests=None,
            )
        )
        assert not decision.allowed, value


def test_public_private_malformed_and_protected_sources_denied() -> None:
    for value in (
        "8.8.8.8",
        "1.1.1.1",
        "192.0.2.1",
        "203.0.113.50",
        "255.255.255.255",
        "198.51.100.66/32",
        "198.51.100.66-198.51.100.70",
        " 198.51.100.66",
        "198.51.100.66 ",
        "198.51.100.66\n",
        "not-an-ip",
        "::ffff:198.51.100.66",
        "169.254.169.254",
        "0.0.0.0/0",
        "*",
    ):
        decision = check(
            _inp(
                kind="block_source",
                target_type="source_ip",
                target_value=value,
                max_requests=None,
            )
        )
        assert not decision.allowed, value
        assert any(c.name == "address_policy" and not c.passed for c in decision.checks), value


def test_binding_hash_includes_rate_and_grant() -> None:
    a = check(_inp(max_requests=1))
    b = check(_inp(max_requests=9))
    assert a.binding_sha256 != b.binding_sha256
    other_grant = GrantView(
        id="cgr_01JOTHER000000000000000CGR",
        application_id=APP,
        allowed_kinds=("rate_limit", "block_source", "increase_logging", "escalate"),
        max_duration_s=300,
        hourly_budget=5,
        revoked_at=None,
        expires_at=None,
    )
    c = check(_inp(grant=other_grant, max_requests=1))
    assert a.binding_sha256 != c.binding_sha256


def test_expired_and_revoked_grants_denied() -> None:
    expired = GrantView(
        id="cgr_01JEXPIRED0000000000000CGR",
        application_id=APP,
        allowed_kinds=("rate_limit", "block_source", "increase_logging", "escalate"),
        max_duration_s=300,
        hourly_budget=5,
        revoked_at=None,
        expires_at=NOW,
    )
    revoked = GrantView(
        id="cgr_01JREVOKED0000000000000CGR",
        application_id=APP,
        allowed_kinds=("rate_limit", "block_source", "increase_logging", "escalate"),
        max_duration_s=300,
        hourly_budget=5,
        revoked_at=NOW,
        expires_at=None,
    )
    assert not check(_inp(grant=expired)).allowed
    assert not check(_inp(grant=revoked)).allowed


def test_hourly_budget_exhausted_denied() -> None:
    decision = check(_inp(actions_last_hour=5))
    assert not decision.allowed
    assert any(c.name == "hourly_budget" and not c.passed for c in decision.checks)


def test_isolated_fixture_source_is_allowed() -> None:
    decision = check(
        _inp(
            kind="block_source",
            target_type="source_ip",
            target_value=ISOLATED_FIXTURE_SOURCE,
            max_requests=None,
        )
    )
    assert decision.allowed


def test_rules_originated_still_requires_grant() -> None:
    decision = check(_inp(grant=None))
    assert not decision.allowed
    assert any(c.name == "owner_grant_exact_kind" and not c.passed for c in decision.checks)


def test_kill_switch_and_unresolved_fail_closed() -> None:
    assert not check(_inp(emergency_stop_active=True)).allowed
    assert not check(_inp(unresolved_control=True)).allowed


def test_channel_must_be_loopback_fixed_path() -> None:
    decision = check(
        _inp(
            channel=ChannelView(
                host="example.com", port=18080, path="/live-defense/control", revoked_at=None
            )
        )
    )
    assert not decision.allowed
    decision = check(
        _inp(
            channel=ChannelView(
                host="127.0.0.1", port=80, path="/live-defense/control", revoked_at=None
            )
        )
    )
    assert not decision.allowed


def test_recommendation_cannot_transition_to_executing() -> None:
    assert (
        LiveControlStatus.executing
        not in ALLOWED_TRANSITIONS[LiveControlStatus.pending_authorization]
    )
    try:
        transition(LiveControlStatus.pending_authorization, LiveControlStatus.executing)
        raise AssertionError("pending_authorization must not jump to executing")
    except IllegalControlTransition:
        pass
    assert (
        transition(LiveControlStatus.authorized, LiveControlStatus.executing)
        is LiveControlStatus.executing
    )
