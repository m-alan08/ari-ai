"""Live Defense Phase 7C: real authorized investigative agent execution."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import llm, worker
from backend.db import snapshots, transaction
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    FIRST_RESPONDER_JOB_KINDS,
    AgentInvocationStatus,
    ConfirmationState,
    EvidenceLifecycleState,
    ExecutionIntegrity,
)

pytest.importorskip("security_agents")

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYMENTS = REPO_ROOT / "examples" / "payments-lite"
ROUTE = "GET /payments/{payment_id}"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=phase7c",
            "-c",
            "user.email=phase7c@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        text=True,
    ).strip()


def _batch(end, requests: int = 20, e4: int = 18, auth: int = 18) -> dict:
    return {
        "source": "owned_staging",
        "window_start": (end - timedelta(minutes=1)).isoformat(),
        "window_end": end.isoformat(),
        "routes": [
            {
                "route": ROUTE,
                "requests": requests,
                "status_4xx": e4,
                "auth_failures": auth,
            }
        ],
    }


def _owned_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "owned-repo"
    shutil.copytree(
        PAYMENTS,
        repo,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info", ".venv"),
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "payments-lite")
    return repo, _git(repo, "rev-parse", "HEAD")


def _ready_snapshot(repo_id: str, sha: str) -> str:
    sid = new_id(IdPrefix.snp)
    with transaction() as conn:
        conn.execute(
            sa.insert(snapshots).values(
                id=sid,
                repo_id=repo_id,
                commit_sha=sha,
                index_version="phase7c",
                status="ready",
                doc={},
                created_at=utcnow(),
            )
        )
    return sid


def _register(alice, pid: str, name: str = "payments staging") -> dict:
    app = alice.post(
        f"/api/v1/projects/{pid}/applications",
        json={"name": name, "environment": "owned_test"},
    )
    assert app.status_code == 201, app.text
    return app.json()


def _bind_and_fake(
    tmp_path: Path, repo: Path, repo_id: str, settings_override, monkeypatch
) -> None:
    from sa_bridge import AlanScriptTransport

    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps([{"repo_id": repo_id, "local_path": str(repo), "subdir": "."}]))
    monkeypatch.setenv("SECURITY_AGENTS_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setenv("SECURITY_AGENTS_LABELS", str(PAYMENTS / "labels.yaml"))
    settings_override(
        repository_bindings_file=str(bindings),
        security_sandbox="local-dev",
        security_workdir=str(tmp_path / "work"),
    )
    llm.TRANSPORT_OVERRIDE = lambda: AlanScriptTransport(repo / "target")


def test_schema_keeps_0_23_without_claiming_0_20() -> None:
    from shared import SCHEMA_VERSION
    from shared.live_defense import ORCHESTRATOR_VERSION

    # F9: additive contract bumps (observability, onboarding, disconnect) must
    # not be blocked by an exact version pin — assert a floor, not equality.
    major, minor, _patch = (int(part) for part in SCHEMA_VERSION.split("."))
    assert (major, minor) >= (0, 23)
    assert ORCHESTRATOR_VERSION == "7c.1.0"


def test_scout_executes_with_mapping_and_snapshot(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    assert (
        alice.post(
            f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"}
        ).status_code
        == 200
    )
    mapping = alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    assert mapping.status_code == 201, mapping.text
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    now = utcnow()
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(now),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    by_agent = {item["agent"]: item for item in invocations}
    scout = by_agent["scout"]
    assert scout["status"] == AgentInvocationStatus.executed.value
    assert scout["provider_mode"] == "fake"
    assert scout["execution"]["typed_output"] is True
    assert scout["execution"]["execution_integrity"] == ExecutionIntegrity.checked.value
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["confirmation"] == ConfirmationState.unverified.value
    assert scout["execution"]["predicted_probability"] is None
    assert scout["execution"]["latency_ms"] is not None and scout["execution"]["latency_ms"] >= 0
    assert scout["execution"]["cost_usd"] is not None
    assert scout["execution"]["tool_call_count"] and scout["execution"]["tool_call_count"] > 0
    assert scout["execution"]["transcript_artifact_id"]
    assert scout["execution"]["hypothesis_count"] and scout["execution"]["hypothesis_count"] >= 1
    diagnose = by_agent["diagnose"]
    assert diagnose["status"] == AgentInvocationStatus.executed.value
    assert diagnose["execution"]["confirmation"] == "unverified"
    assert diagnose["execution"]["evidence_verified"] is False
    fortify = by_agent["fortify"]
    assert fortify["status"] == AgentInvocationStatus.skipped_no_prerequisite
    trace = by_agent.get("trace")
    if trace:
        assert trace["status"] == AgentInvocationStatus.skipped_no_prerequisite.value
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions
    assert decisions[0]["executed"] is False
    assert decisions[0]["confirmation"] == "unverified"
    assert decisions[0]["predicted_probability"] is None
    assert decisions[0]["evidence_state"] != EvidenceLifecycleState.verified.value


def test_probe_is_not_expanded_with_active_scope(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "assisted_response"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(utcnow()),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    probe = next(item for item in invocations if item["agent"] == "probe")
    assert probe["status"] == AgentInvocationStatus.completed_unverified.value
    assert probe["provider_mode"] == "none"
    assert probe["execution"] is None
    patch = next(item for item in invocations if item["agent"] == "patch")
    assert patch["worktree_only"] is True
    assert patch["silent_deploy"] is False
    assert patch["status"] in {
        AgentInvocationStatus.skipped_no_approval.value,
        AgentInvocationStatus.blocked.value,
    }
    auto = alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "auto_execute"}
    )
    assert auto.status_code in {400, 422}


def test_missing_llm_fails_honestly_not_verified(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps([{"repo_id": rid, "local_path": str(repo), "subdir": "."}]))
    monkeypatch.setenv("SECURITY_AGENTS_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setenv("SECURITY_AGENTS_LABELS", str(PAYMENTS / "labels.yaml"))
    settings_override(
        repository_bindings_file=str(bindings),
        security_sandbox="local-dev",
        security_workdir=str(tmp_path / "work"),
        anthropic_api_key="",
    )
    monkeypatch.setattr(llm, "TRANSPORT_OVERRIDE", None)
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(utcnow()),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    scout = next(item for item in invocations if item["agent"] == "scout")
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["error_code"] == "LLM_UNAVAILABLE"
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["confirmation"] == ConfirmationState.unverified.value
    diagnose = next(item for item in invocations if item["agent"] == "diagnose")
    assert diagnose["status"] == AgentInvocationStatus.skipped_no_prerequisite.value


def test_no_snapshot_still_skips_honestly(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": ids["repo"],
            "commit_sha": "b" * 40,
            "deployed_at": utcnow().isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    scout = next(item for item in invocations if item["agent"] == "scout")
    assert scout["status"] == AgentInvocationStatus.skipped_no_snapshot.value
    assert scout["provider_mode"] == "none"
    assert scout["execution"] is None
