"""Deterministic post-processing per agent: what the platform enforces over model text."""

from __future__ import annotations

import pytest

from security_agents.agents import diagnose as diagnose_agent
from security_agents.agents import fortify as fortify_agent
from security_agents.agents import patch as patch_agent
from security_agents.agents import probe as probe_agent
from security_agents.agents import scout as scout_agent
from security_agents.agents import trace as trace_agent
from security_agents.agents import verify as verify_agent
from security_agents.contracts import (
    AgentName,
    Confidence,
    SeverityLevel,
    ValidationOutcome,
    VerificationCheckName,
)
from security_agents.runtime import StepRecord

# --------------------------------------------------------------------------- scout


def scout_input(**kw):
    base = dict(
        assessment_id="asm",
        snapshot_id="snp_test",
        commit_sha="sha",
        available_check_categories=["access_control.ownership"],
        max_hypotheses=5,
    )
    base.update(kw)
    return scout_agent.ScoutInput(**base)


def hypothesis_out(**kw):
    base = dict(
        title="Payment detail may not enforce ownership",
        weakness_class="CWE-639",
        rationale="sibling comparison",
        expected_invariant="only the owner can read a payment",
        cited_paths=["payments_lite/app.py"],
        cited_start_lines=[137],
        cited_end_lines=[150],
        expectation_ids=["exp_owner_only"],
        confidence="high",
        suggested_check_category="access_control.ownership",
    )
    base.update(kw)
    return scout_agent.HypothesisOut(**base)


def scout_output(items):
    return scout_agent.ScoutOutput(
        hypotheses=items, coverage=scout_agent.CoverageOut(), suspicious_instructions=[]
    )


def test_scout_keeps_valid_citations(intel):
    out = scout_output([hypothesis_out()])
    result = scout_agent.to_hypotheses(out, scout_input(), intel)
    assert len(result) == 1
    span = result[0].cited_spans[0]
    assert span.path == "payments_lite/app.py" and span.sha == intel.snapshot.commit_sha
    assert span.span.start_line == 137


def test_scout_drops_hypothesis_citing_a_file_not_in_snapshot(intel):
    out = scout_output([hypothesis_out(cited_paths=["payments_lite/nope.py"])])
    assert scout_agent.to_hypotheses(out, scout_input(), intel) == []


def test_scout_drops_citation_past_end_of_file(intel):
    out = scout_output([hypothesis_out(cited_start_lines=[99999], cited_end_lines=[100000])])
    assert scout_agent.to_hypotheses(out, scout_input(), intel) == []


def test_scout_clamps_end_line_to_file_length(intel):
    out = scout_output([hypothesis_out(cited_start_lines=[1], cited_end_lines=[99999])])
    result = scout_agent.to_hypotheses(out, scout_input(), intel)
    limit = intel.snapshot.line_count("payments_lite/app.py")
    assert result[0].cited_spans[0].span.end_line == limit


def test_scout_drops_unavailable_check_category(intel):
    out = scout_output([hypothesis_out(suggested_check_category="made.up.category")])
    assert scout_agent.to_hypotheses(out, scout_input(), intel) == []


def test_scout_skips_previously_refuted_key(intel):
    from security_agents.intel import hypothesis_key

    key = hypothesis_key("CWE-639", "payments_lite/app.py", None)
    out = scout_output([hypothesis_out()])
    assert scout_agent.to_hypotheses(out, scout_input(refuted_keys=[key]), intel) == []


def test_scout_respects_max_hypotheses(intel):
    out = scout_output([hypothesis_out(title=f"h{i}") for i in range(5)])
    result = scout_agent.to_hypotheses(out, scout_input(), intel, max_items=2)
    assert len(result) == 2


def test_scout_ranking_prefers_high_confidence_and_critical_expectation(intel):
    out = scout_output(
        [
            hypothesis_out(title="low", confidence="low"),
            hypothesis_out(title="high", confidence="high"),
            hypothesis_out(title="medium", confidence="medium"),
        ]
    )
    hyps = scout_agent.to_hypotheses(out, scout_input(), intel)
    ranked = scout_agent.rank(hyps, intel)
    assert [h.title for h in ranked] == ["high", "medium", "low"]


# --------------------------------------------------------------------------- probe


def probe_input():
    return probe_agent.ProbeInput(
        assessment_id="asm",
        hypothesis_id="hyp_1",
        scope_id="scp_test",
        target_id="tgt_payments_lite",
        hypothesis_title="t",
        weakness_class="CWE-639",
        expected_invariant="inv",
        suggested_check_category="access_control.ownership",
    )


