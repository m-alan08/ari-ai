"""Independent checks of corrected release 77897e76. No external providers."""

import ast

import pytest

from backend.artifacts import ArtifactStore
from backend.db import transaction
from backend.redaction import redact_code
from shared.evidence import ArtifactKind


@pytest.mark.parametrize(
    "source",
    [
        'api_key=os.environ["API_KEY"]\n',
        'password=os.environ.get("DB_PASSWORD")\n',
    ],
)
def test_nonliteral_credential_expression_survives_diff_artifact(source, tmp_path):
    """N7: source expressions contain no credential; artifact bytes must survive."""
    ast.parse(source)
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, source, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, body = store.get(conn, ref.id)
    actual = body.decode()
    assert actual == source, f"redaction altered non-secret source: {actual!r}"
    ast.parse(actual)


def test_nonliteral_redaction_stays_parseable():
    source = 'password=os.environ.get("DB_PASSWORD")\n'
    ast.parse(redact_code(source))


def test_legacy_raw_history_is_scrubbed_for_viewer(client_factory):
    import sqlalchemy as sa
    from helpers import add_member, setup_project

    from backend import jobs
    from backend.db import job_events

    alice, bob, viewer = (client_factory(n) for n in ("alice", "bob", "viewer"))
    ids = setup_project(alice, bob)
    org = alice.me["memberships"][0]["organization"]["id"]
    add_member(org, viewer.me["user"]["id"], role="viewer")
    secret = "legacy-history-only-password"
    raw = "postgresql://svc:" + secret + "@db.internal/app"
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "legacy_review", {"project_id": ids["project"]})
        conn.execute(
            sa.update(job_events)
            .where(job_events.c.job_id == jid)
            .values(reason=raw, details={"note": raw})
        )
    response = viewer.get(f"/api/v1/jobs/{jid}/history")
    assert response.status_code == 200
    assert secret not in response.text
    outsider = client_factory("unrelated-outsider")
    assert outsider.get(f"/api/v1/jobs/{jid}/history").status_code == 404
    with transaction() as conn:
        stored = (
            conn.execute(sa.select(job_events.c.reason).where(job_events.c.job_id == jid))
            .scalars()
            .all()
        )
        assert raw in stored, "read-side protection must cover actual legacy rows"


def test_locked_finalizer_serializes_repository_update(client_factory):
    import sqlalchemy as sa
    from helpers import setup_project

    from backend.db import repositories
    from backend.services import repository_revocation_locked

    alice, bob = client_factory("alice"), client_factory("bob")
    rid = setup_project(alice, bob)["repo"]
    with transaction() as finalizer:
        assert repository_revocation_locked(finalizer, repo_id=rid) is None
        with pytest.raises(sa.exc.OperationalError) as blocked:
            with transaction() as revoker:
                revoker.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
                revoker.execute(
                    sa.update(repositories)
                    .where(repositories.c.id == rid)
                    .values(disconnected_at=sa.func.now())
                )
        assert blocked.value.orig.sqlstate == "55P03"
    assert alice.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 200
    with transaction() as finalizer:
        assert repository_revocation_locked(finalizer, repo_id=rid) == "repository_disconnected"
