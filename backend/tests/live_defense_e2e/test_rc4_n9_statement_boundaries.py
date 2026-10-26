"""N9 — statement-boundary probes from Codex's repeat independent review
(docs/reviews/rc4-n8-independent/test_additional.py @ adamissac pull/633)."""

import ast


def test_annotation_does_not_redact_a_different_statement():
    from backend.redaction import redact_code

    source = 'password: str; notice = "public notice"\n'
    ast.parse(source)
    actual = redact_code(source)
    ast.parse(actual)
    assert actual == source, "annotation matcher redacted a different statement: " + actual


def test_annotation_preserves_unrelated_statement_in_stored_diff(tmp_path):
    from backend.artifacts import ArtifactStore
    from backend.db import transaction
    from shared.evidence import ArtifactKind

    source = '--- a/config.py\n+++ b/config.py\n@@ -0,0 +1 @@\n+password: str; notice = "public notice"\n'
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, source, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, body = store.get(conn, ref.id)
    assert body.decode() == source, "non-secret statement changed in persisted diff"


def test_split_read_and_write_limits_are_enforced(client_factory, settings_override):
    settings_override(rate_limit_read_per_minute="3", rate_limit_per_minute="2")
    client = client_factory("split-limit-control")
    for _ in range(3):
        assert client.get("/api/v1/projects").status_code == 200
    assert client.get("/api/v1/projects").status_code == 429
    for i in range(2):
        assert client.post("/api/v1/projects", json={"name": f"write-{i}"}).status_code == 201
    assert client.post("/api/v1/projects", json={"name": "write-blocked"}).status_code == 429