def probe_out(outcome: str, **kw):
    return probe_agent.ProbeOutput(outcome=outcome, **kw)


def check_run(matches, **kw):
    base = {
        "sandbox_run_id": "sbx_1",
        "template_ref": "access_control.ownership@1",
        "template_id": "access_control.ownership",
        "template_version": "1",
        "matches_expectation": matches,
        "parameters": {"route_id": "payment_detail"},
        "passed": 1,
        "failed": 1,
    }
    base.update(kw)
    return base


def record():
    return StepRecord(
        run_id="run_1", agent=AgentName.PROBE, spec_version="0.1.0", model="m", provider_mode="fake"
    )


def test_probe_confirms_when_expectation_did_not_hold():
    r = probe_agent.to_validation_result(probe_out("confirmed"), probe_input(), record(), [check_run(False)])
    assert r.outcome is ValidationOutcome.CONFIRMED
    assert r.sandbox_run_id == "sbx_1"
    assert r.evidence[0].provenance == "check_run"


def test_probe_refutes_when_expectation_held():
    r = probe_agent.to_validation_result(probe_out("refuted"), probe_input(), record(), [check_run(True)])
    assert r.outcome is ValidationOutcome.REFUTED


def test_probe_inconclusive_when_check_result_is_null():
    r = probe_agent.to_validation_result(probe_out("confirmed"), probe_input(), record(), [check_run(None)])
    assert r.outcome is ValidationOutcome.INCONCLUSIVE


def test_executed_evidence_overrides_a_disagreeing_agent_claim():
    """A model claiming 'confirmed' cannot override a check that says the expectation held."""
    r = probe_agent.to_validation_result(probe_out("confirmed"), probe_input(), record(), [check_run(True)])
    assert r.outcome is ValidationOutcome.REFUTED
    assert "disagreement" in r.observations
    assert r.observations["agent_claimed_outcome"] == "confirmed"


def test_probe_without_any_successful_run_is_inconclusive():
    r = probe_agent.to_validation_result(
        probe_out("confirmed", missing_capability="no template"),
        probe_input(),
        record(),
        [{"error": "TEMPLATE_NOT_RUNNABLE", "detail": "x"}],
    )
    assert r.outcome is ValidationOutcome.INCONCLUSIVE
    assert r.observations["outcome_source"] == "no_successful_check_run"
    assert r.sandbox_run_id is None


