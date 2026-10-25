from __future__ import annotations

from datetime import timedelta

import pytest
from standards.catalog import load_json

from security_agents.checks.library import default_library, empty_library
from security_agents.contracts import AgentName, ScopeStatus
from security_agents.standards.roe import (
    APPENDIX_B_COVERAGE,
    PLANNING_COVERAGE,
    RoeError,
    build_roe,
    check_roe,
    coverage_table,
    scope_digest,
)

from ..conftest import NOW, TARGET_ID, make_scope

pytestmark = pytest.mark.standards

OWNERSHIP = "access_control.ownership@1"


def roe_for(scope=None):
    scope = scope or make_scope()
    return build_roe(scope, default_library().list_public(), audit_event_ids=["aud_01SCOPECHECK"])


def test_roe_is_built_from_the_active_scope():
    scope = make_scope()
    roe = roe_for(scope)
    assert roe.scope_id == scope.id
    assert roe.scope_digest == scope_digest(scope)
    assert [t.target_id for t in roe.targets] == [TARGET_ID]
    assert roe.requested_by == "usr_requester" and roe.approved_by == "usr_approver"
    assert roe.valid_from == scope.approved_at and roe.valid_until == scope.expires_at
    assert roe.permitted_check_templates == [
        "access_control.authentication@1",
        "access_control.ownership@1",
        "input_validation.bounds@1",
    ]
    assert set(roe.permitted_agents) == {AgentName.PROBE, AgentName.VERIFY}
    assert roe.prohibited_actions and roe.stop_conditions and roe.data_handling
    assert roe.audit_event_ids == ["aud_01SCOPECHECK"]
    assert roe.alignment.startswith("aligned with NIST SP 800-115")
    assert roe.reference["status_on_csrc"] == "Final"


def test_roe_build_is_pure_and_deterministic():
    assert roe_for().model_dump() == roe_for().model_dump()


def test_roe_lists_only_runnable_templates_in_scope_categories():
    narrow = make_scope(categories=["access_control.ownership"])
    assert roe_for(narrow).permitted_check_templates == [OWNERSHIP]
    assert build_roe(make_scope(), empty_library().list_public()).permitted_check_templates == []


@pytest.mark.parametrize("status", [ScopeStatus.PENDING, ScopeStatus.EXPIRED, ScopeStatus.REVOKED])
def test_roe_is_not_issued_for_an_inactive_scope(status):
    with pytest.raises(RoeError):
        roe_for(make_scope(status=status))


def test_roe_is_not_issued_for_an_unapproved_scope():
    with pytest.raises(RoeError):
        roe_for(make_scope(approved_by=None))


def allowed(roe, scope, *, now=NOW, agent=AgentName.PROBE, target=TARGET_ID, template=OWNERSHIP):
    return check_roe(roe, scope, now, agent=agent, target_id=target, template_ref=template)


def test_current_roe_allows_a_listed_check():
    scope = make_scope()
    decision = allowed(roe_for(scope), scope)
    assert decision.allowed and decision.reason == "OK"


def test_missing_roe_is_denied():
    decision = allowed(None, make_scope())
    assert not decision.allowed and decision.reason == "MISSING_ROE"


def test_roe_for_another_scope_is_denied():
    other = make_scope().model_copy(update={"id": "scp_other"})
    decision = allowed(roe_for(other), make_scope())
    assert decision.reason == "ROE_SCOPE_MISMATCH"


def test_roe_is_stale_when_the_scope_changes_after_issue():
    scope = make_scope()
    roe = roe_for(scope)
    widened = scope.model_copy(
        update={"allowed_check_categories": [*scope.allowed_check_categories, "other"]}
    )
    assert allowed(roe, widened).reason == "ROE_STALE"


def test_roe_is_denied_once_the_scope_is_revoked():
    scope = make_scope()
    roe = roe_for(scope)
    revoked = scope.model_copy(update={"status": ScopeStatus.REVOKED})
    assert allowed(roe, revoked).reason == "ROE_SCOPE_NOT_ACTIVE"


def test_roe_window_is_enforced():
    scope = make_scope()
    roe = roe_for(scope)
    assert allowed(roe, scope, now=scope.expires_at).reason == "ROE_EXPIRED"
    assert allowed(roe, scope, now=scope.approved_at - timedelta(seconds=1)).reason == "ROE_NOT_YET_VALID"


def test_unlisted_target_template_and_agent_are_denied():
    scope = make_scope()
    roe = roe_for(scope)
    assert allowed(roe, scope, target="tgt_elsewhere").reason == "ROE_TARGET_NOT_LISTED"
    assert allowed(roe, scope, template="made.up@1").reason == "ROE_TEMPLATE_NOT_PERMITTED"
    assert allowed(roe, scope, agent=AgentName.SCOUT).reason == "ROE_AGENT_NOT_PERMITTED"


def test_altered_roe_document_is_denied():
    scope = make_scope()
    roe = roe_for(scope)
    tampered = roe.model_copy(
        update={"permitted_check_templates": [*roe.permitted_check_templates, "made.up@1"]}
    )
    assert allowed(tampered, scope, template="made.up@1").reason == "ROE_DIGEST_MISMATCH"


def test_coverage_table_covers_every_pinned_sp_800_115_heading():
    pinned = load_json("nist-sp800-115.json")
    assert {s["section"] for s in pinned["roe_template_sections"]} == set(APPENDIX_B_COVERAGE)
    assert {s["section"] for s in pinned["planning_sections"]} == set(PLANNING_COVERAGE)
    rows = coverage_table()
    assert len(rows) == len(APPENDIX_B_COVERAGE) + len(PLANNING_COVERAGE)
    assert {row["status"] for row in rows} <= {"covered", "partial", "not covered", "not applicable"}
    assert any(row["status"] == "not covered" for row in rows), "honest gaps must remain visible"
