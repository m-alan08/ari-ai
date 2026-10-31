"""Tier 2/4 integration: signed webhook -> durable job -> monitoring assessment -> worker
hosts an orchestrator through AssessmentHost -> agent runs, findings, approval gate,
patch, independent Verify -> persisted evidence -> REST + SSE consumed like the UI.

The orchestrator is the TEST DOUBLE in reference_orchestrator.py with a FakeTransport;
this proves the backend contract and gates, not live agent intelligence."""

from __future__ import annotations

import pytest
from helpers import post_webhook, push_payload, setup_project
from reference_orchestrator import scripted_transport

from backend import llm, worker


@pytest.fixture
def reference(settings_override):
    settings_override(orchestrator_factory="reference_orchestrator:build_orchestrator")
    llm.TRANSPORT_OVERRIDE = scripted_transport


def _start_from_webhook(alice, bob) -> tuple[dict, str]:
    ids = setup_project(alice, bob)
    assert post_webhook(alice, push_payload(["app/invoices.py"])).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    asm_id = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"][0][
        "assessment_id"
    ]
    worker.drain(kinds=["run_assessment"])
    return ids, asm_id


def test_full_loop_with_approval_gate(client_factory, reference) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids, asm_id = _start_from_webhook(alice, bob)
    detail = alice.get(f"/api/v1/assessments/{asm_id}").json()
    assert detail["assessment"]["state"] == "AWAITING_APPROVAL"
    agents = [r["agent"] for r in detail["agent_runs"]]
    assert agents[:3] == ["reviewer", "scout", "probe"], agents
    assert sorted(agents[3:]) == ["diagnose", "trace"], agents
    assert all(
        r["status"] == "succeeded" and r["provider_mode"] == "fake" for r in detail["agent_runs"]
    )
    assert all(
        r["transcript_artifact_id"] and r["model_id"] == "claude-haiku-5-5"
        for r in detail["agent_runs"]
    )
    usage = detail["assessment"]["usage"]
    assert usage["llm_calls"] == 5 and usage["input_tokens"] == 4000 and usage["usd"] > 0
    assert detail["findings"][0]["status"] == "validated"

    pending = alice.get(
        f"/api/v1/projects/{ids['project']}/approvals", params={"status": "pending"}
    ).json()["items"]
    assert len(pending) == 1 and pending[0]["subject_type"] == "remediation"
    r = bob.post(
        f"/api/v1/approvals/{pending[0]['id']}/decision",
        json={"decision": "approved", "reason": "go"},
    )
    assert r.status_code == 200, r.text
    worker.drain(kinds=["run_assessment"])

    detail = alice.get(f"/api/v1/assessments/{asm_id}").json()
    assert detail["assessment"]["state"] == "COMPLETED"
    assert [r["agent"] for r in detail["agent_runs"]][5:] == ["patch", "verify", "fortify"]
    fid = detail["findings"][0]["id"]
    assert detail["findings"][0]["status"] == "verified"
    patches = alice.get(f"/api/v1/findings/{fid}/patches").json()["items"]
    assert patches[0]["verification"]["verdict"] == "accepted"
    diff = alice.get(f"/api/v1/artifacts/{patches[0]['patch']['diff_artifact_id']}")
    assert diff.status_code == 200 and b"tenant" in diff.content
    assert diff.headers["X-Artifact-SHA256"] == patches[0]["patch"]["diff_sha256"]
    report = alice.get(f"/api/v1/assessments/{asm_id}/report").json()
    assert report["source"] == "persisted records" and len(report["agent_runs"]) == 8
    # 0.8.0 DTOs: the wire bodies are exactly the shared models (Vihaan's M6 ask c).
    from shared.api import AssessmentReport, FindingDetail

    assert AssessmentReport.model_validate(report).findings
    detail = FindingDetail.model_validate(alice.get(f"/api/v1/findings/{fid}").json())
    assert detail.finding.id == fid

    # SSE: full replay from the durable log, closes after the terminal event.
    with alice.stream("GET", f"/api/v1/assessments/{asm_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())
    states = [
        ln.split('"state":"')[1].split('"')[0]
        for ln in text.splitlines()
        if ln.startswith("data:") and '"type":"assessment.state_changed"' in ln
    ]
    assert states == [
        "CREATED",
        "SCOPE_CHECK",
        "INDEXING",
        "FUNCTIONAL_REVIEW",
        "RECON",
        "VALIDATION",
        "ANALYSIS",
        "DIAGNOSIS",
        "AWAITING_APPROVAL",
        "REMEDIATION",
        "HARDENING",
        "REPORTING",
        "COMPLETED",
    ]
    assert "event: assessment.completed" in text
    # Reconnect with Last-Event-ID: only later events, nothing synthesized.
    all_ids = [int(ln[4:]) for ln in text.splitlines() if ln.startswith("id: ")]
    assert all_ids == list(range(1, len(all_ids) + 1))
    mid = all_ids[len(all_ids) // 2]
    with alice.stream(
        "GET", f"/api/v1/assessments/{asm_id}/events", headers={"Last-Event-ID": str(mid)}
    ) as resp:
        ids_after = [int(ln[4:]) for ln in resp.iter_lines() if ln.startswith("id: ")]
    assert ids_after == all_ids[all_ids.index(mid) + 1 :]
    with alice.stream(
        "GET", f"/api/v1/assessments/{asm_id}/events", headers={"Last-Event-ID": str(all_ids[-1])}
    ) as resp:
        assert [ln for ln in resp.iter_lines() if ln.startswith("id: ")] == []  # closes, no hang
    eve = client_factory("eve")
    assert eve.get(f"/api/v1/assessments/{asm_id}/events").status_code == 404


def test_declined_remediation_skips_patching(client_factory, reference) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids, asm_id = _start_from_webhook(alice, bob)
    ap = alice.get(
        f"/api/v1/projects/{ids['project']}/approvals", params={"status": "pending"}
    ).json()["items"][0]
    r = bob.post(
        f"/api/v1/approvals/{ap['id']}/decision", json={"decision": "declined", "reason": "not now"}
    )
    assert r.status_code == 200
    worker.drain(kinds=["run_assessment"])
    detail = alice.get(f"/api/v1/assessments/{asm_id}").json()
    assert detail["assessment"]["state"] == "COMPLETED"
    assert "patch" not in [r["agent"] for r in detail["agent_runs"]]


def test_verify_must_be_independent_of_patch(client_factory, settings_override) -> None:
    settings_override(orchestrator_factory="reference_orchestrator:build_cheating_orchestrator")
    llm.TRANSPORT_OVERRIDE = scripted_transport
    alice, bob = client_factory("alice"), client_factory("bob")
    ids, asm_id = _start_from_webhook(alice, bob)
    ap = alice.get(
        f"/api/v1/projects/{ids['project']}/approvals", params={"status": "pending"}
    ).json()["items"][0]
    bob.post(f"/api/v1/approvals/{ap['id']}/decision", json={"decision": "approved"})
    worker.drain(kinds=["run_assessment"])
    a = alice.get(f"/api/v1/assessments/{asm_id}").json()["assessment"]
    assert a["state"] == "FAILED" and a["error"]["code"] == "FORBIDDEN"
    assert "verify" in a["error"]["message"].lower()


@pytest.mark.parametrize("message", ["index failed", ""])
def test_failed_transition_always_records_error(client_factory, reference, message) -> None:
    from backend.host import AssessmentHost
    from shared.assessment import AssessmentState

    alice, bob = client_factory("alice"), client_factory("bob")
    _, asm_id = _start_from_webhook(alice, bob)
    result = AssessmentHost(asm_id).transition(AssessmentState.FAILED, message)
    assert result.error is not None
    assert result.error.code.value == "INTERNAL"
    assert result.error.message
    assert alice.get(f"/api/v1/assessments/{asm_id}").json()["assessment"]["error"] == (
        result.error.model_dump(mode="json")
    )


def test_budget_exhaustion_stops_the_run(client_factory, reference) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    r = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={
            "scope_id": ids["scope"],
            "repo_id": ids["repo"],
            "commit_sha": "f" * 40,
            "budget": {"max_tokens": 1500, "max_usd": 10, "max_wall_clock_s": 600},
        },
    )
    asm_id = r.json()["assessment"]["id"]
    worker.drain(kinds=["run_assessment"])
    a = alice.get(f"/api/v1/assessments/{asm_id}").json()
    assert a["assessment"]["state"] == "BUDGET_EXHAUSTED"
    assert a["assessment"]["error"]["code"] == "BUDGET_EXHAUSTED"
    assert len(a["agent_runs"]) <= 2


def test_host_refuses_illegal_transitions_and_unapproved_remediation(
    client_factory, settings_override
) -> None:
    from backend.host import AssessmentHost, HostError
    from shared.assessment import AssessmentState

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    asm_id = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": "f" * 40},
    ).json()["assessment"]["id"]
    host = AssessmentHost(asm_id)
    with pytest.raises(HostError) as e:
        host.transition(AssessmentState.REMEDIATION)
    assert e.value.code.value == "APPROVAL_REQUIRED"
    with pytest.raises(HostError) as e:
        host.transition(AssessmentState.COMPLETED)
    assert e.value.code.value == "CONFLICT_STATE"
    host.transition(AssessmentState.SCOPE_CHECK)
    alice.post(f"/api/v1/scopes/{ids['scope']}/revoke")
    with pytest.raises(HostError) as e:
        host.transition(AssessmentState.INDEXING)
    assert e.value.code.value in ("SCOPE_DENIED", "CANCELLED")