def test_probe_interpretation_is_length_capped_by_the_schema():
    """The non-authoritative explanation cannot exceed 500 chars (docs/agents/RED_PROBE)."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        probe_out("confirmed", interpretation="x" * 501)
    r = probe_agent.to_validation_result(
        probe_out("confirmed", interpretation="x" * 500), probe_input(), record(), [check_run(False)]
    )
    assert len(r.interpretation) == 500


# --------------------------------------------------------------------------- trace


def trace_input():
    return trace_agent.TraceInput(
        assessment_id="asm",
        finding_id="fnd_1",
        validation_result_id="val_1",
        sandbox_run_id="sbx_1",
        hypothesis_title="t",
        weakness_class="CWE-639",
        confirmed_outcome="confirmed",
        affected_paths=["payments_lite/app.py"],
        affected_symbol="get_payment",
        expectation_ids=["exp_owner_only"],
    )


def metrics(vector="AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", artifact="sbx_1"):
    out = []
    for part in vector.split("/"):
        name, value = part.split(":")
        out.append(
            {
                "metric": name,
                "value": value,
                "rationale": f"{name} rationale",
                "evidence_artifact_id": artifact,
            }
        )
    return out


def trace_output(**kw):
    base = dict(
        steps=[
            trace_agent.PathStepOut(
                description="authenticated customer requests another customer's payment",
                symbol_ref="get_payment",
                verified=True,
                evidence_artifact_id="sbx_1",
            )
        ],
        impact_summary="another customer's payment record is readable",
        cvss_base_metrics=metrics(),
        severity_rationale="network reachable, low privileges, confidentiality high",
    )
    base.update(kw)
    return trace_agent.TraceOutput(**base)


def confirmed_validation():
    return probe_agent.to_validation_result(
        probe_out("confirmed"), probe_input(), record(), [check_run(False)]
    )


def test_trace_computes_cvss_vector_score_and_level(intel):
    path, severity = trace_agent.to_attack_path(
        trace_output(), trace_input(), intel, validation=confirmed_validation()
    )
    assert severity.cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N"
    assert severity.cvss_base_score == 6.5
    assert severity.cvss_rating == "medium"
    assert severity.level is SeverityLevel.MEDIUM
    assert severity.score_kind.startswith("CVSS v3.1 base score only")


@pytest.mark.parametrize(
    "vector,score,level",
    [
        ("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8, SeverityLevel.CRITICAL),
        ("AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0, SeverityLevel.CRITICAL),
        ("AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 8.8, SeverityLevel.HIGH),
        ("AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 5.5, SeverityLevel.MEDIUM),
        ("AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0, SeverityLevel.INFO),
    ],
)
def test_trace_level_follows_the_computed_rating(intel, vector, score, level):
    _, severity = trace_agent.to_attack_path(
        trace_output(cvss_base_metrics=metrics(vector)),
        trace_input(),
        intel,
        validation=confirmed_validation(),
    )
    assert severity.cvss_base_score == score
    assert severity.level is level


def test_trace_output_has_no_field_for_a_score_or_level():
    fields = set(trace_agent.TraceOutput.model_fields)
    for forbidden in ("score", "base_score", "level", "severity", "rating", "cvss_vector"):
        assert forbidden not in fields, forbidden
    proposal_fields = set(trace_agent.CvssMetricProposal.model_fields)
    assert not any(token in f for f in proposal_fields for token in ("score", "level", "rating"))


def test_trace_rejects_missing_or_duplicated_metrics():
    import pydantic

    incomplete = metrics()[:7]
    with pytest.raises(pydantic.ValidationError, match="exactly once"):
        trace_output(cvss_base_metrics=[*incomplete, incomplete[0]])


def test_trace_rejects_an_invalid_metric_value():
    import pydantic

    bad = metrics()
    bad[0] = {**bad[0], "value": "X"}
    with pytest.raises(pydantic.ValidationError, match="AV must be one of"):
        trace_output(cvss_base_metrics=bad)


def test_metric_without_evidence_is_labelled_unverified(intel):
    plain = [{**m, "evidence_artifact_id": None} for m in metrics()]
    _, severity = trace_agent.to_attack_path(
        trace_output(cvss_base_metrics=plain), trace_input(), intel, validation=confirmed_validation()
    )
    assert {e.status for e in severity.metric_evidence} == {"unverified"}


def test_metric_citing_a_foreign_run_is_unverified(intel):
    foreign = metrics(artifact="sbx_someone_else")
    _, severity = trace_agent.to_attack_path(
        trace_output(cvss_base_metrics=foreign), trace_input(), intel, validation=confirmed_validation()
    )
    assert {e.status for e in severity.metric_evidence} == {"unverified"}


def test_factors_are_derived_from_metrics_and_expectations(intel):
    _, severity = trace_agent.to_attack_path(
        trace_output(), trace_input(), intel, validation=confirmed_validation()
    )
    assert severity.factors.reachability == "authenticated"
    assert severity.factors.privileges_required == "low"
    assert severity.factors.data_sensitivity == "financial"
    assert severity.factors.verified_path is True


def test_trace_marks_step_verified_only_with_executed_evidence(intel):
    path, severity = trace_agent.to_attack_path(
        trace_output(), trace_input(), intel, validation=confirmed_validation()
    )
    assert path.steps[0].verified is True
    assert severity.factors.verified_path is True


def test_trace_downgrades_unsupported_verified_claim(intel):
    out = trace_output(
        steps=[
            trace_agent.PathStepOut(
                description="reaches the ledger", verified=True, evidence_artifact_id=None
            )
        ]
    )
    path, severity = trace_agent.to_attack_path(out, trace_input(), intel, validation=None)
    assert path.steps[0].verified is False
    assert severity.factors.verified_path is False


def test_trace_ignores_foreign_artifact_id(intel):
    out = trace_output(
        steps=[
            trace_agent.PathStepOut(description="x", verified=True, evidence_artifact_id="sbx_someone_else")
        ]
    )
    path, _ = trace_agent.to_attack_path(out, trace_input(), intel, validation=confirmed_validation())
    assert path.steps[0].verified is False


def test_trace_steps_are_ordered(intel):
    out = trace_output(
        steps=[
            trace_agent.PathStepOut(description="a", verified=False),
            trace_agent.PathStepOut(description="b", verified=False),
        ]
    )
    path, _ = trace_agent.to_attack_path(out, trace_input(), intel)
    assert [s.order for s in path.steps] == [1, 2]


def test_attack_technique_is_validated_and_enriched(intel):
    out = trace_output(
        steps=[trace_agent.PathStepOut(description="x", verified=False, attack_technique="T1190")]
    )
    path, _ = trace_agent.to_attack_path(out, trace_input(), intel)
    assert path.steps[0].attack_technique == "T1190"
    assert path.steps[0].attack_technique_name == "Exploit Public-Facing Application"
    assert path.steps[0].attack_version == "20.0"


def test_invented_attack_technique_is_rejected():
    import pydantic

    with pytest.raises(pydantic.ValidationError, match=r"T9999.*ATT&CK"):
        trace_agent.PathStepOut(description="x", verified=False, attack_technique="T9999")


# --------------------------------------------------------------------------- diagnose


def diagnose_input():
    return diagnose_agent.DiagnoseInput(
        assessment_id="asm",
        finding_id="fnd_1",
        weakness_class="CWE-639",
        hypothesis_title="t",
        severity_level="high",
        impact_summary="x",
        affected_paths=["payments_lite/app.py"],
        affected_symbol="get_payment",
        violated_expectation_ids=["exp_owner_only"],
    )


def diagnose_output(**kw):
    base = dict(
        root_cause_summary="handler uses the unscoped lookup",
        cause_class="missing_check",
        cause_paths=["payments_lite/app.py"],
        cause_start_lines=[137],
        cause_end_lines=[150],
        cause_symbol="get_payment",
        violated_expectation_ids=["exp_owner_only"],
        strategy="use the ownership-scoped lookup like the sibling routes",
        files_expected=["payments_lite/app.py"],
        tests_must_fail_before=["a non-owner can read the payment"],
        tests_must_pass_after=["a non-owner gets 404"],
    )
    base.update(kw)
    return diagnose_agent.DiagnoseOutput(**base)


def test_diagnose_builds_plan_with_real_paths(intel):
    rc, plan, related = diagnose_agent.to_plan(diagnose_output(), diagnose_input(), intel)
    assert rc.cause_spans[0].path == "payments_lite/app.py"
    assert plan.files_expected == ["payments_lite/app.py"]
    assert plan.test_plan.must_pass_after


def test_diagnose_filters_invented_plan_files(intel):
    _, plan, _ = diagnose_agent.to_plan(
        diagnose_output(files_expected=["payments_lite/app.py", "does/not/exist.py"]),
        diagnose_input(),
        intel,
    )
    assert plan.files_expected == ["payments_lite/app.py"]


def test_diagnose_allows_new_test_files_in_plan(intel):
    _, plan, _ = diagnose_agent.to_plan(
        diagnose_output(files_expected=["payments_lite/app.py", "tests/test_new_authz.py"]),
        diagnose_input(),
        intel,
    )
    assert "tests/test_new_authz.py" in plan.files_expected


def test_diagnose_filters_unknown_expectation_ids(intel):
    rc, _, _ = diagnose_agent.to_plan(
        diagnose_output(violated_expectation_ids=["exp_made_up"]), diagnose_input(), intel
    )
    assert rc.violated_expectation_ids == ["exp_owner_only"]


def test_diagnose_falls_back_to_affected_path_when_spans_invalid(intel):
    rc, _, _ = diagnose_agent.to_plan(diagnose_output(cause_paths=["nope.py"]), diagnose_input(), intel)
    assert rc.cause_spans[0].path == "payments_lite/app.py"


# --------------------------------------------------------------------------- verify verdict


def evidence(**kw):
    base = dict(
        apply_facts={
            "applied": True,
            "files_touched": ["payments_lite/app.py", "tests/test_authz.py"],
            "out_of_scope": [],
            "new_tests": ["tests/test_authz.py"],
            "tampered_protected_tests": [],
        },
        recheck_runs=[check_run(True)],
        test_runs=[
            {
                "selection": "project_suite",
                "passed": 9,
                "failed": 0,
                "errors": 0,
                "failing_tests": [],
                "sandbox_run_id": "sbx_t",
            },
        ],
        baseline_failing=[],
        tests_added=["tests/test_authz.py"],
        protected_tests=["tests/test_payments.py"],
        original_check_ref="access_control.ownership@1",
        original_check_params={"route_id": "payment_detail"},
    )
    base.update(kw)
    return verify_agent.VerifyEvidence(**base)


def test_verify_accepts_only_when_every_check_passes():
    verdict, checks, reasons = verify_agent.compute_verdict(evidence())
    assert verdict == "accepted"
    assert {c.name for c in checks} == set(VerificationCheckName)
    assert all(c.passed for c in checks) and not reasons


def test_verify_rejects_when_original_check_still_fails():
    verdict, checks, reasons = verify_agent.compute_verdict(evidence(recheck_runs=[check_run(False)]))
    assert verdict == "rejected"
    failed = next(c for c in checks if c.name is VerificationCheckName.ORIGINAL_CHECK_NOW_FAILS)
    assert not failed.passed and reasons


def test_verify_rejects_when_recheck_never_ran():
    verdict, checks, _ = verify_agent.compute_verdict(evidence(recheck_runs=[]))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.ORIGINAL_CHECK_NOW_FAILS).passed


def test_verify_rejects_recheck_with_changed_parameters():
    runs = [check_run(True, parameters={"route_id": "something_else"})]
    verdict, checks, _ = verify_agent.compute_verdict(evidence(recheck_runs=runs))
    assert verdict == "rejected"
    detail = next(c for c in checks if c.name is VerificationCheckName.ORIGINAL_CHECK_NOW_FAILS).detail
    assert "different parameters" in detail


def test_verify_rejects_test_tampering():
    facts = {
        "applied": True,
        "files_touched": ["payments_lite/app.py", "tests/test_payments.py"],
        "out_of_scope": [],
        "new_tests": [],
        "tampered_protected_tests": ["tests/test_payments.py"],
    }
    verdict, checks, reasons = verify_agent.compute_verdict(evidence(apply_facts=facts))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.NO_TEST_TAMPERING).passed
    assert any("tests/test_payments.py" in r for r in reasons)


def test_verify_rejects_out_of_scope_changes():
    facts = {
        "applied": True,
        "files_touched": ["payments_lite/store.py"],
        "out_of_scope": ["payments_lite/store.py"],
        "new_tests": [],
        "tampered_protected_tests": [],
    }
    verdict, checks, _ = verify_agent.compute_verdict(evidence(apply_facts=facts))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.DIFF_SCOPE_OK).passed


def test_verify_rejects_diff_that_does_not_apply():
    facts = {
        "applied": False,
        "files_touched": [],
        "out_of_scope": [],
        "new_tests": [],
        "tampered_protected_tests": [],
    }
    verdict, _, reasons = verify_agent.compute_verdict(evidence(apply_facts=facts))
    assert verdict == "rejected" and any("did not apply" in r for r in reasons)


def test_verify_rejects_new_test_failures_but_tolerates_baseline():
    runs = [
        {
            "selection": "project_suite",
            "passed": 7,
            "failed": 1,
            "failing_tests": ["tests/test_payments.py::test_x"],
            "errors": 0,
            "sandbox_run_id": "s",
        }
    ]
    rejected, _, _ = verify_agent.compute_verdict(evidence(test_runs=runs))
    assert rejected == "rejected"
    accepted, _, _ = verify_agent.compute_verdict(
        evidence(test_runs=runs, baseline_failing=["tests/test_payments.py::test_x"])
    )
    assert accepted == "accepted"


def test_verify_rejects_when_suite_not_run():
    verdict, checks, _ = verify_agent.compute_verdict(evidence(test_runs=[]))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.FUNCTIONAL_SUITE_PASSES).passed


def test_verify_rejects_patch_without_a_regression_test():
    verdict, checks, reasons = verify_agent.compute_verdict(evidence(tests_added=[]))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.GENERATED_TESTS_PASS).passed
    assert any("no test" in r for r in reasons)


def test_verify_rejects_when_added_test_fails():
    runs = [
        {
            "selection": "project_suite",
            "passed": 5,
            "failed": 1,
            "failing_tests": ["tests/test_authz.py::test_denied"],
            "errors": 0,
            "sandbox_run_id": "s",
        }
    ]
    verdict, _, reasons = verify_agent.compute_verdict(evidence(test_runs=runs))
    assert verdict == "rejected"
    assert any("tests/test_authz.py" in r for r in reasons)


def test_verify_verdict_is_independent_of_agent_summary():
    """Even with no LLM output at all, the verdict is computable from records."""
    result = verify_agent.to_result(
        None,
        verify_agent.input_from_candidate(
            __import__("security_agents.contracts", fromlist=["PatchCandidate"]).PatchCandidate(
                finding_id="fnd_1",
                attempt=1,
                diff_uri="u",
                diff_sha256="s",
                worktree_ref="w",
                files_changed=["payments_lite/app.py"],
                tests_added=["tests/test_authz.py"],
                local_test_run_id=None,
            ),
            diagnose_agent.to_plan(diagnose_output(), diagnose_input(), None or _IntelStub())[1],
            None,
            assessment_id="asm",
            scope_id="scp",
            target_id="tgt",
            baseline_failing=[],
        ),
        evidence(original_check_ref=None),
        sandbox_run_ids=["sbx_t"],
        verify_run_id="run_v",
    )
    assert result.verdict == "rejected"  # no original check recorded => cannot prove the fix
    assert result.verify_run_id == "run_v"
    assert "did not produce a summary" in result.summary


class _IntelStub:
    """Minimal intel for the plan builder in the test above."""

    class _Snap:
        commit_sha = "sha"
        root = None

        def exists(self, p):
            return p.startswith(("payments_lite/", "tests/"))

        def line_count(self, p):
            return 200

        def files(self):
            return ["payments_lite/app.py"]

    snapshot = _Snap()

    def expectations(self, subject=None):
        from security_agents.contracts import BehavioralExpectation

        return [
            BehavioralExpectation(
                id="exp_owner_only",
                snapshot_id="snp",
                subject_ref="payments_lite/app.py",
                statement="s",
                kind="access_control",
                confidence=Confidence.HIGH,
                origin="fixture",
                status="confirmed",
            )
        ]


# --------------------------------------------------------------------------- patch / fortify


def test_patch_candidate_reflects_the_worktree_not_the_claim(intel, tmp_path):
    from security_agents.tools import PatchWorktree

    wt = PatchWorktree.create(intel, tmp_path / "wt", ["payments_lite/app.py"])
    wt.write_file(
        "payments_lite/app.py", (intel.snapshot.root / "payments_lite/app.py").read_text() + "\n# fix\n"
    )
    out = patch_agent.PatchOutput(
        explanation="x",
        files_changed=["payments_lite/app.py", "payments_lite/store.py"],
        tests_added=["tests/test_imaginary.py"],
        local_tests_passed=True,
    )
    data = patch_agent.input_from_plan(
        diagnose_agent.to_plan(diagnose_output(), diagnose_input(), intel)[1],
        assessment_id="asm",
        approval_id="apv",
        attempt=1,
        cause_paths=[],
    )
    candidate, diff, notes = patch_agent.to_candidate(out, data, wt, diff_uri="u")
    assert candidate is not None
    assert candidate.files_changed == ["payments_lite/app.py"]
    assert candidate.tests_added == []
    assert any("agent claimed" in n for n in notes)
    assert diff.startswith("diff --git")


def test_patch_with_no_changes_yields_no_candidate(intel, tmp_path):
    from security_agents.tools import PatchWorktree

    wt = PatchWorktree.create(intel, tmp_path / "wt2", ["payments_lite/app.py"])
    out = patch_agent.PatchOutput(
        explanation="could not fix",
        files_changed=[],
        tests_added=[],
        local_tests_passed=False,
        blocked_reason="needs a dependency change",
    )
    data = patch_agent.input_from_plan(
        diagnose_agent.to_plan(diagnose_output(), diagnose_input(), intel)[1],
        assessment_id="asm",
        approval_id="apv",
        attempt=1,
        cause_paths=[],
    )
    candidate, diff, notes = patch_agent.to_candidate(out, data, wt, diff_uri="u")
    assert candidate is None and diff == ""
    assert any("blocked" in n for n in notes)


def test_fortify_promotes_only_real_added_tests(intel):
    data = fortify_agent.FortifyInput(
        assessment_id="asm",
        finding_id="fnd_1",
        verification_result_id="ver_1",
        root_cause_summary="x",
        cause_class="missing_check",
        tests_added=["tests/test_authz.py"],
        affected_symbol="get_payment",
    )
    out = fortify_agent.FortifyOutput(
        recommendations=[
            fortify_agent.RecommendationOut(
                category="centralize_check",
                recommendation="route every read through the scoped lookup",
                reference_path="payments_lite/store.py",
                reference_start_line=60,
                reference_end_line=70,
                effort="small",
            )
        ],
        future_hypotheses=[
            fortify_agent.FutureHypothesisOut(
                weakness_class="CWE-639",
                title="sibling route",
                rationale="same shape",
                path="payments_lite/app.py",
                start_line=40,
                end_line=50,
            ),
            fortify_agent.FutureHypothesisOut(
                weakness_class="CWE-639",
                title="phantom",
                rationale="x",
                path="payments_lite/ghost.py",
                start_line=1,
                end_line=2,
            ),
        ],
        monitoring_hints=["/payments/{payment_id}"],
        regression_test_names=["tests/test_authz.py::test_denied"],
    )
    tests, recs, drafts, hints = fortify_agent.to_records(out, data, intel)
    assert [t.test_path for t in tests] == ["tests/test_authz.py"]
    assert tests[0].test_name == "tests/test_authz.py::test_denied"
    assert len(recs) == 1 and recs[0].references[0].path == "payments_lite/store.py"
    assert len(drafts) == 1, "a draft citing a non-existent file is dropped"
    assert hints == ["/payments/{payment_id}"]


def test_fortify_without_added_tests_promotes_nothing(intel):
    data = fortify_agent.FortifyInput(
        assessment_id="asm",
        finding_id="f",
        verification_result_id="v",
        root_cause_summary="x",
        cause_class="missing_check",
        tests_added=[],
    )
    out = fortify_agent.FortifyOutput(
        recommendations=[
            fortify_agent.RecommendationOut(category="policy_test", recommendation="add one", effort="small")
        ]
    )
    tests, recs, _, _ = fortify_agent.to_records(out, data, intel)
    assert tests == [] and len(recs) == 1


def test_verify_later_run_does_not_mask_added_test_failure():
    runs = [
        {
            "selection": "added_tests",
            "passed": 0,
            "failed": 1,
            "failing_tests": ["tests/test_authz.py::test_denied"],
            "errors": 0,
            "sandbox_run_id": "s1",
        },
        {
            "selection": "project_suite",
            "passed": 9,
            "failed": 0,
            "failing_tests": [],
            "errors": 0,
            "sandbox_run_id": "s2",
        },
    ]
    verdict, checks, reasons = verify_agent.compute_verdict(evidence(test_runs=runs))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.GENERATED_TESTS_PASS).passed
    assert any("tests/test_authz.py::test_denied" in r for r in reasons)


def test_verify_later_suite_run_does_not_mask_earlier_suite_failure():
    runs = [
        {
            "selection": "project_suite",
            "passed": 8,
            "failed": 1,
            "failing_tests": ["tests/test_payments.py::test_x"],
            "errors": 0,
            "sandbox_run_id": "s1",
        },
        {
            "selection": "project_suite",
            "passed": 9,
            "failed": 0,
            "failing_tests": [],
            "errors": 0,
            "sandbox_run_id": "s2",
        },
    ]
    verdict, checks, _ = verify_agent.compute_verdict(evidence(test_runs=runs))
    assert verdict == "rejected"
    assert not next(c for c in checks if c.name is VerificationCheckName.FUNCTIONAL_SUITE_PASSES).passed


def test_verify_later_suite_run_does_not_mask_collection_errors():
    runs = [
        {
            "selection": "project_suite",
            "passed": 3,
            "failed": 0,
            "failing_tests": [],
            "errors": 2,
            "sandbox_run_id": "s1",
        },
        {
            "selection": "project_suite",
            "passed": 9,
            "failed": 0,
            "failing_tests": [],
            "errors": 0,
            "sandbox_run_id": "s2",
        },
    ]
    verdict, _, reasons = verify_agent.compute_verdict(evidence(test_runs=runs))
    assert verdict == "rejected"
    assert any("collection errors" in r for r in reasons)


def test_check_on_a_prefix_mounted_route_decides_the_hypothesis():
    """Scout cites the handler's own path; the recipe names the mounted API path."""
    data = probe_input().model_copy(update={"subject_routes": ["GET /repositories/{repo_id}"]})
    run = check_run(False, route={"method": "GET", "path_template": "/api/v1/repositories/{id}"})
    r = probe_agent.to_validation_result(probe_out("confirmed"), data, record(), [run])
    assert r.outcome.value == "confirmed"
    other = check_run(False, route={"method": "GET", "path_template": "/api/v1/projects/{project_id}"})
    r = probe_agent.to_validation_result(probe_out("confirmed"), data, record(), [other])
    assert r.outcome.value == "inconclusive"


