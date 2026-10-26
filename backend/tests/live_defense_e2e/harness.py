"""Reusable owned-staging environment for Live Defense end-to-end tests.

`OwnedStaging` drives the real first-responder pipeline end to end: real HTTP
routes (TestClient against the FastAPI app), the real PostgreSQL database, the
real durable job queue (`worker.drain`), the real `security_agents` runtime, and
the real artifact store. Only the model is scripted (FakeLLM /
TEST-ONLY gateways), and every record it produces is labelled `provider_mode`
`fake`. Nothing here may be presented as live-provider evidence.

Deliberately decoupled from Phase 7C internals: the harness talks to the public
API, the jobs table, and the artifact store, so later phases can reuse it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from helpers import add_member

from backend import worker
from backend.artifacts import ArtifactStore
from backend.db import (
    analyst_handoffs,
    artifacts,
    audit_events,
    authorized_scopes,
    first_responder_decisions,
    first_responder_invocations,
    jobs,
    monitoring_events,
    runtime_windows,
    snapshots,
    transaction,
)
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.intelligence import Snapshot, SnapshotStatus
from shared.live_defense import FIRST_RESPONDER_JOB_KINDS

REPO_ROOT = Path(__file__).resolve().parents[3]
PAYMENTS = REPO_ROOT / "examples" / "payments-lite"
DEFAULT_ROUTE = "GET /payments/{payment_id}"
DEFAULT_KINDS = ["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=e2e-harness",
            "-c",
            "user.email=e2e@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        text=True,
    ).strip()


@dataclass
class OwnedApp:
    """One project + repo + connected application under test."""

    owner: Any
    project_id: str
    repo_id: str
    scope_id: str | None
    org_id: str
    app: dict[str, Any] = field(default_factory=dict)
    token: str | None = None
    repo_path: Path | None = None
    commit_sha: str | None = None
    mapping_id: str | None = None


class OwnedStaging:
    """Builds an isolated owned-staging environment inside the test database."""

    def __init__(
        self,
        client_factory: Any,
        settings_override: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self.client_factory = client_factory
        self.settings_override = settings_override
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.clients: dict[str, Any] = {}
        self.apps: list[OwnedApp] = []
        self._added_members: set[tuple[str, str]] = set()
        self._bindings: list[dict[str, Any]] = []
        self._bindings_file = tmp_path / "bindings.json"
        self._bindings_file.write_text("[]")
        monkeypatch.setenv("SECURITY_AGENTS_WORKDIR", str(tmp_path / "work"))
        monkeypatch.setenv("SECURITY_AGENTS_LABELS", str(PAYMENTS / "labels.yaml"))
        settings_override(
            repository_bindings_file=str(self._bindings_file),
            security_sandbox="local-dev",
            security_workdir=str(tmp_path / "work"),
        )

    # -- users ---------------------------------------------------------------

    def login(self, name: str) -> Any:
        if name not in self.clients:
            self.clients[name] = self.client_factory(name)
        return self.clients[name]

    # -- environment pieces ---------------------------------------------------

    def use_fake_llm(self) -> None:
        """Route first-responder LLM calls to the scripted FakeLLM path."""
        from backend import llm

        self.monkeypatch.setattr(llm, "TRANSPORT_OVERRIDE", lambda: object())

    def use_gateway(self, factory: Any) -> None:
        """Install a custom first-responder gateway (failure injection).

        ``factory`` is a callable returning an LLMGateway-compatible object and
        replaces ``backend.live_defense.agent_execution.GATEWAY_OVERRIDE``.
        """
        from backend.live_defense import agent_execution

        self.monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", factory)

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        return _git(repo, *args)

    def checkout_owned_repo(
        self, name: str, extra_commit: dict[str, str] | None = None
    ) -> tuple[Path, str]:
        """Copy the owned vulnerable app into a git repo and return (path, sha).

        ``extra_commit`` maps a relative file path to new content; when given, a
        second commit is added so the caller can build stale/current commit
        pairs for snapshot-attribution scenarios.
        """
        repo = self.tmp_path / name
        shutil.copytree(
            PAYMENTS,
            repo,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info", ".venv"),
        )
        _git(repo, "init", "-q")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "payments-lite")
        if extra_commit:
            for rel, content in extra_commit.items():
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "staging change")
        return repo, _git(repo, "rev-parse", "HEAD")

    def create_project(
        self,
        owner: Any,
        approver: Any,
        *,
        name: str = "payments-lite",
        repo_name: str | None = None,
        approve_scope: bool = True,
    ) -> OwnedApp:
        """Owner creates a project with a connected repo and a scope the approver signs."""
        org_id = owner.me["memberships"][0]["organization"]["id"]
        approver_id = approver.me["user"]["id"]
        if (org_id, approver_id) not in self._added_members:
            add_member(org_id, approver_id)
            self._added_members.add((org_id, approver_id))
        repo_name = repo_name or f"acme/{name}"
        p = owner.post(
            "/api/v1/projects",
            json={
                "name": name,
                "settings": {"monitoring_policy": {"enabled": True}, "delivery_mode": "manual"},
            },
        )
        assert p.status_code == 201, p.text
        pid = p.json()["id"]
        r = owner.post(f"/api/v1/projects/{pid}/repositories", json={"full_name": repo_name})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        expires = (utcnow() + timedelta(days=7)).isoformat()
        s = owner.post(
            f"/api/v1/projects/{pid}/scopes",
            json={
                "name": "sandbox",
                "expires_at": expires,
                "targets": [
                    {
                        "kind": "sandbox_build",
                        "repo_id": rid,
                        "build_recipe_ref": "examples/payments-lite/sandbox.yaml",
                        "exposed_ports": [8080],
                    }
                ],
                "allowed_check_categories": ["authorization.ownership"],
            },
        )
        assert s.status_code == 201, s.text
        sid = s.json()["id"]
        if approve_scope:
            a = approver.post(f"/api/v1/scopes/{sid}/approve")
            assert a.status_code == 200, a.text
        app = OwnedApp(owner=owner, project_id=pid, repo_id=rid, scope_id=sid, org_id=org_id)
        self.apps.append(app)
        return app

    def bind_repo(self, app: OwnedApp, repo_path: Path, subdir: str = ".") -> None:
        self._bindings.append(
            {"repo_id": app.repo_id, "local_path": str(repo_path), "subdir": subdir}
        )
        self._bindings_file.write_text(json.dumps(self._bindings))
        app.repo_path = repo_path

    def register_app(self, app: OwnedApp, name: str = "owned staging") -> dict[str, Any]:
        resp = app.owner.post(
            f"/api/v1/projects/{app.project_id}/applications",
            json={"name": name, "environment": "owned_test"},
        )
        assert resp.status_code == 201, resp.text
        app.app = resp.json()
        return app.app

    def issue_token(self, app: OwnedApp) -> str:
        resp = app.owner.post(f"/api/v1/applications/{app.app['id']}/ingest-tokens")
        assert resp.status_code in {200, 201}, resp.text
        app.token = resp.json()["token"]
        return app.token

    def attest_mapping(
        self,
        app: OwnedApp,
        *,
        commit_sha: str,
        deployed_at: Any | None = None,
        source: str = "owner_attested",
    ) -> dict[str, Any]:
        resp = app.owner.post(
            f"/api/v1/applications/{app.app['id']}/deploy-mappings",
            json={
                "repo_id": app.repo_id,
                "commit_sha": commit_sha,
                "deployed_at": (deployed_at or (utcnow() - timedelta(minutes=5))).isoformat(),
                "source": source,
            },
        )
        assert resp.status_code == 201, resp.text
        app.mapping_id = resp.json()["id"]
        app.commit_sha = commit_sha
        return resp.json()

    def index_snapshot(self, repo_id: str, commit_sha: str, *, status: str = "ready") -> str:
        """Insert a valid snapshot row, standing in for a completed index job."""
        snap = Snapshot(
            id=new_id(IdPrefix.snp),
            repo_id=repo_id,
            commit_sha=commit_sha,
            status=SnapshotStatus(status),
            index_version="e2e-live-defense",
            created_at=utcnow(),
        )
        with transaction() as conn:
            conn.execute(
                sa.insert(snapshots).values(
                    id=snap.id,
                    repo_id=repo_id,
                    commit_sha=commit_sha,
                    index_version=snap.index_version,
                    status=snap.status.value,
                    doc=snap.model_dump(mode="json"),
                )
            )
        return snap.id

    def set_mode(self, client: Any, project_id: str, intent: str) -> Any:
        return client.post(
            f"/api/v1/projects/{project_id}/first-responder/mode", json={"intent": intent}
        )

    # -- telemetry ------------------------------------------------------------

    @staticmethod
    def telemetry_batch(
        end: Any, requests: int = 20, e4: int = 18, auth: int = 18
    ) -> dict[str, Any]:
        return {
            "source": "owned_staging",
            "window_start": (end - timedelta(minutes=1)).isoformat(),
            "window_end": end.isoformat(),
            "routes": [
                {
                    "route": DEFAULT_ROUTE,
                    "requests": requests,
                    "status_4xx": e4,
                    "auth_failures": auth,
                }
            ],
        }

    def ingest(
        self,
        client: Any,
        app: OwnedApp,
        batch: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> Any:
        return client.post(
            f"/api/v1/ingest/runtime/{app.project_id}",
            json=batch or self.telemetry_batch(utcnow()),
            headers={"Authorization": f"Bearer {token if token is not None else app.token}"},
        )

    # -- execution ------------------------------------------------------------

    @staticmethod
    def drain(max_jobs: int = 60, kinds: list[str] | None = None) -> list[str]:
        return worker.drain(max_jobs=max_jobs, kinds=kinds or DEFAULT_KINDS)

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def invocations(client: Any, project_id: str) -> list[dict[str, Any]]:
        return client.get(f"/api/v1/projects/{project_id}/first-responder/invocations").json()[
            "items"
        ]

    @staticmethod
    def decisions(client: Any, project_id: str) -> list[dict[str, Any]]:
        return client.get(f"/api/v1/projects/{project_id}/first-responder/decisions").json()[
            "items"
        ]

    @staticmethod
    def handoffs(client: Any, project_id: str) -> list[dict[str, Any]]:
        return client.get(f"/api/v1/projects/{project_id}/first-responder/handoffs").json()["items"]

    @staticmethod
    def by_agent(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {item["agent"]: item for item in items}

    @staticmethod
    def artifact_json(artifact_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read an artifact back through the store; verifies its sha256."""
        with transaction() as conn:
            meta, data = ArtifactStore().get(conn, artifact_id)
        return meta, json.loads(data.decode())

    @staticmethod
    def artifact_rows(
        project_id: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        stmt = sa.select(artifacts)
        if project_id:
            stmt = stmt.where(artifacts.c.project_id == project_id)
        if kind:
            stmt = stmt.where(artifacts.c.kind == kind)
        with transaction() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]

    @staticmethod
    def job_rows(kind: str | None = None) -> list[dict[str, Any]]:
        stmt = sa.select(jobs)
        if kind:
            stmt = stmt.where(jobs.c.kind == kind)
        with transaction() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]

    @staticmethod
    def table_count(table: Any, project_id: str | None = None) -> int:
        stmt = sa.select(sa.func.count()).select_from(table)
        if project_id is not None and "project_id" in table.c:
            stmt = stmt.where(table.c.project_id == project_id)
        with transaction() as conn:
            return int(conn.execute(stmt).scalar() or 0)

    @staticmethod
    def requeue_now(job_id: str) -> None:
        """Make a backoff-queued job claimable immediately (failure-injection tests)."""
        with transaction() as conn:
            conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(run_after=sa.func.now()))

    @staticmethod
    def revive_job(job_id: str) -> None:
        """Re-deliver an already-finished job (redelivery / at-least-once tests)."""
        with transaction() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="queued", run_after=sa.func.now(), attempts=0)
            )

    @staticmethod
    def expire_lease(job_id: str) -> None:
        """Expire a running job's lease using the database clock (recovery tests).

        The reconciler compares ``lease_expires_at`` against ``now()`` inside the
        transaction — backdating with the DB's own clock (not Python utcnow())
        makes expiry unequivocal (review finding F10).
        """
        with transaction() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(lease_expires_at=sa.func.now() - sa.text("interval '1 hour'"))
            )

    @staticmethod
    def expire_scopes(project_id: str) -> int:
        """Expire every AuthorizedScope of a project (invalid-authorization tests)."""
        with transaction() as conn:
            res = conn.execute(
                sa.update(authorized_scopes)
                .where(authorized_scopes.c.project_id == project_id)
                .values(expires_at=utcnow() - timedelta(hours=1))
            )
            return int(res.rowcount or 0)

    @staticmethod
    def artifact_path(artifact_id: str) -> Path:
        """Filesystem path of the artifact's stored bytes (integrity tests)."""
        with transaction() as conn:
            key = conn.execute(
                sa.select(artifacts.c.storage_key).where(artifacts.c.id == artifact_id)
            ).scalar_one()
        return ArtifactStore().root / key

    @staticmethod
    def audit_actions(project_id: str) -> list[str]:
        stmt = sa.select(audit_events.c.doc["action"].as_string()).where(
            audit_events.c.project_id == project_id
        )
        with transaction() as conn:
            return [r[0] for r in conn.execute(stmt).all() if r[0]]

    # table handles kept here so scenario files stay free of db imports
    T = type(
        "T",
        (),
        {
            "runtime_windows": runtime_windows,
            "monitoring_events": monitoring_events,
            "first_responder_decisions": first_responder_decisions,
            "first_responder_invocations": first_responder_invocations,
            "analyst_handoffs": analyst_handoffs,
            "audit_events": audit_events,
        },
    )
