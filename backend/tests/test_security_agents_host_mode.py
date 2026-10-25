"""Executable acceptance test for Alan's host mode (security_agents.api:build_orchestrator
-> HostOrchestrator.run(host)) against the REAL backend: AssessmentHost on PostgreSQL,
the real LLMGateway (scripted transport labeled fake, see sa_bridge.py), the worker,
API approval and resume.

Initially failed on five conformance issues (property/method shapes, provider_mode,
fabricated patch run id, missing per-run usage/tool calls, in-memory resume); fixed in
security_agents/host_adapter.py by Srihith while Alan was unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

security_agents = pytest.importorskip("security_agents")
pytest.importorskip("security_agents.host_adapter")
TARGET = Path(security_agents.__file__).resolve().parents[2] / "examples" / "payments-lite"
if not (TARGET / "build_recipe.json").exists():
    pytest.skip(
        "security_agents checkout without payments-lite build_recipe.json", allow_module_level=True
    )

from helpers import add_member  # noqa: E402

from backend import llm, worker  # noqa: E402


def test_host_mode_full_flow_against_real_backend(
    client_factory, settings_override, monkeypatch, tmp_path
):
    from sa_bridge import AlanScriptTransport

    monkeypatch.setenv("SECURITY_AGENTS_TARGET_DIR", str(TARGET))
    monkeypatch.setenv("SECURITY_AGENTS_WORKDIR", str(tmp_path / "w"))
    # The directory fixture has no durable index, so host intel falls back to the
    # labels file — without it, expectations__for_subject returns nothing and the
    # fact-derived CVSS is honestly incomplete.
    monkeypatch.setenv("SECURITY_AGENTS_LABELS", str(TARGET / "labels.yaml"))
    settings_override(
        orchestrator_factory="security_agents.api:build_orchestrator", security_sandbox="local-dev"
    )
    bridge = AlanScriptTransport(TARGET / "target")
    llm.TRANSPORT_OVERRIDE = lambda: bridge
    alice, bob = client_factory("alice"), client_factory("bob")
    add_member(alice.me["memberships"][0]["organization"]["id"], bob.me["user"]["id"])
    pid = alice.post(
        "/api/v1/projects", json={"name": "p", "settings": {"delivery_mode": "manual"}}
    ).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories", json={"full_name": "acme/payments-lite"}
    ).json()["id"]
    sid = alice.post(
        f"/api/v1/projects/{pid}/scopes",
        json={
            "name": "s",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "targets": [
                {"kind": "sandbox_build", "repo_id": rid, "build_recipe_ref": "build_recipe.json"}
            ],
            "allowed_check_categories": ["access_control.ownership", "input_validation.bounds"],
        },
    ).json()["id"]
    assert bob.post(f"/api/v1/scopes/{sid}/approve").status_code == 200
    asm = alice.post(
        f"/api/v1/projects/{pid}/assessments",
        json={"scope_id": sid, "repo_id": rid, "commit_sha": "b" * 40},
    ).json()["assessment"]["id"]

    worker.drain(kinds=["run_assessment"])
    detail = alice.get(f"/api/v1/assessments/{asm}").json()
    assert detail["assessment"]["state"] == "AWAITING_APPROVAL", detail["assessment"]
    pending = alice.get(f"/api/v1/projects/{pid}/approvals", params={"status": "pending"}).json()[
        "items"
    ]
    assert (
        bob.post(
            f"/api/v1/approvals/{pending[0]['id']}/decision", json={"decision": "approved"}
        ).status_code
        == 200
    )
    worker.drain(kinds=["run_assessment"])

    detail = alice.get(f"/api/v1/assessments/{asm}").json()
    assert detail["assessment"]["state"] == "COMPLETED", detail["assessment"]
    runs = detail["agent_runs"]
    order = [r["agent"] for r in runs]
    # Scout then Probe; Diagnose runs alongside Trace, Fortify alongside Patch -> Verify.
    assert order[:2] == ["scout", "probe"], order
    assert sorted(order[2:4]) == ["diagnose", "trace"], order
    assert sorted(order[4:]) == ["fortify", "patch", "verify"], order
    assert all(r["usage"]["llm_calls"] > 0 and r["tool_calls"] for r in runs), (
        "per-run usage/tool calls recorded"
    )
    patches = alice.get(f"/api/v1/findings/{detail['findings'][0]['id']}/patches").json()["items"]
    assert patches and patches[-1]["verification"]["verdict"] == "accepted"
    # The patch hash names the member-facing artifact; the binding names Verify's bytes.
    diff = alice.get(f"/api/v1/artifacts/{patches[-1]['patch']['diff_artifact_id']}")
    assert diff.status_code == 200
    assert diff.headers["X-Artifact-SHA256"] == patches[-1]["patch"]["diff_sha256"]
    assert b"[REDACTED" in diff.content
    # The accepted verdict is bound to the exact bytes Verify executed (pre-redaction).
    import hashlib
    import json as _json

    ver = patches[-1]["verification"]
    scope = next(c for c in ver["checks"] if c["name"] == "diff_scope_ok")
    binding_ref = next(
        e for e in scope["evidence"] if "verified-bytes binding" in (e["note"] or "")
    )
    binding = _json.loads(alice.get(f"/api/v1/artifacts/{binding_ref['artifact_id']}").content)
    assert binding["stored_diff_sha256"] == diff.headers["X-Artifact-SHA256"]
    assert binding["verified_diff_sha256"] != patches[-1]["patch"]["diff_sha256"]
    assert binding["redacted"] is True
    assert binding["sealed_diff_artifact_id"]
    assert alice.get(f"/api/v1/artifacts/{binding['sealed_diff_artifact_id']}").status_code == 404
    raw = sorted((tmp_path / "w").rglob("patch-*.diff"))[-1].read_bytes()
    assert binding["verified_diff_sha256"] == hashlib.sha256(raw).hexdigest()

    # Vihaan's M6 observations: lifecycle persisted with history; usage ledger complete.
    finding = alice.get(f"/api/v1/findings/{detail['findings'][0]['id']}").json()["finding"]
    assert finding["status"] == "hardened", finding["status"]
    path = [(h["from_status"], h["to_status"]) for h in finding["history"]]
    assert path == [
        ("hypothesis", "validated"),
        ("validated", "diagnosed"),
        ("diagnosed", "patch_proposed"),
        ("patch_proposed", "verified"),
        ("verified", "hardened"),
    ], path
    usage = detail["assessment"]["usage"]
    assert usage["tool_calls"] == sum(len(r["tool_calls"]) for r in runs) > 0
    assert usage["llm_calls"] == sum(r["usage"]["llm_calls"] for r in runs)
    assert usage["sandbox_seconds"] > 0, "Probe/Verify sandbox time reaches the ledger"
    # The persisted transcript keeps successful tool results (Codex review on #218).
    calls = _probe_transcript_calls(alice, runs)
    check = next(c for c in calls if c["name"] == "checks.run_template" and c["ok"])
    assert "matches_expectation" in check["summary"] and "exit_code" in check["summary"]


def _probe_transcript_calls(client, runs):
    import json as _json

    probe = next(r for r in runs if r["agent"] == "probe")
    art = client.get(f"/api/v1/artifacts/{probe['transcript_artifact_id']}")
    assert art.status_code == 200
    return _json.loads(art.content)["tool_invocations"]