def test_same_route_matching():
    same = probe_agent.same_route
    assert same("GET /repositories/{repo_id}", "GET /api/v1/repositories/{id}")
    assert same("get /payments/{payment_id}", "GET /payments/{id}/")
    assert not same("POST /repositories/{repo_id}", "GET /api/v1/repositories/{id}")
    assert not same("GET /{id}", "GET /api/v1/repositories/{id}")  # a bare parameter matches nothing
    assert not same("GET /tories/{id}", "GET /api/v1/repositories/{id}")  # segment boundary


def test_scout_surveys_only_recipe_included_code(tmp_path):
    import json
    from types import SimpleNamespace

    from security_agents.agents import scout

    (tmp_path / "build_recipe.json").write_text(json.dumps({"sandbox": {"include": ["backend"]}}))
    intel = SimpleNamespace(snapshot=SimpleNamespace(root=tmp_path))
    assert scout._recipe_include(intel) == ("backend",)
    assert scout._in_target("backend/backend/api/routes.py", ("backend",))
    assert not scout._in_target("examples/payments-lite/target/app.py", ("backend",))
    assert not scout._in_target("backend2/x.py", ("backend",))
    (tmp_path / "build_recipe.json").write_text("{not json")
    assert scout._recipe_include(intel) == ()


def test_patch_can_edit_a_span_of_a_large_file_without_rewriting_it(intel, tmp_path):
    from security_agents.tools.services import PatchWorktree

    wt = PatchWorktree.create(intel, tmp_path / "wt3", ["payments_lite/app.py"])
    src = (wt.root / "payments_lite/app.py").read_text()
    anchor = "def get_payment("
    assert src.count(anchor) == 1
    out = wt.edit_file("payments_lite/app.py", anchor, "def get_payment_checked(")
    assert out["action"] == "edited" and "payments_lite/app.py" in wt.changed()
    assert "def get_payment_checked(" in (wt.root / "payments_lite/app.py").read_text()
    # ambiguous or missing spans are refused without writing
    assert wt.edit_file("payments_lite/app.py", "def ", "x")["error"] == "NO_UNIQUE_MATCH"
    assert wt.edit_file("payments_lite/app.py", "no such text", "x")["error"] == "NO_UNIQUE_MATCH"
    assert wt.edit_file("payments_lite/missing.py", "a", "b")["error"] in ("NOT_FOUND", "PATH_NOT_ALLOWED")
    # outside the approved plan: refused, file untouched
    other = next(p for p in ("payments_lite/store.py", "payments_lite/models.py") if (wt.root / p).is_file())
    before = (wt.root / other).read_text()
    assert wt.edit_file(other, before[:20], "x")["error"] == "PATH_NOT_ALLOWED"
    assert (wt.root / other).read_text() == before


