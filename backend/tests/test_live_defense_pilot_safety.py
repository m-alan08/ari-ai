"""Defense Lab pilot safety acceptance suite.

Maps each pilot acceptance criterion to an executable check:

* no model output, ingest payload, or typed actor field can grant defensive
  permissions (the 15-check gate only trusts the authenticated session);
* unsafe kinds (isolate, firewall, credential rotation, deploy, db mutation,
  patch, disable_application, principal blocks) can never execute even with a
  perfect grant + channel;
* every individual gate failure still fails closed;
* shadow/v6 inference abstains when its serving assumptions do not hold;
* enforcement can only reach the owned-test loopback adapter.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.live_defense.adapters import AdapterDenied, call, registered_control_url
from backend.live_defense.authorize import (
    AuthorizationInput,
    ChannelView,
    GrantView,
    check,
)
from backend.live_defense.policy import decide_inference
from backend.live_defense.quarantine import secret_like_reason
from monitoring.detect import WindowView
from shared.base import utcnow
from shared.live_defense import (
    AUTHORIZATION_CHECKS,
    CONTROL_PATH,
    ISOLATED_FIXTURE_SOURCE,
    AuthorizationContext,
    InferenceStatus,
    LiveControlKind,
    LiveControlTargetType,
)

OWNER = "usr_owner12345"
APP = "app_staging1"
PROJECT = "prj_demo1"

_GRANT = GrantView(
    id="cgr_grant1",
    application_id=APP,
    allowed_kinds=(
        LiveControlKind.rate_limit.value,
        LiveControlKind.block_source.value,
        LiveControlKind.increase_logging.value,
        LiveControlKind.escalate.value,
    ),
    max_duration_s=3600,
    hourly_budget=10,
    revoked_at=None,
    expires_at=None,
)
_CHANNEL = ChannelView(host="127.0.0.1", port=8443, path=CONTROL_PATH, revoked_at=None)


def _inp(**over) -> AuthorizationInput:
    """A fully valid authorization request; tests break one assumption each."""
    base = dict(
        session_user_id=OWNER,
        claimed_actor_id=None,
        project_id=PROJECT,
        application_id=APP,
        application_exists=True,
        application_environment="owned_test",
        kind=LiveControlKind.rate_limit.value,
        target_type=LiveControlTargetType.route.value,
        target_value="GET /payments/{payment_id}",
        duration_s=900,
        max_requests=50,
        grant=_GRANT,
        channel=_CHANNEL,
        emergency_stop_active=False,
        unresolved_control=False,
        actions_last_hour=0,
        now=utcnow(),
        application_disconnected=False,
    )
    base.update(over)
    return AuthorizationInput(**base)


def _names(decision) -> set[str]:
    return {c.name for c in decision.checks if not c.passed}


def test_gate_runs_all_15_named_checks() -> None:
    assert len(AUTHORIZATION_CHECKS) == 15
    decision = check(_inp())
    assert {c.name for c in decision.checks} == set(AUTHORIZATION_CHECKS)
    assert decision.allowed
    assert not decision.abstain


def test_model_supplied_actor_never_authorizes() -> None:
    """A model (or request body) typing usr_* is not an authenticated identity."""
    for claimed in (OWNER, "usr_attacker99", "system"):
        decision = check(_inp(claimed_actor_id=claimed))
        assert not decision.allowed
        assert "authenticated_session" in _names(decision)


def test_no_session_never_authorizes() -> None:
    decision = check(_inp(session_user_id=None))
    assert not decision.allowed
    decision = check(_inp(session_user_id="not-a-user"))
    assert not decision.allowed


@pytest.mark.parametrize(
    "kind",
    [
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
    ],
)
def test_unsafe_kinds_denied_and_abstain_even_with_perfect_grant(kind: str) -> None:
    """Network containment, account disable, and infra mutation are not for sale."""
    decision = check(_inp(kind=kind, target_type="none", target_value=""))
    assert not decision.allowed
    assert decision.abstain
    assert "action_allowlist" in _names(decision)


@pytest.mark.parametrize(
    "overrides,failed",
    [
        ({"application_exists": False}, "application_registered"),
        ({"application_id": None}, "application_registered"),
        ({"application_disconnected": True}, "application_registered"),
        ({"application_environment": "production"}, "environment_owned_test"),
        ({"application_environment": "staging"}, "environment_owned_test"),
        ({"application_environment": None}, "environment_owned_test"),
        ({"grant": None}, "owner_grant_exact_kind"),
        ({"emergency_stop_active": True}, "kill_switch_clear"),
        ({"unresolved_control": True}, "no_unresolved_control"),
        ({"actions_last_hour": 10}, "hourly_budget"),
        ({"duration_s": 3601}, "duration_within_grant"),
        ({"duration_s": 4}, "duration_within_grant"),
        ({"channel": None}, "control_channel_allowlist"),
    ],
)
def test_each_broken_assumption_fails_closed(overrides: dict, failed: str) -> None:
    decision = check(_inp(**overrides))
    assert not decision.allowed
    assert failed in _names(decision)


def test_revoked_or_expired_grant_denied() -> None:
    revoked = GrantView(**{**_GRANT.__dict__, "revoked_at": utcnow()})
    assert not check(_inp(grant=revoked)).allowed
    expired = GrantView(**{**_GRANT.__dict__, "expires_at": utcnow() - timedelta(seconds=1)})
    assert not check(_inp(grant=expired)).allowed


def test_grant_wrong_app_or_kind_denied() -> None:
    other_app = GrantView(**{**_GRANT.__dict__, "application_id": "app_other"})
    decision = check(_inp(grant=other_app))
    assert not decision.allowed
    assert "owner_grant_exact_app" in _names(decision)
    escalate_only = GrantView(
        **{**_GRANT.__dict__, "allowed_kinds": (LiveControlKind.escalate.value,)}
    )
    decision = check(_inp(grant=escalate_only))
    assert not decision.allowed
    assert "owner_grant_exact_kind" in _names(decision)


def test_block_source_only_fixture_ip_no_cidr() -> None:
    base = dict(
        kind=LiveControlKind.block_source.value,
        target_type=LiveControlTargetType.source_ip.value,
    )
    ok = check(_inp(**base, target_value=ISOLATED_FIXTURE_SOURCE))
    assert ok.allowed, [c.model_dump() for c in ok.checks]
    for bad in ("10.0.0.0/8", "192.168.1.1-192.168.1.50", "8.8.8.8", "127.0.0.1", "", "corp-proxy"):
        decision = check(_inp(**base, target_value=bad))
        assert not decision.allowed, bad


def test_non_loopback_channel_denied() -> None:
    for host in ("0.0.0.0", "localhost", "10.1.2.3", "adapter.internal", "[::1]"):
        channel = ChannelView(host=host, port=8443, path=CONTROL_PATH, revoked_at=None)
        decision = check(_inp(channel=channel))
        assert not decision.allowed, host
    channel = ChannelView(host="127.0.0.1", port=443, path=CONTROL_PATH, revoked_at=None)
    assert not check(_inp(channel=channel)).allowed
    channel = ChannelView(host="127.0.0.1", port=8443, path="/other", revoked_at=None)
    assert not check(_inp(channel=channel)).allowed


def test_adapter_ssrf_rechecks_at_send_time() -> None:
    """Second layer: even if the gate were bypassed, the adapter refuses."""
    for url in (
        "http://10.0.0.5:8443" + CONTROL_PATH,
        "https://127.0.0.1:8443" + CONTROL_PATH,
        "http://127.0.0.1:8443/other",
        "http://127.0.0.1:8443" + CONTROL_PATH + "?x=1",
        "http://user:pw@127.0.0.1:8443" + CONTROL_PATH,
        "http://127.0.0.1:80" + CONTROL_PATH,
    ):
        with pytest.raises(AdapterDenied):
            call(url, "hkt_ctl_test", {"op": "apply", "control_id": "lct_x"})
    with pytest.raises(AdapterDenied):
        registered_control_url("localhost", 8443)


def test_adapter_recrosschecks_block_source_target() -> None:
    url = f"http://127.0.0.1:8443{CONTROL_PATH}"
    with pytest.raises(AdapterDenied):
        call(
            url,
            "hkt_ctl_test",
            {
                "op": "apply",
                "control_id": "lct_x",
                "kind": "block_source",
                "target_value": "8.8.8.8",
            },
        )


def _view(**kw) -> WindowView:
    requests = kw.get("requests", 20)
    e4 = kw.get("status_4xx", 18)
    e5 = kw.get("status_5xx", 0)
    base = dict(
        route="GET /payments/{payment_id}",
        requests=requests,
        status_4xx=e4,
        status_5xx=e5,
        successes=max(0, requests - e4 - e5),
        auth_failures=18,
        anomaly_score=0.9,
        principals=(),
        source="owned_staging",
    )
    base.update(kw)
    return WindowView(**base)


def test_v6_abstains_on_unauthenticated_source() -> None:
    out = decide_inference(
        source="simulated",
        windows=[_view()],
        hits=[],
        auth=AuthorizationContext.verified,
        window_end=utcnow(),
    )
    assert out.status is InferenceStatus.skipped_source


def test_v6_abstains_on_stale_and_empty_windows() -> None:
    stale = decide_inference(
        source="owned_staging",
        windows=[_view()],
        hits=[],
        auth=AuthorizationContext.verified,
        window_end=utcnow() - timedelta(hours=2),
    )
    assert stale.status is InferenceStatus.skipped_stale
    empty = decide_inference(
        source="owned_staging",
        windows=[],
        hits=[],
        auth=AuthorizationContext.verified,
        window_end=utcnow(),
    )
    assert empty.status is InferenceStatus.skipped_incomplete


def test_v6_abstains_without_ownership_context() -> None:
    out = decide_inference(
        source="owned_staging",
        windows=[_view()],
        hits=[],
        auth=AuthorizationContext.unverified,
        window_end=utcnow(),
    )
    # HTTP-200-heavy traffic without verified ownership must not feed the model.
    assert out.status in {
        InferenceStatus.skipped_quiet_window,
        InferenceStatus.skipped_authorization,
    }
    assert out.status is not InferenceStatus.ran


def test_secret_like_telemetry_is_quarantined() -> None:
    for payload in (
        {"api_key": "sk-ant-" + "a" * 40},
        {"note": "password=hunter2"},
        "Bearer " + "x" * 60,
    ):
        assert secret_like_reason(payload)
    assert secret_like_reason({"requests": 20, "status_4xx": 3}) is None


def test_gate_exception_fails_closed() -> None:
    """A malformed input type raises inside a check; the gate must still deny."""
    decision = check(_inp(duration_s="nine hundred"))  # type: ignore[arg-type]
    assert not decision.allowed
