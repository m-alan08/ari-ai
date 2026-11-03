"""Strict release sign-off is bound to the candidate SHA: required checks on that exact
commit, a PR head that does not move during verification, and the deployed build."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from test_staging_pilot import _release_check

SHA = "a" * 40
OTHER = "b" * 40
REQUIRED = ("backend-tests", "gitleaks")


def _run(checks, *, heads=(SHA, SHA), statuses=(), status_sha=SHA):
    heads = list(heads)

    def fake_run(cmd, **_kw):
        out = ""
        if cmd[:3] == ["gh", "pr", "view"]:
            head = heads.pop(0) if heads else SHA
            rollup = [
                {"name": c["name"], "status": "COMPLETED", "conclusion": "SUCCESS"} for c in checks
            ]
            out = json.dumps({"number": 1, "statusCheckRollup": rollup, "headRefOid": head})
        elif cmd[:2] == ["gh", "api"] and cmd[2].endswith("/check-runs?per_page=100"):
            out = json.dumps({"check_runs": checks})
        elif cmd[:2] == ["gh", "api"] and cmd[2].endswith("/status"):
            out = json.dumps({"sha": status_sha, "statuses": list(statuses)})
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    return fake_run


def _gate(monkeypatch, checks, **kw):
    rc = _release_check()
    monkeypatch.setattr(subprocess, "run", _run(checks, **kw))
    rc.check_ci(strict=True, expected_sha=SHA, required=REQUIRED)
    return {name: (status, detail) for status, name, detail in rc.results}.get(
        "CI bound to candidate SHA"
    )


def _check(name, conclusion="success", status="completed", sha=SHA, at="2026-10-29T00:00:00Z"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "completed_at": at,
    }


GREEN = [_check("backend-tests"), _check("gitleaks")]


def test_required_checks_green_on_the_candidate_pass(monkeypatch) -> None:
    assert _gate(monkeypatch, GREEN)[0] == "PASS"


def test_a_missing_required_check_fails(monkeypatch) -> None:
    status, detail = _gate(monkeypatch, [_check("backend-tests")])
    assert status == "FAIL" and "gitleaks: missing" in detail


def test_a_skipped_required_check_fails(monkeypatch) -> None:
    status, detail = _gate(monkeypatch, [_check("backend-tests"), _check("gitleaks", "skipped")])
    assert status == "FAIL" and "gitleaks: skipped" in detail


def test_pending_and_failed_required_checks_fail(monkeypatch) -> None:
    pending = [_check("backend-tests", None, "in_progress"), _check("gitleaks")]
    assert "backend-tests: pending" in _gate(monkeypatch, pending)[1]
    failed = [_check("backend-tests", "failure"), _check("gitleaks")]
    assert _gate(monkeypatch, failed) == ("FAIL", "backend-tests: failure")


def test_results_for_another_sha_are_not_evidence(monkeypatch) -> None:
    stale = [_check("backend-tests"), _check("gitleaks", sha=OTHER)]
    status, detail = _gate(monkeypatch, stale)
    assert status == "FAIL" and "gitleaks: results only for another SHA" in detail


def test_latest_rerun_on_the_candidate_decides(monkeypatch) -> None:
    rerun = [*GREEN, _check("backend-tests", "failure", at="2026-10-28T00:00:00Z")]
    assert _gate(monkeypatch, rerun)[0] == "PASS"


def test_pr_head_that_is_not_the_candidate_fails(monkeypatch) -> None:
    status, detail = _gate(monkeypatch, GREEN, heads=(OTHER, OTHER))
    assert status == "FAIL" and "is not the candidate" in detail


def test_head_moving_during_verification_fails(monkeypatch) -> None:
    status, detail = _gate(monkeypatch, GREEN, heads=(SHA, OTHER))
    assert status == "FAIL" and "moved" in detail


def test_required_commit_status_is_read_from_the_candidate(monkeypatch) -> None:
    rc = _release_check()
    statuses = [{"context": "Devin Review", "state": "pending"}]
    monkeypatch.setattr(subprocess, "run", _run(GREEN, statuses=statuses))
    rc.check_ci(strict=True, expected_sha=SHA, required=(*REQUIRED, "Devin Review"))
    gate = {n: (s, d) for s, n, d in rc.results}["CI bound to candidate SHA"]
    assert gate == ("FAIL", "Devin Review: pending")


def _identity(monkeypatch, code, body, expected=SHA):
    rc = _release_check()
    monkeypatch.setattr(rc, "get_json", lambda _url: (code, body))
    rc.check_identity("https://staging.example", expected)
    return {n: (s, d) for s, n, d in rc.results}["deployed candidate identity"]


def test_deployed_candidate_must_match_the_expected_sha(monkeypatch) -> None:
    assert _identity(monkeypatch, 200, {"status": "ok", "git_sha": SHA})[0] == "PASS"
    status, detail = _identity(monkeypatch, 200, {"status": "ok", "git_sha": OTHER})
    assert status == "FAIL" and "is not the candidate" in detail
    assert _identity(monkeypatch, 200, {"status": "ok", "git_sha": None})[0] == "FAIL"
    assert _identity(monkeypatch, 200, {"git_sha": SHA}, expected=None)[0] == "SKIP"


def test_strict_signoff_without_candidate_binding_fails() -> None:
    rc = _release_check()
    rc.results.clear()
    for gate in rc.REQUIRED_GATES:
        if gate not in ("CI bound to candidate SHA", "deployed candidate identity"):
            rc.report("PASS", gate, "ok")
    assert rc.finalize(strict=True) != 0
    assert {"CI bound to candidate SHA", "deployed candidate identity"} <= {
        n for s, n, _ in rc.results if s == "FAIL"
    }