def test_scout_prompt_starts_from_the_commit_change(intel):
    from security_agents.agents import scout

    entries = intel.entry_points()
    changed = entries[-1].path
    data = scout.ScoutInput(
        assessment_id="asm",
        snapshot_id="snp",
        commit_sha="a" * 40,
        changed_paths=[changed],
        change_diff=f"--- a/{changed}\n+++ b/{changed}\n-old\n+new\n",
    )
    prompt = scout.build_prompt(data, intel)
    assert "reviews one commit" in prompt and "+new" in prompt
    first_entry = prompt.index('"file":')
    assert prompt[first_entry : first_entry + 200].count(changed) == 1  # changed handler listed first
    plain = scout.build_prompt(
        scout.ScoutInput(assessment_id="asm", snapshot_id="snp", commit_sha="a" * 40), intel
    )
    assert "reviews one commit" not in plain


def test_patch_prompt_carries_the_cause_code_and_recipe_test_roots(intel):
    data = patch_agent.PatchInput(
        assessment_id="asm_t",
        finding_id="fnd_t",
        remediation_plan_id="rmp_t",
        approval_id="apv_t",
        attempt=1,
        strategy="Filter refunds by owner.",
        files_expected=["payments_lite/store.py"],
        cause_spans=[("payments_lite/store.py", 46, 48)],
    )
    prompt = patch_agent.build_prompt(data, intel)
    assert "def refunds_for(self, payment_id: str)" in prompt
    assert "cause_spans" not in prompt.split("cause_code")[0]  # not duplicated in the plan
    assert "snapshot_files" not in prompt


