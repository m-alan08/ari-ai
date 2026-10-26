"""Regression tests for the pilot security-hardening fixes.

Each test names the defect it proves closed. All run against the real API,
real PostgreSQL, and real session/CSRF plumbing — no mocked boundaries.
"""

import sqlalchemy as sa
from helpers import post_webhook, push_payload, setup_project

from backend import jobs as jobmod
from backend import worker
from backend.api.auth import upsert_user
from backend.api.deps import SESSION_COOKIE, create_session
from backend.artifacts import ArtifactStore
from backend.db import jobs, memberships, repositories, transaction, users
from shared.evidence import ArtifactKind


def _session_client(client, login: str, github_id: int | None = None) -> str:
    """Create a user + session directly (works under any APP_ENV, unlike dev login)."""
    with transaction() as conn:
        uid = upsert_user(conn, login, None, github_id)
        token, csrf = create_session(conn, uid)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = csrf
    return uid


class TestAccountIdentity:
    """P1: upsert_user matched on mutable github_login — a freed login let a
    different GitHub account inherit the victim's user row, orgs, and projects."""

    def test_reused_login_cannot_take_over_account(self):
        with transaction() as conn:
            victim = upsert_user(conn, "victim", None, 111)
            attacker = upsert_user(conn, "victim", None, 222)
            assert attacker != victim
            rows = (
                conn.execute(sa.select(users.c.id, users.c.github_login, users.c.github_id))
                .mappings()
                .all()
            )
            by_id = {r["id"]: r for r in rows}
            # The victim's row keeps its immutable github_id; the stale login is freed.
            assert by_id[victim]["github_id"] == 111
            assert by_id[victim]["github_login"] != "victim"
            assert by_id[attacker]["github_login"] == "victim"
            assert by_id[attacker]["github_id"] == 222

    def test_github_id_survives_rename_and_reclaims_login(self):
        with transaction() as conn:
            victim = upsert_user(conn, "old-name", None, 111)
            # GitHub-side rename: same immutable id, new login.
            again = upsert_user(conn, "new-name", None, 111)
            assert again == victim
            row = conn.execute(sa.select(users.c.github_login).where(users.c.id == victim)).scalar()
            assert row == "new-name"

    def test_dev_created_user_links_first_github_id(self):
        # Rows with no github_id yet (dev login) still bind on first OAuth sign-in.
        with transaction() as conn:
            uid = upsert_user(conn, "dev-user", None, None)
            assert upsert_user(conn, "dev-user", None, 777) == uid
            assert (
                conn.execute(sa.select(users.c.github_id).where(users.c.id == uid)).scalar() == 777
            )


