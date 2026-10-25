"""Accepted assessment job identifiers must resolve with project authorization."""

from helpers import HEAD, setup_project


def test_assessment_job_is_readable_only_by_its_project_members(client_factory):
    alice, bob, eve = client_factory("alice"), client_factory("bob"), client_factory("eve")
    ids = setup_project(alice, bob)
    accepted = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": HEAD},
    )
    assert accepted.status_code == 202, accepted.text
    url = f"/api/v1/jobs/{accepted.json()['job_id']}"
    for member in (alice, bob):
        response = member.get(url)
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["id"] == accepted.json()["job_id"]
        assert job["kind"] == "run_assessment" and job["status"] == "queued"
    assert eve.get(url).status_code == 404
    assert client_factory().get(url).status_code == 401


def test_project_jobs_list_surfaces_dead_and_failed(client_factory):
    """Dead-letter visibility: members can list a project's jobs filtered by status."""
    import sqlalchemy as sa

    from backend import jobs as jobq
    from backend.db import jobs as jobs_t
    from backend.db import transaction

    alice, bob = client_factory("alice"), client_factory("bob")
    eve = client_factory("eve")
    ids = setup_project(alice, bob)
    with transaction() as conn:
        dead_id, _ = jobq.enqueue(
            conn, "probe_dead", {"project_id": ids["project"]}, dedupe_key="vis-dead"
        )
        conn.execute(sa.update(jobs_t).where(jobs_t.c.id == dead_id).values(status="dead"))
        ok_id, _ = jobq.enqueue(
            conn, "probe_ok", {"project_id": ids["project"]}, dedupe_key="vis-ok"
        )
        jobq.enqueue(conn, "probe_other", {"project_id": "proj_other"}, dedupe_key="vis-other")

    url = f"/api/v1/projects/{ids['project']}/jobs"
    all_jobs = alice.get(url).json()["items"]
    got_ids = {j["id"] for j in all_jobs}
    assert {dead_id, ok_id} <= got_ids
    assert all(j["kind"] != "probe_other" for j in all_jobs)

    dead_only = alice.get(url, params={"status": "dead,failed"}).json()["items"]
    assert {j["id"] for j in dead_only} == {dead_id}
    assert dead_only[0]["status"] == "dead"

    assert eve.get(url).status_code == 404
    assert client_factory().get(url).status_code == 401


def test_project_jobs_list_scrubs_secret_values_in_last_error(client_factory):
    """A secret embedded in last_error.message must never surface via the list."""
    import sqlalchemy as sa

    from backend import jobs as jobq
    from backend.db import jobs as jobs_t
    from backend.db import transaction

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    marker = "ghp_" + "secretmarker" + "0" * 20
    with transaction() as conn:
        jid, _ = jobq.enqueue(
            conn, "probe_leak", {"project_id": ids["project"]}, dedupe_key="vis-leak"
        )
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(
                status="dead",
                last_error={
                    "code": "TOOL_ERROR",
                    "message": f"call failed: {marker} rejected",
                    "trace": "full traceback",
                },
            )
        )

    items = alice.get(f"/api/v1/projects/{ids['project']}/jobs", params={"status": "dead"}).json()[
        "items"
    ]
    err = next(j["last_error"] for j in items if j["id"] == jid)
    assert marker not in err["message"]
    assert "[REDACTED:github_token]" in err["message"]
    assert "trace" not in err


def test_fail_scrubs_secrets_at_write_time(client_factory):
    """Secrets in a failure message never persist in jobs.last_error."""
    import sqlalchemy as sa

    from backend import jobs as jobq
    from backend.db import jobs as jobs_t
    from backend.db import transaction

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    key = "sk-ant-api03-" + "secretmarker" + "0" * 40
    dsn = "post" + "gresql://svc_user" + ":" + "hunter2pass" + "@db.internal:5432/app"
    kv = "pass" + "word=" + "hunter" + "2"
    with transaction() as conn:
        jid, _ = jobq.enqueue(
            conn, "probe_leak", {"project_id": ids["project"]}, dedupe_key="wr-leak"
        )
        job = jobq.claim(conn, owner="w1", lease_s=60, kinds=["probe_leak"])
        assert job and job.id == jid
        jobq.fail(
            conn,
            jid,
            "w1",
            {"code": "TOOL_ERROR", "message": f"connect {dsn} failed: {key} ({kv})"},
            retryable=False,
        )
        row = (
            conn.execute(sa.select(jobs_t.c.last_error).where(jobs_t.c.id == jid))
            .mappings()
            .first()
        )
    msg = row["last_error"]["message"]
    for marker in (key, "hunter2pass", "hunter2"):
        assert marker not in msg
    assert "[REDACTED:anthropic_key]" in msg and "[REDACTED:dsn_credentials]" in msg

    items = alice.get(
        f"/api/v1/projects/{ids['project']}/jobs", params={"status": "failed"}
    ).json()["items"]
    api_msg = next(j["last_error"]["message"] for j in items if j["id"] == jid)
    for marker in (key, "hunter2pass", "hunter2"):
        assert marker not in api_msg