def test_host_checkpoints_are_scoped_durable_and_integrity_checked(client_factory):
    """Salvaged from PR #35: fresh AssessmentHost instances must see the latest payload."""
    import sqlalchemy as sa

    from backend.db import artifacts, transaction
    from backend.host import AssessmentHost, HostError

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)

    def create() -> str:
        return alice.post(
            f"/api/v1/projects/{ids['project']}/assessments",
            json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": "f" * 40},
        ).json()["assessment"]["id"]

    first, other = create(), create()
    host = AssessmentHost(first)
    assert host.load_checkpoint("test.v1") is None
    host.save_checkpoint("test.v1", {"step": 1})
    host.save_checkpoint("test.v1", {"step": 2})
    assert AssessmentHost(first).load_checkpoint("test.v1") == {"step": 2}
    assert AssessmentHost(other).load_checkpoint("test.v1") is None
    assert host.load_checkpoint("different.v1") is None
    with transaction() as conn:
        rows = (
            conn.execute(sa.select(artifacts).where(artifacts.c.project_id == ids["project"]))
            .mappings()
            .all()
        )
    for artifact in rows:
        (host.store.root / artifact["storage_key"]).write_bytes(b"corrupted")
    with pytest.raises(OSError, match="integrity"):
        host.load_checkpoint("test.v1")
    host.save_checkpoint("claim.v1", {"step": 3})
    claimed = AssessmentHost(first).claim_checkpoint("claim.v1")
    assert claimed == {"step": 3}
    assert AssessmentHost(first).claim_checkpoint("claim.v1") is None
    assert AssessmentHost(first).load_checkpoint("claim.v1") is None
    with pytest.raises(HostError) as missing:
        AssessmentHost(first).claim_checkpoint("never-saved.v1")
    assert missing.value.code.value == "CONFLICT_STATE"