class TestRepositoryConnect:
    """P1/P2: installation_id=None connected any org/repo name with no proof of
    access and permanently squatted the globally-unique name (409 for the owner)."""

    def test_name_only_connect_rejected_outside_dev(self, settings_override, client_factory):
        settings_override(app_env="staging")
        c = client_factory()
        _session_client(c, "prod-owner")
        p = c.post("/api/v1/projects", json={"name": "p", "settings": {}})
        assert p.status_code == 201, p.text
        r = c.post(
            f"/api/v1/projects/{p.json()['id']}/repositories",
            json={"full_name": "acme/other-repo", "installation_id": None},
        )
        assert r.status_code == 422
        with transaction() as conn:
            assert conn.execute(sa.select(repositories)).first() is None

    def test_name_only_connect_still_allowed_in_dev(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        assert ids["repo"]


class TestRateLimits:
    """P2: require_user-authed routes outside routes.py had no rate limit, and
    expensive job enqueues were unbounded."""

    def test_global_user_limit_applies_on_all_routers(self, settings_override, client_factory):
        # #607: reads are limited per-user via require_user on every router.
        settings_override(rate_limit_read_per_minute="3")
        c = client_factory("limited")
        for _ in range(3):
            assert c.get("/api/v1/projects").status_code == 200
        r = c.get("/api/v1/projects")
        assert r.status_code == 429

    def test_snapshot_requests_are_bounded(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        for i in range(30):
            r = alice.post(
                f"/api/v1/repositories/{ids['repo']}/snapshots",
                json={"commit_sha": f"{i:040x}"},
            )
            assert r.status_code == 202, (i, r.text)
        r = alice.post(
            f"/api/v1/repositories/{ids['repo']}/snapshots", json={"commit_sha": "f" * 40}
        )
        assert r.status_code == 429


class TestRepositoryDisconnect:
    """P2: there was no way to disconnect a repository — monitoring, snapshots,
    assessments, and live reads ran until the App was uninstalled on GitHub."""

    def test_disconnect_blocks_repo_scoped_work_and_reconnect_restores(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        eve = client_factory("eve")
        ids = setup_project(alice, bob)
        rid = ids["repo"]

        # Non-member cannot disconnect (existence is not leaked).
        assert eve.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 404

        d = alice.post(f"/api/v1/repositories/{rid}/disconnect")
        assert d.status_code == 200 and d.json()["disconnected"] is True

        r = alice.post(f"/api/v1/repositories/{rid}/snapshots", json={"commit_sha": "c" * 40})
        assert r.status_code == 409

        a = alice.post(
            f"/api/v1/projects/{ids['project']}/assessments",
            json={"repo_id": rid, "scope_id": ids["scope"], "commit_sha": "c" * 40},
        )
        assert a.status_code == 409

        commits = alice.get(f"/api/v1/repositories/{rid}/commits")
        assert commits.status_code == 200
        assert commits.json()["available"] is False
        assert "disconnected" in commits.json()["reason"].lower()

        rc = alice.post(f"/api/v1/repositories/{rid}/reconnect")
        assert rc.status_code == 200 and rc.json()["disconnected"] is False
        r = alice.post(f"/api/v1/repositories/{rid}/snapshots", json={"commit_sha": "c" * 40})
        assert r.status_code == 202

    def test_disconnect_stops_push_automation(self, settings_override, client_factory):
        settings_override(
            github_auto_commit_authors="dev",
            automatic_assessments_per_hour="100",
            automatic_assessments_per_day="100",
        )
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob, approve=False)
        alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect")
        assert post_webhook(alice, push_payload(["README.md"])).status_code == 202
        worker.drain(kinds=["process_github_delivery"])
        with transaction() as conn:
            assert (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(jobs)
                    .where(jobs.c.kind == "assess_github_commit")
                ).scalar()
                == 0
            )

    def test_assess_commit_job_exits_when_repo_disconnected(
        self, settings_override, client_factory
    ):
        settings_override(github_auto_commit_authors="dev")
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob, approve=False)
        alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect")
        with transaction() as conn:
            jobmod.enqueue(
                conn,
                "assess_github_commit",
                {
                    "repo_id": ids["repo"],
                    "installation_id": None,
                    "sha": "b" * 40,
                    "sender": "dev",
                    "payload_artifact_id": None,
                    "ref": "refs/heads/main",
                    "event_type": "push",
                    "pr_number": None,
                    "occurred_at": "2026-10-24T00:00:00Z",
                    "received_at": "2026-10-24T00:00:00Z",
                },
                dedupe_key="test:disconnect-exit",
            )
        worker.drain(kinds=["assess_github_commit"])
        with transaction() as conn:
            status = conn.execute(
                sa.select(jobs.c.status).where(jobs.c.dedupe_key == "test:disconnect-exit")
            ).scalar()
        assert status == "succeeded"