def test_jobs_list_member_sees_coarse_error_owner_sees_sanitized(client_factory):
    """Members get {code, category}; owners get the sanitized message."""
    import sqlalchemy as sa
    from helpers import add_member

    from backend import jobs as jobq
    from backend.db import jobs as jobs_t
    from backend.db import transaction

    alice, bob = client_factory("alice"), client_factory("bob")
    carol = client_factory("carol")
    ids = setup_project(alice, bob)
    org_id = alice.me["memberships"][0]["organization"]["id"]
    add_member(org_id, carol.me["user"]["id"], role="member")
    with transaction() as conn:
        jid, _ = jobq.enqueue(
            conn, "probe_coarse", {"project_id": ids["project"]}, dedupe_key="vis-coarse"
        )
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(
                status="dead",
                last_error={
                    "code": "TOOL_ERROR",
                    "message": "connect post" + "gresql://u:p" + "@db.internal failed",
                    "trace": "tb",
                },
            )
        )
    url = f"/api/v1/projects/{ids['project']}/jobs?status=dead"
    member_err = next(j["last_error"] for j in carol.get(url).json()["items"] if j["id"] == jid)
    assert member_err == {"code": "TOOL_ERROR", "category": "tool"}
    owner_err = next(j["last_error"] for j in alice.get(url).json()["items"] if j["id"] == jid)
    assert "db.internal" in owner_err["message"] and "u:p@" not in owner_err["message"]
    assert "trace" not in owner_err


def test_jobs_list_never_leaks_across_projects(client_factory):
    """Org B sees nothing of org A: 404 on A's list + A's jobs absent from B's list."""
    from backend import jobs as jobq
    from backend.db import transaction

    alice, bob = client_factory("alice"), client_factory("bob")
    dave = client_factory("dave")
    ids_a = setup_project(alice, bob)
    # a bare project in a different org — jobs listing needs no repo/scope
    p = dave.post("/api/v1/projects", json={"name": "org-b-project"})
    assert p.status_code == 201, p.text
    ids_b = {"project": p.json()["id"]}
    assert ids_a["project"] != ids_b["project"]

    accepted = alice.post(
        f"/api/v1/projects/{ids_a['project']}/assessments",
        json={"scope_id": ids_a["scope"], "repo_id": ids_a["repo"], "commit_sha": HEAD},
    )
    assert accepted.status_code == 202, accepted.text
    a_assessment = accepted.json()["assessment"]["id"]

    with transaction() as conn:
        a_job, _ = jobq.enqueue(
            conn, "probe_a", {"project_id": ids_a["project"]}, dedupe_key="iso-a"
        )
        b_job, _ = jobq.enqueue(
            conn, "probe_b", {"project_id": ids_b["project"]}, dedupe_key="iso-b"
        )
        # payload names only an assessment in A — covered by the OR payload clause
        a2_job, _ = jobq.enqueue(
            conn, "probe_a2", {"assessment_id": a_assessment}, dedupe_key="iso-a2"
        )

    # non-member of A's org cannot even resolve the project
    assert dave.get(f"/api/v1/projects/{ids_a['project']}/jobs").status_code == 404

    b_items = dave.get(f"/api/v1/projects/{ids_b['project']}/jobs").json()["items"]
    b_ids = {j["id"] for j in b_items}
    assert b_job in b_ids
    assert a_job not in b_ids
    assert a2_job not in b_ids

    a_items = alice.get(f"/api/v1/projects/{ids_a['project']}/jobs").json()["items"]
    a_ids = {j["id"] for j in a_items}
    assert a_job in a_ids
    assert a2_job in a_ids
    assert b_job not in a_ids
