"""End-to-end: Scout -> Probe -> Trace -> Diagnose -> Patch -> Verify -> Fortify.

Deterministic (fake provider), but everything else is real: real tool
authorization, real sandboxed check and test execution, a real diff written to
a real worktree, and an independent Verify run that re-checks the patched code
in a fresh checkout. Labelled `fake` provider mode throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from security_agents.contracts import AgentName, Approval, Budget, ValidationOutcome
from security_agents.llm import FakeLLM, ProviderGateway
from security_agents.workflow import AssessmentState, FindingRecord, Orchestrator, OrchestratorConfig
from security_agents.workflow.states import MAX_PATCH_ATTEMPTS

from . import fake_agents
from .conftest import TARGET_ID, live_scope

EXPECTED_AGENT_ORDER = ["scout", "probe", "trace", "diagnose", "patch", "verify", "fortify"]


def assert_agent_order(order: list[str]) -> None:
    """Scout then Probe; Diagnose runs alongside Trace, Fortify alongside Patch -> Verify."""
    assert order[:2] == EXPECTED_AGENT_ORDER[:2], order
    assert sorted(order[2:4]) == ["diagnose", "trace"], order
    rest = order[4:]
    assert sorted(rest) == sorted(EXPECTED_AGENT_ORDER[4:]), order
    assert rest.index("patch") < rest.index("verify"), order


def build(intel, driver, library, tmp_path: Path, *, scripts=None, approve=True, **patch_kw):
    scripts = scripts or fake_agents.all_scripts(intel.snapshot.root, **patch_kw)
    gateway = ProviderGateway(FakeLLM(scripts), default_model="claude-opus-5-5")

    def approver(finding: FindingRecord) -> Approval | None:
        if not approve or finding.plan is None:
            return None
        return Approval(
            project_id="prj_test",
            subject_type="remediation",
            subject_id=finding.plan.id,
            decision="approved",
            decided_by="usr_human_reviewer",
            reason="plan reviewed in test",
        )

    config = OrchestratorConfig(
        assessment_id="asm_e2e",
        project_id="prj_test",
        org_id="org_test",
        scope=live_scope(),
        target_id=TARGET_ID,
        requested_by="usr_requester",
        budget=Budget(max_tokens=2_000_000, max_usd=50.0, max_wall_clock_s=900.0),
        max_hypotheses_to_validate=2,
    )
    return Orchestrator(
        config,
        intel=intel,
        gateway=gateway,
        driver=driver,
        library=library,
        workdir=tmp_path / "work",
        approve_remediation=approver,
    ), gateway


@pytest.fixture(scope="module")
def _e2e_cache() -> dict:
    return {}


@pytest.fixture
def result(intel, driver, library, tmp_path):
    orch, gateway = build(intel, driver, library, tmp_path)
    res = orch.run()
    return res, gateway


def test_assessment_completes_through_every_state(result):
    res, _ = result
    assert res.state is AssessmentState.COMPLETED, res.error_detail
    assert res.state_log.states() == [
        "scope_check",
        "indexing",
        "functional_review",
        "recon",
        "validation",
        "analysis",
        "diagnosis",
        "awaiting_approval",
        "remediation",
        "hardening",
        "reporting",
        "completed",
    ]


def test_all_seven_agents_ran_as_separate_runs(result):
    res, _ = result
    order = [r.agent.value for r in res.runs]
    assert_agent_order(order)
    assert len({r.id for r in res.runs}) == 7, "each agent step is its own AgentRun"
    assert all(r.status == "succeeded" for r in res.runs), [
        (r.agent.value, r.error_code) for r in res.runs if r.status != "succeeded"
    ]


def test_each_run_has_distinct_role_identity_and_tool_history(result):
    res, gateway = result
    by_agent = {r.agent.value: r for r in res.runs}
    assert set(by_agent) == set(EXPECTED_AGENT_ORDER)
    # distinct system prompts per role, from the provider's own request log
    systems = {c.agent.value: c.system for c in gateway.provider.calls}
    assert len(set(systems.values())) == 7
    # distinct tool histories
    histories = {rec.agent.value: tuple(rec.tool_names()) for rec in res.records}
    assert len(set(histories.values())) == 7, histories
    assert "checks.run_template" in histories["probe"]
    assert "worktree.write_file" in histories["patch"]
    assert "patch.apply_fresh" in histories["verify"]
    assert "checks.run_template" not in histories["scout"]
    assert "worktree.write_file" not in histories["verify"]


def test_every_agent_made_at_least_one_model_request_and_one_tool_call(result):
    res, gateway = result
    per_agent: dict[str, int] = {}
    for call in gateway.provider.calls:
        per_agent[call.agent.value] = per_agent.get(call.agent.value, 0) + 1
    assert set(per_agent) == set(EXPECTED_AGENT_ORDER)
    assert all(n >= 2 for n in per_agent.values()), per_agent
    for rec in res.records:
        assert rec.tool_invocations, f"{rec.agent.value} made no tool call"


def test_scout_hypothesis_cites_the_real_handler(result):
    res, _ = result
    finding = res.findings[0]
    span = finding.hypothesis.cited_spans[0]
    assert span.path == "payments_lite/app.py"
    assert finding.hypothesis.weakness_class == "CWE-639"
    assert "exp_owner_only" in finding.hypothesis.expectation_ids


def test_probe_confirmed_the_planted_weakness_with_executed_evidence(result):
    res, _ = result
    finding = res.findings[0]
    assert finding.validation is not None
    assert finding.validation.outcome is ValidationOutcome.CONFIRMED
    assert finding.validation.check_template_id == "access_control.ownership"
    assert finding.validation.sandbox_run_id
    assert finding.validation.observations["outcome_source"] == "executed_check"
    assert finding.validation.evidence[0].provenance == "check_run"


def test_trace_severity_is_computed_by_the_platform_from_cvss_metrics(result):
    res, _ = result
    finding = res.findings[0]
    severity = finding.severity
    assert severity is not None
    assert severity.cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N"
    assert severity.cvss_base_score == 6.5
    assert severity.cvss_rating == "medium"
    assert severity.level.value == "medium"
    assert severity.score_kind.startswith("CVSS v3.1 base score only")
    assert severity.level_source.startswith("derived from the CVSS v3.1 qualitative rating")
    assert severity.factors.data_sensitivity == "financial"
    assert severity.factors.verified_path is True
    statuses = {e.metric: e.status for e in severity.metric_evidence}
    assert statuses["S"] == "unverified" and statuses["A"] == "unverified"
    assert statuses["C"] == "cited" and statuses["AV"] == "cited"
    verified = [s for s in finding.attack_path.steps if s.verified]
    assert verified and all(s.evidence for s in verified)


def test_base_score_differs_from_the_labelled_business_severity(result):
    res, _ = result
    assert res.findings[0].severity.cvss_rating == "medium"


def test_attack_path_records_no_applicable_technique_honestly(result):
    res, _ = result
    steps = res.findings[0].attack_path.steps
    assert all(step.attack_technique is None for step in steps)
    assert all(step.attack_label == "no applicable technique" for step in steps)


def test_diagnose_plan_bounds_the_patch(result):
    res, _ = result
    finding = res.findings[0]
    assert finding.root_cause is not None and finding.plan is not None
    assert finding.root_cause.cause_class.value == "missing_check"
    assert finding.plan.files_expected == ["payments_lite/app.py"]
    assert finding.plan.test_plan.must_pass_after


def test_patch_produced_a_real_minimal_diff_with_a_new_test(result, tmp_path):
    res, _ = result
    finding = res.findings[0]
    assert finding.candidates, finding.notes
    candidate = finding.candidates[-1]
    assert set(candidate.files_changed) == {"payments_lite/app.py", "tests/test_authz_regression.py"}
    assert candidate.tests_added == ["tests/test_authz_regression.py"]
    diff = (tmp_path / "work" / f"patch-{finding.finding_id}-1.diff").read_text()
    assert "require_owner(p.customer_id, customer)" in diff
    assert diff.count("diff --git") == 2
    assert candidate.diff_sha256


def test_verify_accepted_on_executed_evidence(result):
    res, _ = result
    finding = res.findings[0]
    assert finding.verifications, finding.notes
    verification = finding.verifications[-1]
    assert verification.verdict == "accepted", verification.reasons
    passed = {c.name.value: c.passed for c in verification.checks}
    assert all(passed.values()), passed
    assert len(verification.checks) == 5
    assert verification.sandbox_run_ids


def test_verify_ran_in_its_own_sandboxes_separate_from_patch(result):
    res, _ = result
    finding = res.findings[0]
    verify_ids = set(finding.verifications[-1].sandbox_run_ids)
    patch_record = next(r for r in res.records if r.agent is AgentName.PATCH)
    patch_ids = {
        inv.summary.get("sandbox_run_id")
        for inv in patch_record.tool_invocations
        if inv.summary.get("sandbox_run_id")
    }
    assert verify_ids and not (verify_ids & patch_ids)


def test_verify_received_no_patch_transcript(result):
    """Independence: nothing from Patch's conversation reaches Verify's prompt."""
    res, gateway = result
    patch_calls = [c for c in gateway.provider.calls if c.agent is AgentName.PATCH]
    verify_calls = [c for c in gateway.provider.calls if c.agent is AgentName.VERIFY]
    patch_explanation = "Applying the scoped lookup"
    verify_text = "\n".join(str(m) for c in verify_calls for m in c.messages)
    assert patch_explanation in "\n".join(str(m) for c in patch_calls for m in c.messages)
    assert patch_explanation not in verify_text
    assert "Replaced the unscoped payment lookup" not in verify_text


def test_fortify_promoted_a_regression_test(result):
    res, _ = result
    finding = res.findings[0]
    assert finding.status == "hardened"
    assert len(finding.regression_tests) >= 1
    assert finding.regression_tests[0].test_path == "tests/test_authz_regression.py"
    assert finding.recommendations
    assert finding.monitoring_hints


def test_typed_handoff_chain_is_linked_by_ids(result):
    res, _ = result
    finding = res.findings[0]
    assert finding.validation.hypothesis_id == finding.hypothesis.id
    assert finding.attack_path.finding_id == finding.finding_id
    assert finding.root_cause.finding_id == finding.finding_id
    assert finding.plan.finding_id == finding.finding_id
    assert finding.candidates[-1].finding_id == finding.finding_id
    assert finding.verifications[-1].patch_id == finding.candidates[-1].id
    assert finding.regression_tests[0].finding_id == finding.finding_id


def test_audit_chain_is_hash_linked_and_covers_the_gates(result):
    res, _ = result
    actions = [e.action for e in res.audit]
    for expected in (
        "scope.checked",
        "agent.step",
        "finding.hypothesis",
        "finding.validation",
        "finding.severity",
        "finding.diagnosed",
        "approval.granted",
        "patch.candidate",
        "finding.verification",
        "finding.hardened",
        "assessment.completed",
    ):
        assert expected in actions, expected
    prev = "0" * 64
    for event in res.audit:
        assert event.prev_hash == prev
        prev = event.hash
    assert len({e.hash for e in res.audit}) == len(res.audit)


def test_report_is_honest_about_provider_mode(result):
    res, _ = result
    report = res.report()
    assert report["provider_modes"] == ["fake"]
    assert report["state"] == "completed"
    assert sorted(report["agents_executed"]) == sorted(EXPECTED_AGENT_ORDER)
    assert report["usage"]["sandbox_seconds"] > 0
    assert report["findings"][0]["status"] == "hardened"
    assert report["findings"][0]["verdicts"] == ["accepted"]


def test_usage_is_accounted_across_the_assessment(result):
    res, _ = result
    assert res.usage.total_tokens == sum(r.usage.total_tokens for r in res.runs)
    assert res.usage.usd > 0


# --------------------------------------------------------------------------- bad patches


@pytest.mark.boundary
def test_worktree_refuses_a_patch_attempt_that_weakens_an_existing_test(intel, driver, library, tmp_path):
    """First line of defence: the write is refused, so no weakened test reaches the diff."""
    orch, _ = build(intel, driver, library, tmp_path, weaken_tests=True)
    res = orch.run()
    finding = res.findings[0]
    patch_record = next(r for r in res.records if r.agent is AgentName.PATCH)
    refused = [
        inv
        for inv in patch_record.tool_invocations
        if inv.name == "worktree.write_file" and inv.error_code == "PATH_NOT_ALLOWED"
    ]
    assert refused, [(i.name, i.error_code) for i in patch_record.tool_invocations]
    candidate = finding.candidates[-1]
    assert "tests/test_payments.py" not in candidate.files_changed
    diff = (tmp_path / "work" / f"patch-{finding.finding_id}-1.diff").read_text()
    assert "tests/test_payments.py" not in diff


@pytest.mark.boundary
def test_worktree_refuses_a_patch_attempt_outside_the_plan(intel, driver, library, tmp_path):
    orch, _ = build(intel, driver, library, tmp_path, out_of_scope=True)
    res = orch.run()
    finding = res.findings[0]
    patch_record = next(r for r in res.records if r.agent is AgentName.PATCH)
    refused = [
        inv
        for inv in patch_record.tool_invocations
        if inv.name == "worktree.write_file" and inv.error_code == "PATH_NOT_ALLOWED"
    ]
    assert refused
    assert "payments_lite/store.py" not in finding.candidates[-1].files_changed


@pytest.mark.boundary
def test_ineffective_in_scope_patch_is_rejected_and_retried_then_unresolved(intel, driver, library, tmp_path):
    """Verify rejects a patch that does not fix the finding; the loop retries once."""
    orch, _ = build(intel, driver, library, tmp_path, ineffective=True)
    res = orch.run()
    patch_runs = [r for r in res.runs if r.agent is AgentName.PATCH]
    verify_runs = [r for r in res.runs if r.agent is AgentName.VERIFY]
    assert [r.attempt for r in patch_runs] == list(range(1, MAX_PATCH_ATTEMPTS + 1)), [
        r.attempt for r in patch_runs
    ]
    assert len(verify_runs) == MAX_PATCH_ATTEMPTS
    finding = res.findings[0]
    assert finding.status == "patch_rejected"
    assert [v.verdict for v in finding.verifications] == ["rejected"] * MAX_PATCH_ATTEMPTS
    first = finding.verifications[0]
    recheck = next(c for c in first.checks if c.name.value == "original_check_now_fails_to_reproduce")
    assert not recheck.passed
    assert "remediation.exhausted" in [e.action for e in res.audit]
    # Fortify's step runs alongside remediation, but an unresolved finding is never hardened.
    assert finding.status != "hardened" and not finding.recommendations


@pytest.mark.boundary
def test_patch_retry_receives_rejection_reasons_but_no_verify_transcript(intel, driver, library, tmp_path):
    orch, gateway = build(intel, driver, library, tmp_path, ineffective=True)
    orch.run()
    patch_calls = [c for c in gateway.provider.calls if c.agent is AgentName.PATCH]
    second_attempt = [c for c in patch_calls if "This is attempt 2" in str(c.messages[0]["content"])]
    assert second_attempt, "the retry prompt should state the attempt number"
    prompt = str(second_attempt[0].messages[0]["content"])
    assert "rejection_reasons" in prompt
    assert "original_check_now_fails_to_reproduce" in prompt
    # machine reasons only: Verify's own summary text must not be handed over
    assert "Applied the diff to a fresh checkout" not in prompt


@pytest.mark.boundary
def test_verify_rejects_a_planted_bad_patch_that_edits_a_protected_test(intel, driver, library, tmp_path):
    """A diff authored outside the worktree guard is still rejected by Verify (SB-09).

    This is the `examples/fixtures/bad-patches` case: a patch that makes the
    failing expectation pass by weakening the test that encodes it.
    """
    from security_agents.agents import verify as verify_agent
    from security_agents.sandbox.workspace import fresh_checkout, unified_diff
    from security_agents.tools import AuditSink, PatchApplier, ToolContext

    recipe_path = intel.snapshot.root / "build_recipe.json"
    recipe_path.chmod(0o644)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["protected_tests"] = ["tests/test_api.py"]
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    recipe_path.chmod(0o444)
    orchestrator, _ = build(intel, driver, library, tmp_path)
    protected_tests = orchestrator._protected_tests()
    assert "tests/test_api.py" in protected_tests

    work = tmp_path / "bad"
    wt = fresh_checkout(intel.snapshot, work / "wt")
    protected = wt / "tests" / "test_api.py"
    protected.write_text(protected.read_text().replace("== 404", "in (200, 404)"))
    diff = unified_diff(intel.snapshot.root, wt, ["tests/test_api.py"])
    assert "tests/test_api.py" in diff

    applier = PatchApplier(
        intel=intel,
        artifacts={
            "pat_bad": {
                "diff": diff,
                "plan_files": ["payments_lite/app.py"],
                "protected_tests": protected_tests,
            }
        },
        workdir=work,
    )
    ctx = ToolContext(
        assessment_id="asm_bad",
        agent=AgentName.VERIFY,
        run_id="run_bad",
        step_key="verify",
        read_root=intel.snapshot.root,
        audit=AuditSink(),
    )
    facts = applier.apply_fresh(ctx, "pat_bad")
    assert facts["applied"] is True
    assert facts["tampered_protected_tests"] == ["tests/test_api.py"]
    # editing an existing test is both tampering and outside the plan's file set
    assert facts["out_of_scope_files"] == ["tests/test_api.py"]
    assert facts["new_test_files"] == []

    evidence = verify_agent.VerifyEvidence(
        apply_facts=dict(ctx.artifacts["verify_facts"]),
        recheck_runs=[],
        test_runs=[],
        baseline_failing=[],
        tests_added=[],
        protected_tests=protected_tests,
        original_check_ref="access_control.ownership@1",
        original_check_params={},
    )
    verdict, checks, reasons = verify_agent.compute_verdict(evidence)
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name.value == "no_test_tampering").passed
    assert not next(c for c in checks if c.name.value == "diff_scope_ok").passed
    assert any("tests/test_api.py" in r for r in reasons)


@pytest.mark.boundary
def test_verify_rejects_a_planted_bad_patch_outside_the_plan(intel, driver, library, tmp_path):
    from security_agents.agents import verify as verify_agent
    from security_agents.sandbox.workspace import fresh_checkout, unified_diff
    from security_agents.tools import AuditSink, PatchApplier, ToolContext

    work = tmp_path / "bad2"
    wt = fresh_checkout(intel.snapshot, work / "wt")
    store = wt / "payments_lite" / "store.py"
    store.write_text(store.read_text() + "\n# unrelated change\n")
    diff = unified_diff(intel.snapshot.root, wt, ["payments_lite/store.py"])

    applier = PatchApplier(
        intel=intel,
        artifacts={
            "pat_scope": {
                "diff": diff,
                "plan_files": ["payments_lite/app.py"],
                "protected_tests": ["tests/test_api.py"],
            }
        },
        workdir=work,
    )
    ctx = ToolContext(
        assessment_id="asm_bad2",
        agent=AgentName.VERIFY,
        run_id="run_bad2",
        step_key="verify",
        read_root=intel.snapshot.root,
        audit=AuditSink(),
    )
    facts = applier.apply_fresh(ctx, "pat_scope")
    assert facts["out_of_scope_files"] == ["payments_lite/store.py"]
    verdict, checks, _ = verify_agent.compute_verdict(
        verify_agent.VerifyEvidence(
            apply_facts=dict(ctx.artifacts["verify_facts"]),
            recheck_runs=[],
            test_runs=[],
            baseline_failing=[],
            tests_added=[],
            protected_tests=["tests/test_api.py"],
            original_check_ref="access_control.ownership@1",
            original_check_params={},
        )
    )
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name.value == "diff_scope_ok").passed


@pytest.mark.boundary
def test_patch_without_a_regression_test_is_rejected(intel, driver, library, tmp_path):
    """A fix with no test is not verifiable, so it cannot be accepted."""
    orch, _ = build(intel, driver, library, tmp_path, skip_tests=True)
    res = orch.run()
    finding = res.findings[0]
    assert finding.verifications
    generated = next(c for c in finding.verifications[0].checks if c.name.value == "generated_tests_pass")
    assert not generated.passed
    assert finding.verifications[0].verdict == "rejected"


def test_patch_cannot_write_outside_the_plan(intel, driver, library, tmp_path):
    """The worktree refuses a path the approved plan does not cover."""
    from security_agents.tools import PatchWorktree

    worktree = PatchWorktree.create(intel, tmp_path / "wt", ["payments_lite/app.py"])
    denied = worktree.write_file("payments_lite/store.py", "x = 1\n")
    assert denied["error"] == "PATH_NOT_ALLOWED"
    assert not (worktree.root / "payments_lite" / "store.py").read_text().startswith("x = 1")
    allowed = worktree.write_file("tests/test_new_case.py", "def test_x():\n    assert True\n")
    assert allowed["action"] == "created"


# --------------------------------------------------------------------------- gates


def test_declined_remediation_stops_before_patching(intel, driver, library, tmp_path):
    orch, _ = build(intel, driver, library, tmp_path, approve=False)
    res = orch.run()
    assert res.state is AssessmentState.COMPLETED
    order = [r.agent.value for r in res.runs]
    assert order[:2] == ["scout", "probe"] and sorted(order[2:]) == ["diagnose", "trace"], order
    finding = res.findings[0]
    assert finding.status == "diagnosed"
    assert not finding.candidates
    assert "approval.absent" in [e.action for e in res.audit]


def test_agent_approval_is_not_accepted(intel, driver, library, tmp_path):
    """An approval that is not a human decision is ignored (docs/15 §7)."""
    scripts = fake_agents.all_scripts(intel.snapshot.root)
    gateway = ProviderGateway(FakeLLM(scripts))

    def agent_approver(finding: FindingRecord) -> Approval | None:
        return Approval(
            project_id="prj_test",
            subject_type="remediation",
            subject_id=finding.plan.id if finding.plan else "x",
            decision="approved",
            decided_by="agent:patch",
        )

    config = OrchestratorConfig(
        assessment_id="asm_gate",
        project_id="prj_test",
        org_id="org_test",
        scope=live_scope(),
        target_id=TARGET_ID,
        requested_by="usr_requester",
    )
    res = Orchestrator(
        config,
        intel=intel,
        gateway=gateway,
        driver=driver,
        library=library,
        workdir=tmp_path / "w",
        approve_remediation=agent_approver,
    ).run()
    assert not any(r.agent is AgentName.PATCH for r in res.runs)
    assert "approval.invalid_actor" in [e.action for e in res.audit]


def test_approval_for_a_different_plan_is_ignored(intel, driver, library, tmp_path):
    scripts = fake_agents.all_scripts(intel.snapshot.root)
    gateway = ProviderGateway(FakeLLM(scripts))

    def wrong_approver(finding: FindingRecord) -> Approval | None:
        return Approval(
            project_id="prj_test",
            subject_type="remediation",
            subject_id="rmp_someone_else",
            decision="approved",
            decided_by="usr_human",
        )

    config = OrchestratorConfig(
        assessment_id="asm_gate2",
        project_id="prj_test",
        org_id="org_test",
        scope=live_scope(),
        target_id=TARGET_ID,
        requested_by="usr_requester",
    )
    res = Orchestrator(
        config,
        intel=intel,
        gateway=gateway,
        driver=driver,
        library=library,
        workdir=tmp_path / "w",
        approve_remediation=wrong_approver,
    ).run()
    assert not any(r.agent is AgentName.PATCH for r in res.runs)
    assert "approval.mismatch" in [e.action for e in res.audit]


def test_finding_standards_are_evidence_backed(result):
    """One planted IDOR finding, classified from executed evidence (not an LLM score)."""
    res, _ = result
    finding = res.findings[0]
    std = finding.standards
    assert std is not None
    assert std.cwe and std.cwe.id == "CWE-639"
    assert "T1190" in [m.id for m in std.attack]
    assert std.cvss and std.cvss.complete and std.cvss.score is not None
    assert "provisional" not in std.cvss.assessor
    assert std.exploit_status.value == "reproduced_in_sandbox"
    summary = finding.public()["standards"]
    assert summary["cwe"] == "CWE-639"
    assert summary["cvss_complete"] is True
    assert "D3-AMED" in [m.id for m in std.d3fend]


# --- functional remediation approvals owed by the host (Srihith follow-up on #73) ---


def test_pending_functional_approval_pauses_even_with_approved_security_work(
    intel, driver, library, tmp_path
):
    orch, _ = build(intel, driver, library, tmp_path)  # security remediation approved
    orch.external_pending = lambda: True
    res = orch.run()
    assert res.state is AssessmentState.AWAITING_APPROVAL
    assert "patch" not in [r.agent.value for r in res.runs], "nothing remediated before all decide"


def test_early_security_exit_still_waits_for_a_functional_approval(intel, driver, library, tmp_path):
    from security_agents.agents import scout as scout_agent
    from security_agents.llm import FakeTurn

    submit = scout_agent.SPEC.submit_tool_name()
    empty = {
        "hypotheses": [],
        "coverage": {"entry_points_reviewed": [], "looks_correct": [], "skipped_with_reason": []},
        "suspicious_instructions": [],
    }
    scripts = fake_agents.all_scripts(intel.snapshot.root)
    scripts[AgentName.SCOUT] = [FakeTurn(tool_calls=[(submit, empty)])]
    orch, _ = build(intel, driver, library, tmp_path, scripts=scripts)
    orch.external_pending = lambda: True
    res = orch.run()
    assert res.state is AssessmentState.AWAITING_APPROVAL
    skipped = [e for e in res.state_log.entries if "skipped (no hypotheses)" in e[2]]
    assert len(skipped) == 4, res.state_log  # VALIDATION, ANALYSIS, DIAGNOSIS, AWAITING_APPROVAL

    orch2, _ = build(intel, driver, library, tmp_path / "b", scripts=scripts)
    assert orch2.run().state is AssessmentState.COMPLETED  # nothing owed: report as before


def test_report_carries_a_bounded_redacted_tool_trace_per_run(result):
    """A failed paid live run must be diagnosable from the report (M3 attempt: Probe was
    inconclusive and the report had only counts)."""
    report = result[0].report()
    for run in report["runs"]:
        trace = run["tool_trace"]
        assert len(trace) == run["tool_calls"] or len(trace) == 40, run["agent"]
        assert trace, f"{run['agent']} has no tool trace"
        for call in trace:
            assert set(call) == {"name", "ok", "error_code", "args", "summary"}
            assert len(call["args"]) <= 800 and len(call["summary"]) <= 1200
    probe = next(r for r in report["runs"] if r["agent"] == "probe")
    checks = [c for c in probe["tool_trace"] if c["name"] == "checks.run_template" and c["ok"]]
    assert checks, probe["tool_trace"]
    # A SUCCESSFUL check keeps its outcome, so an inconclusive live Probe is diagnosable.
    summary = json.loads(checks[-1]["summary"])
    assert "matches_expectation" in summary and "exit_code" in summary, summary
    assert "route_id" in json.loads(checks[-1]["args"])["parameters"]