class TestStaleAuthorization:
    """P2: access decisions must follow live membership, not session-era state."""

    def test_removed_member_loses_access_immediately(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        eve = client_factory("eve")
        # eve joins alice's org as viewer, reads the project, then is removed.
        with transaction() as conn:
            conn.execute(
                sa.insert(memberships).values(
                    org_id=ids["org"], user_id=eve.me["user"]["id"], role="viewer"
                )
            )
        assert eve.get(f"/api/v1/projects/{ids['project']}").status_code == 200
        with transaction() as conn:
            conn.execute(
                sa.delete(memberships).where(
                    memberships.c.user_id == eve.me["user"]["id"],
                    memberships.c.org_id == ids["org"],
                )
            )
        assert eve.get(f"/api/v1/projects/{ids['project']}").status_code == 404
        assert eve.get(f"/api/v1/repositories/{ids['repo']}").status_code == 404


class TestDataHandling:
    """P3: artifact redacted flag must reflect reality; API schema is dev-only;
    webhook signatures and CSRF stay enforced."""

    def test_raw_bytes_artifact_is_not_flagged_redacted(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        store = ArtifactStore()
        with transaction() as conn:
            raw = store.put_bytes(
                conn, b"binary-bytes", kind=ArtifactKind.report, project_id=ids["project"]
            )
            text = store.put_text(
                conn, "ghp_" + "x" * 36, kind=ArtifactKind.report, project_id=ids["project"]
            )
            rows = {
                r["id"]: r["redacted"]
                for r in conn.execute(sa.text("SELECT id, redacted FROM artifacts")).mappings()
            }
        assert raw.redacted is False and rows[raw.id] is False
        assert text.redacted is True and rows[text.id] is True

    def test_cross_project_artifact_access_denied(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        eve = client_factory("eve")
        ids = setup_project(alice, bob)
        store = ArtifactStore()
        with transaction() as conn:
            ref = store.put_json(
                conn, {"k": "v"}, kind=ArtifactKind.report, project_id=ids["project"]
            )
        r = eve.get(f"/api/v1/artifacts/{ref.id}")
        assert r.status_code == 404
        assert alice.get(f"/api/v1/artifacts/{ref.id}").status_code == 200

    def test_openapi_schema_hidden_outside_dev(self, settings_override, client_factory):
        settings_override(app_env="staging")
        c = client_factory()
        assert c.get("/api/openapi.json").status_code == 404

    def test_invalid_webhook_signature_rejected(self, client_factory):
        c = client_factory()
        r = post_webhook(c, push_payload(["x.py"]), signature="sha256=" + "0" * 64)
        assert r.status_code == 401

    def test_csrf_required_for_mutations(self, client_factory):
        c = client_factory("csrfuser")
        del c.headers["X-CSRF-Token"]
        assert c.post("/api/v1/projects", json={"name": "x", "settings": {}}).status_code == 403

    def test_unauthenticated_requests_rejected(self, client_factory):
        c = client_factory()
        assert c.get("/api/v1/projects").status_code == 401


class TestDisconnectLifecycle:
    """Canonical disconnect model: unambiguous state, revoked app-side access,
    cancelled pending work, cooperative cancel of in-flight investigations,
    audit history preserved, and reconnect only after renewed authorization."""

    def test_disconnect_revokes_app_access_cancels_jobs_and_inflight(self, client_factory):
        from backend.db import (
            assessments,
            audit_events,
            connected_applications,
            deploy_commit_mappings,
            snapshots,
        )

        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        rid, pid = ids["repo"], ids["project"]
        uid = alice.me["user"]["id"]

        with transaction() as conn:
            conn.execute(
                sa.insert(connected_applications).values(
                    id="app-disconnect-1",
                    project_id=pid,
                    name="staging",
                    environment="owned_test",
                    created_by=uid,
                    doc={},
                )
            )
            conn.execute(
                sa.insert(deploy_commit_mappings).values(
                    id="map-disconnect-1",
                    project_id=pid,
                    application_id="app-disconnect-1",
                    repo_id=rid,
                    commit_sha="a" * 40,
                    created_by=uid,
                    doc={},
                )
            )
            conn.execute(
                sa.insert(snapshots).values(
                    id="snap-disconnect-1",
                    repo_id=rid,
                    commit_sha="b" * 40,
                    index_version="v1",
                    status="pending",
                    doc={},
                )
            )
            jobmod.enqueue(conn, "propose_build_recipe", {"repo_id": rid}, dedupe_key="t:recipe")
            jobmod.enqueue(
                conn,
                "index_snapshot",
                {"snapshot_id": "snap-disconnect-1"},
                dedupe_key="t:index",
            )
            jobmod.enqueue(
                conn,
                "assess_github_commit",
                {"repo_id": rid, "sha": "c" * 40},
                dedupe_key="t:assess",
            )

        a = alice.post(
            f"/api/v1/projects/{pid}/assessments",
            json={"repo_id": rid, "scope_id": ids["scope"], "commit_sha": "c" * 40},
        )
        assert a.status_code == 202, a.text
        asm_id = a.json()["assessment"]["id"]

        d = alice.post(f"/api/v1/repositories/{rid}/disconnect")
        assert d.status_code == 200

        with transaction() as conn:
            row = conn.execute(
                sa.select(
                    deploy_commit_mappings.c.revoked_at,
                    deploy_commit_mappings.c.id,
                ).where(deploy_commit_mappings.c.id == "map-disconnect-1")
            ).one()
            assert row.revoked_at is not None

            for kind in ("propose_build_recipe", "index_snapshot", "assess_github_commit"):
                status = conn.execute(
                    sa.select(jobs.c.status).where(jobs.c.kind == kind)
                ).scalar_one()
                assert status == "cancelled", f"{kind} was {status}"

            snap_status = conn.execute(
                sa.select(snapshots.c.status).where(snapshots.c.id == "snap-disconnect-1")
            ).scalar_one()
            assert snap_status == "failed"

            asm_state = conn.execute(
                sa.select(assessments.c.state).where(assessments.c.id == asm_id)
            ).scalar_one()
            assert asm_state == "CANCELLED"

            event = conn.execute(
                sa.select(audit_events.c.doc).where(
                    audit_events.c.doc["action"].astext == "repository.disconnected",
                    audit_events.c.doc["subject_id"].astext == rid,
                )
            ).scalar_one()
            details = event["details"]
            assert details["queued_jobs_cancelled"] == 3
            assert details["deploy_mappings_revoked"] == 1
            assert details["assessments_cancelled"] == 1

        # Unambiguous state is visible through the typed Repository contract.
        repo = alice.get(f"/api/v1/repositories/{rid}").json()
        assert repo["disconnected_at"] is not None

    def test_installation_id_survives_disconnect_as_access_record(self, client_factory):
        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        rid = ids["repo"]
        with transaction() as conn:
            conn.execute(
                sa.update(repositories).where(repositories.c.id == rid).values(installation_id=42)
            )
        alice.post(f"/api/v1/repositories/{rid}/disconnect")
        with transaction() as conn:
            kept = conn.execute(
                sa.select(repositories.c.installation_id).where(repositories.c.id == rid)
            ).scalar_one()
        assert kept == 42

        # Reconnect requires renewed authorization: without an access grant on
        # installation 42 the owner is denied (and stays disconnected).
        r = alice.post(f"/api/v1/repositories/{rid}/reconnect")
        assert r.status_code == 403
        repo = alice.get(f"/api/v1/repositories/{rid}").json()
        assert repo["disconnected_at"] is not None

    def test_disconnect_cancels_linked_agent_steps_and_running_jobs(self, client_factory):
        """R2: the sweep must reach live-defense agent steps through the trusted
        repo<->mapping linkage (app -> mapping -> repo), not just repo_id payloads,
        and running steps get cooperative cancel, not silent continuation."""
        from backend.db import (
            connected_applications,
            deploy_commit_mappings,
            first_responder_decisions,
        )
        from backend.services import repository_revocation

        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        rid, pid = ids["repo"], ids["project"]
        uid = alice.me["user"]["id"]

        with transaction() as conn:
            conn.execute(
                sa.insert(connected_applications).values(
                    id="app-r2-1",
                    project_id=pid,
                    name="staging",
                    environment="owned_test",
                    created_by=uid,
                    doc={},
                )
            )
            conn.execute(
                sa.insert(deploy_commit_mappings).values(
                    id="map-r2-1",
                    project_id=pid,
                    application_id="app-r2-1",
                    repo_id=rid,
                    commit_sha="a" * 40,
                    created_by=uid,
                    doc={},
                )
            )
            conn.execute(
                sa.insert(first_responder_decisions).values(
                    id="dec-r2-1",
                    project_id=pid,
                    application_id="app-r2-1",
                    dedupe_key="t:dec-r2-1",
                    action="investigate",
                    doc={},
                )
            )
            jobmod.enqueue(
                conn,
                "live_defense_first_responder",
                {"project_id": pid, "application_id": "app-r2-1"},
                dedupe_key="t:fr-parent",
            )
            jobmod.enqueue(
                conn,
                "live_defense_agent_step",
                {"project_id": pid, "decision_id": "dec-r2-1", "agent": "scout"},
                dedupe_key="t:fr-step",
            )
            running_id, _ = jobmod.enqueue(
                conn,
                "live_defense_agent_step",
                {"project_id": pid, "decision_id": "dec-r2-1", "agent": "trace"},
                dedupe_key="t:fr-step-running",
            )
            conn.execute(sa.update(jobs).where(jobs.c.id == running_id).values(status="running"))
            # An unrelated app's work in the same project must survive.
            jobmod.enqueue(
                conn,
                "live_defense_agent_step",
                {"project_id": pid, "decision_id": "dec-other", "agent": "scout"},
                dedupe_key="t:fr-step-other",
            )

        alice.post(f"/api/v1/repositories/{rid}/disconnect")

        with transaction() as conn:
            by_dedupe = {
                r.dedupe_key: r
                for r in conn.execute(
                    sa.select(jobs.c.dedupe_key, jobs.c.status, jobs.c.cancel_requested)
                ).mappings()
            }
            assert by_dedupe["t:fr-parent"]["status"] == "cancelled"
            assert by_dedupe["t:fr-step"]["status"] == "cancelled"
            running = by_dedupe["t:fr-step-running"]
            assert running["status"] == "running" and running["cancel_requested"] is True
            assert by_dedupe["t:fr-step-other"]["status"] == "queued"

            # The canonical callback check reports both revocation layers.
            assert repository_revocation(conn, repo_id=rid) == "repository_disconnected"
            assert (
                repository_revocation(conn, repo_id=rid, application_id="app-r2-1")
                == "repository_disconnected"
            )
            assert (
                repository_revocation(conn, repo_id=None, mapping_id="map-r2-1")
                == "deploy_mapping_revoked"
            )
            assert repository_revocation(conn, repo_id="rep_does_not_exist") is None

    def test_revocation_locked_serializes_finalize_with_disconnect(self, client_factory):
        """N1: the canonical finalization check takes repo-then-mapping FOR UPDATE
        locks (same order as disconnect) so a success outcome can never persist
        after disconnect commits — whichever side commits first wins."""
        from backend.db import connected_applications, deploy_commit_mappings
        from backend.services import repository_revocation_locked

        alice, bob = client_factory("alice"), client_factory("bob")
        ids = setup_project(alice, bob)
        rid, pid = ids["repo"], ids["project"]
        uid = alice.me["user"]["id"]

        with transaction() as conn:
            conn.execute(
                sa.insert(connected_applications).values(
                    id="app-n1-1",
                    project_id=pid,
                    name="staging",
                    environment="owned_test",
                    created_by=uid,
                    doc={},
                )
            )
            conn.execute(
                sa.insert(deploy_commit_mappings).values(
                    id="map-n1-1",
                    project_id=pid,
                    application_id="app-n1-1",
                    repo_id=rid,
                    commit_sha="a" * 40,
                    created_by=uid,
                    doc={},
                )
            )
            # Pre-disconnect: the locked check passes and takes both locks.
            assert (
                repository_revocation_locked(
                    conn, repo_id=rid, application_id="app-n1-1", mapping_id="map-n1-1"
                )
                is None
            )

        alice.post(f"/api/v1/repositories/{rid}/disconnect")

        with transaction() as conn:
            # Post-disconnect commit: the same locked read a finalizer performs
            # sees revocation — an 'executed' outcome cannot be written.
            assert (
                repository_revocation_locked(conn, repo_id=rid, application_id="app-n1-1")
                == "repository_disconnected"
            )
            assert (
                repository_revocation_locked(conn, repo_id=None, mapping_id="map-n1-1")
                == "deploy_mapping_revoked"
            )
