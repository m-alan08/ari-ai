"""Additional review-only artifact disclosure probes with synthetic secrets."""

import pytest

from backend.artifacts import ArtifactStore
from backend.db import transaction
from shared.evidence import ArtifactKind


@pytest.mark.parametrize(
    "source",
    [
        'settings = {"password": "review-only-dict-password"}\n',
        'password: str = "review-only-typed-password"\n',
    ],
)
def test_literal_secret_is_not_stored_in_redacted_diff(source, tmp_path):
    secret = (
        "review-only-dict-password" if "dict-password" in source else "review-only-typed-password"
    )
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, source, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        row, body = store.get(conn, ref.id)
    assert row["redacted"] is True
    assert secret not in body.decode(), "literal credential retained in artifact labeled redacted"


@pytest.mark.parametrize(
    "source,secret",
    [
        ('+settings = {"password": "review-only-dict-password"}\n', "review-only-dict-password"),
        ('+password: str = "review-only-typed-password"\n', "review-only-typed-password"),
    ],
)
def test_redacted_diff_literal_secret_not_disclosed_by_artifact_api(source, secret, client_factory):
    from helpers import add_member, setup_project

    alice, bob, viewer = (client_factory(n) for n in ("alice", "bob", "viewer"))
    ids = setup_project(alice, bob)
    org = alice.me["memberships"][0]["organization"]["id"]
    add_member(org, viewer.me["user"]["id"], role="viewer")
    diff = "--- a/settings.py\n+++ b/settings.py\n@@ -0,0 +1 @@\n" + source
    with transaction() as conn:
        ref = ArtifactStore().put_text(
            conn, diff, kind=ArtifactKind.diff, project_id=ids["project"]
        )
    outsider = client_factory("unrelated-artifact-outsider")
    assert outsider.get(f"/api/v1/artifacts/{ref.id}").status_code == 404
    response = viewer.get(f"/api/v1/artifacts/{ref.id}")
    assert response.status_code == 200
    assert ref.redacted is True
    assert secret not in response.text, (
        "viewer received credential bytes from a redacted diff artifact"
    )
