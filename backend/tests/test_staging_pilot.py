"""Staging pilot gates: isolation preflight, recommendation-only delivery, provider-outage
failure visibility and stuck-work monitoring on /api/v1/ops/status."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from helpers import setup_project
from test_auto_delivery import FakeApp, _actions, _run_push, _set_mode, reference, wired
from test_preflight import _staging_settings

from backend import delivery, jobs, llm
from backend.db import assessments, transaction
from backend.db import jobs as jobs_t
from backend.delivery import authority, push_patch
from backend.llm import FakeTransport, LLMError
from backend.preflight import validate_config
from backend.worker import PermanentJobError
from shared.base import utcnow
from shared.errors import ErrorCode

__all__ = ["reference", "wired"]  # fixtures re-used from test_auto_delivery

STAGING_OK: dict = {}


# --- deployment configuration -------------------------------------------------------


def test_recommendation_only_defaults_on_in_staging_only() -> None:
    assert _staging_settings().recommendation_only
    assert not _staging_settings(app_env="production").recommendation_only
    assert not _staging_settings(app_env="test").recommendation_only


def test_valid_isolated_staging_config_passes() -> None:
    r = validate_config(_staging_settings(**STAGING_OK))
    assert r.ok, r.errors


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"pilot_recommendation_only": False}, "PILOT_RECOMMENDATION_ONLY"),
        ({"allow_auto_delivery": True}, "ALLOW_AUTO_DELIVERY"),
        ({"ops_token": None}, "OPS_TOKEN is required"),
        (
            {"database_url": "postgresql+psycopg://u:p@db.ari-ai-production-76d4.up.railway.app/x"},
            "DATABASE_URL points at production",
        ),
        ({"frontend_origin": "https://ari-ai-lake.vercel.app"}, "FRONTEND_ORIGIN points at"),
        (
            {"api_base_url": "https://api.example.com", "production_hosts": "api.example.com"},
            "API_BASE_URL points at",
        ),
        ({"auth_dev_login_enabled": True}, "AUTH_DEV_LOGIN_ENABLED"),
        ({"security_sandbox": "local-dev"}, "SECURITY_SANDBOX=local-dev"),
    ],
)
def test_unsafe_staging_config_is_refused(over: dict, needle: str) -> None:
    r = validate_config(_staging_settings(**(STAGING_OK | over)))
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_preflight_errors_never_echo_secret_values() -> None:
    r = validate_config(
        _staging_settings(
            ops_token=None,
            database_url="postgresql+psycopg://u:s3cr3t-pw@ari-ai-production-76d4.up.railway.app/x",
        )
    )
    assert r.errors and "s3cr3t-pw" not in " ".join(r.errors + r.warnings)


# --- recommendation-only delivery -----------------------------------------------------


def test_recommendation_only_withdraws_owner_delivery_authority(
    client_factory, settings_override
) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_mode(alice, ids["project"], "auto")
    with transaction() as conn:
        assert authority(conn, ids["project"]) is not None
        settings_override(pilot_recommendation_only="true")
        assert authority(conn, ids["project"]) is None


def test_recommendation_only_run_stops_at_the_human_gate_and_never_pushes(
    wired, settings_override
) -> None:
    settings_override(pilot_recommendation_only="true")
    alice, ids, asm_id = wired("auto")
    state = alice.get(f"/api/v1/assessments/{asm_id}").json()["assessment"]["state"]
    assert state == "AWAITING_APPROVAL"
    pending = alice.get(
        f"/api/v1/projects/{ids['project']}/approvals", params={"status": "pending"}
    ).json()["items"]
    assert any(item["subject_type"] == "remediation" for item in pending)
    assert "approval.auto_approved" not in _actions()
    with transaction() as conn:
        kinds = conn.execute(sa.select(jobs_t.c.kind)).scalars().all()
    assert "deliver_verified_patch" not in kinds
    assert FakeApp.calls == []


def test_push_patch_refuses_to_write_in_recommendation_only(
    settings_override, tmp_path: Path
) -> None:
    settings_override(pilot_recommendation_only="true")
    with pytest.raises(PermanentJobError) as exc:
        push_patch(tmp_path, "acme/app", "a" * 40, b"", "tok", "fix", "main")
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert delivery.push_patch is push_patch


# --- provider outage ---------------------------------------------------------------


def test_provider_outage_fails_the_assessment_visibly(
    client_factory, reference, settings_override
) -> None:
    settings_override(llm_max_retries="0")
    outage = LLMError(ErrorCode.LLM_UNAVAILABLE, "provider 503", retryable=True)
    llm.TRANSPORT_OVERRIDE = lambda: FakeTransport([outage] * 50)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    asm_id = _run_push(alice, ids["project"])
    view = alice.get(f"/api/v1/assessments/{asm_id}").json()["assessment"]
    assert view["state"] == "FAILED"
    assert "LLM_UNAVAILABLE" in str(view)


# --- stuck-work monitoring --------------------------------------------------------------


def _ops(client) -> dict:
    r = client.get("/api/v1/ops/status")
    assert r.status_code == 200
    return r.json()


def test_ops_status_reports_stuck_assessments_failed_jobs_and_posture(
    client_factory, reference
) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    asm_id = _run_push(alice, ids["project"])
    body = _ops(alice)
    assert body["stuck"]["assessments"] == 0 and body["stuck"]["jobs"] == 0
    assert body["pilot"] == {
        "recommendation_only": False,
        "auto_delivery_allowed": True,
        "projects_not_manual_delivery": 0,
    }

    with transaction() as conn:
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == asm_id)
            .values(state="INDEXING", updated_at=utcnow() - timedelta(hours=3))
        )
        dead, _ = jobs.enqueue(conn, "staging_probe", {}, project_id=ids["project"])
        conn.execute(sa.update(jobs_t).where(jobs_t.c.id == dead).values(status="dead"))
        stale, _ = jobs.enqueue(conn, "staging_probe", {}, project_id=ids["project"])
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == stale)
            .values(
                status="running",
                lease_owner="w-gone",
                lease_expires_at=utcnow() - timedelta(minutes=5),
            )
        )
    body = _ops(alice)
    assert body["stuck"]["assessments"] == 1
    assert body["stuck"]["assessment_ids"] == [asm_id]
    assert body["stuck"]["jobs_by_reason"].get("lease_expired") == 1
    assert stale in body["stuck"]["job_ids"]
    assert body["failed_jobs_24h"] == {"dead": 1}


def test_assessment_awaiting_a_human_or_owned_by_a_live_job_is_not_stuck(
    client_factory, reference
) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    asm_id = _run_push(alice, ids["project"])
    old = utcnow() - timedelta(hours=3)
    with transaction() as conn:
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == asm_id)
            .values(state="AWAITING_APPROVAL", updated_at=old)
        )
    assert _ops(alice)["stuck"]["assessments"] == 0
    with transaction() as conn:
        conn.execute(sa.update(assessments).where(assessments.c.id == asm_id).values(state="RECON"))
        jobs.enqueue(conn, "run_assessment", {"assessment_id": asm_id}, project_id=ids["project"])
    assert _ops(alice)["stuck"]["assessments"] == 0


# --- release gate ---------------------------------------------------------------------


def _release_check():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "deploy" / "release_check.py"
    spec = importlib.util.spec_from_file_location("release_check_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gate(body: dict) -> dict[str, str]:
    rc = _release_check()
    rc.check_monitoring(body)
    return {name: status for status, name, _ in rc.results}


def test_release_gate_fails_on_stuck_work_or_unsafe_staging_posture() -> None:
    safe = {
        "app_env": "staging",
        "stuck": {"jobs": 0, "assessments": 0, "jobs_by_reason": {}},
        "failed_jobs_24h": {},
        "pilot": {"recommendation_only": True, "auto_delivery_allowed": False},
    }
    assert set(_gate(safe).values()) == {"PASS"}
    stuck = safe | {"stuck": {"jobs": 1, "assessments": 0, "jobs_by_reason": {"x": 1}}}
    assert _gate(stuck)["stuck assessments/jobs"] == "FAIL"
    unsafe = safe | {"pilot": {"recommendation_only": False, "auto_delivery_allowed": False}}
    assert _gate(unsafe)["recommendation-only posture"] == "FAIL"
    assert (
        _gate({k: v for k, v in safe.items() if k != "stuck"})["stuck assessments/jobs"] == "FAIL"
    )
    assert _gate(safe | {"failed_jobs_24h": {"dead": 2}})["failed jobs (24h)"] == "WARN"


# --- CI evidence gate (Codex release review findings) --------------------------------------


def _ci_gate(monkeypatch, checks: list[dict] | None, *, strict: bool, allow: tuple[str, ...] = ()):
    import json
    import subprocess
    from types import SimpleNamespace

    rc = _release_check()
    rollup = json.dumps({"number": 1, "statusCheckRollup": checks})

    def fake_run(cmd, **_kw):
        if cmd[:2] == ["gh", "pr"]:
            return SimpleNamespace(returncode=0, stdout=rollup, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc.check_ci(strict=strict, allow_skipped=allow)
    return {name: (status, detail) for status, name, detail in rc.results}["CI success"]


def _done(name: str, conclusion: str | None) -> dict:
    return {"name": name, "status": "COMPLETED", "conclusion": conclusion}


def test_ci_gate_rejects_an_empty_check_list(monkeypatch) -> None:
    assert _ci_gate(monkeypatch, [], strict=True)[0] == "FAIL"
    assert _ci_gate(monkeypatch, [], strict=False)[0] == "FAIL"
    assert _ci_gate(monkeypatch, None, strict=True)[0] == "FAIL"


def test_ci_gate_does_not_count_skipped_security_checks_as_pass(monkeypatch) -> None:
    checks = [_done("backend-tests", "SUCCESS"), _done("gitleaks", "SKIPPED"), _done("audit", None)]
    status, detail = _ci_gate(monkeypatch, checks, strict=True)
    assert status != "PASS"
    assert "gitleaks" in detail and "audit" in detail
    assert _ci_gate(monkeypatch, checks, strict=False)[0] == "WARN"


def test_ci_gate_passes_only_when_every_non_allowlisted_check_succeeded(monkeypatch) -> None:
    checks = [_done("backend-tests", "SUCCESS"), _done("live", "SKIPPED")]
    assert _ci_gate(monkeypatch, checks, strict=True)[0] != "PASS"
    status, detail = _ci_gate(monkeypatch, checks, strict=True, allow=("live",))
    assert status == "PASS" and "live" in detail
    assert _ci_gate(monkeypatch, [_done("backend-tests", "SUCCESS")], strict=True)[0] == "PASS"
    failing = [_done("backend-tests", "FAILURE"), _done("live", "SKIPPED")]
    assert _ci_gate(monkeypatch, failing, strict=True, allow=("live",))[0] == "FAIL"


def test_ci_gate_reads_commit_statuses_as_well_as_check_runs(monkeypatch) -> None:
    # `gh pr view --json statusCheckRollup` mixes CheckRun rows (status/conclusion/name) with
    # StatusContext rows (state/context); a failing or pending commit status must not vanish.
    ok = [_done("backend-tests", "SUCCESS"), {"context": "Devin Review", "state": "SUCCESS"}]
    assert _ci_gate(monkeypatch, ok, strict=True)[0] == "PASS"
    failing = [_done("backend-tests", "SUCCESS"), {"context": "Devin Review", "state": "FAILURE"}]
    status, detail = _ci_gate(monkeypatch, failing, strict=True)
    assert status == "FAIL" and "Devin Review" in detail
    pending = [_done("backend-tests", "SUCCESS"), {"context": "Devin Review", "state": "PENDING"}]
    assert _ci_gate(monkeypatch, pending, strict=True)[0] == "WARN"


def test_strict_signoff_requires_every_gate_to_have_reported() -> None:
    rc = _release_check()
    rc.results.clear()
    for gate in rc.REQUIRED_GATES:
        rc.report("PASS", gate, "ok")
    assert rc.finalize(strict=True) == 0
    rc.results.clear()
    assert rc.finalize(strict=True) != 0  # nothing ran: must not sign off
    rc.results.clear()
    for gate in rc.REQUIRED_GATES[1:]:
        rc.report("PASS", gate, "ok")
    assert rc.finalize(strict=True) != 0  # one gate never produced a row
    assert any(s == "FAIL" and rc.REQUIRED_GATES[0] in n for s, n, _ in rc.results)
    rc.results.clear()
    rc.report("PASS", rc.REQUIRED_GATES[0], "ok")
    assert rc.finalize(strict=False) == 0  # non-strict keeps today's FAIL-only semantics