def test_a_claim_held_by_a_job_that_stopped_running_can_be_reclaimed(client_factory):
    """An aborted resume (worker died, deploy restart) must not strand the assessment."""
    from types import SimpleNamespace

    import sqlalchemy as sa

    from backend import jobs as jobq
    from backend.db import assessments, jobs, transaction
    from backend.host import AssessmentHost

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    first = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": "f" * 40},
    ).json()["assessment"]["id"]
    with transaction() as conn:
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == first)
            .values(state="AWAITING_APPROVAL")
        )
        job_id, _ = jobq.enqueue(conn, "run_assessment", {"assessment_id": first})
        conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(status="running"))
    holder = SimpleNamespace(job=SimpleNamespace(id=job_id), check_cancelled=lambda: None)
    AssessmentHost(first).save_checkpoint("lease.v1", {"lease_test": 1})
    assert AssessmentHost(first, job_ctx=holder).claim_checkpoint("lease.v1") == {"lease_test": 1}
    assert AssessmentHost(first).claim_checkpoint("lease.v1") is None  # holder still running
    with transaction() as conn:
        conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(status="succeeded"))
    assert AssessmentHost(first).claim_checkpoint("lease.v1") == {"lease_test": 1}  # lapsed claim
    with transaction() as conn:  # once the resume moved the assessment on, never replay it
        conn.execute(
            sa.update(assessments).where(assessments.c.id == first).values(state="REMEDIATION")
        )
    assert AssessmentHost(first, job_ctx=holder).claim_checkpoint("lease.v1") is None