def test_patch_prompt_reuses_repository_credential_fixtures_and_bumps_version() -> None:
    from security_agents.agents.patch import SPEC, SYSTEM

    assert "reuse existing repository fixtures" in SYSTEM
    assert "never inline credential-shaped strings" in SYSTEM
    assert SPEC.version == "0.1.1"
    assert SPEC.prompt_ref == "prompts/patch/v0.1.1.md"


def test_added_tests_can_run_against_the_unpatched_code(intel, tmp_path, driver):
    from security_agents.agents import AgentName
    from security_agents.tools import PatchWorktree
    from security_agents.tools.services import TestRunner

    from .conftest import make_ctx

    wt = PatchWorktree.create(intel, tmp_path / "wt", ["payments_lite/app.py"])
    app = "payments_lite/app.py"
    wt.write_file(app, (intel.snapshot.root / app).read_text() + "\n# fixed\n")
    wt.write_file(
        "tests/test_marker.py",
        "import payments_lite.app as m\n\n\ndef test_fixed():\n"
        "    assert '# fixed' in open(m.__file__).read()\n",
    )
    runner = TestRunner(driver=driver, workspace_provider=lambda _ctx: wt)
    ctx = make_ctx(AgentName.PATCH, intel=intel)
    patched = runner.run(ctx, "added_tests", [])
    unpatched = runner.run(ctx, "added_tests_unpatched", [])
    assert patched["passed"] == 1 and not patched["failing_tests"], patched
    assert unpatched["failing_tests"] == ["tests/test_marker.py::test_fixed"], unpatched
    assert not list(tmp_path.glob("unpatched-*"))  # the temporary copy is removed
