# N9 owner fix — statement-boundary defect in the annotation matcher (P2)

Independent repeat review: https://github.com/adamissac/ari-ai/pull/633
(`codex/rc4-n8-repeat`, `docs/reviews/rc4-n8-independent/REPORT.md`)
Candidate reviewed: `4fdcf10cc2320b0eb499a177e3b8bea847de8e73`. N8 resolved;
new finding N9/P2 in the same file you own.

## Defect

`backend/backend/redaction.py:52` — the annotated-assignment alternative added
for N8 uses `[^=\n]+?` for the type annotation. That span accepts `;` and the
next statement, so a declaration with no secret is treated as if it assigned
the next variable's quoted value:

```python
# Input — valid Python; no credential value
password: str; notice = "public notice"
# Actual output
password: str; notice = "[REDACTED:kv]"
```

Both reproductions fail on the real implementation:
1. `redact_code(source)` must return the source byte-identical (both input and
   output must `ast.parse`).
2. A unified diff containing that source must roundtrip byte-identical through
   `ArtifactStore.put_text(..., kind=ArtifactKind.diff)` + `get`
   (`artifacts.py:102–109` routes diffs through `redact_code`).

This is the N7 artifact-integrity requirement newly broken by N8's broader
matcher (introduced in `5628af35`). Not a secret disclosure — a persisted
corruption of an unrelated statement.

## Required correction (Codex's words)

"Restrict the annotation match to the actual assignment's syntactic/token
boundaries; do not consume a semicolon or comment into another statement.
Preserve the N8 dictionary/typed-secret controls, N7 nonliteral expressions,
and original sealed-diff bytes."

## Verbatim probes — `docs/reviews/rc4-n8-independent/test_additional.py`
(copied verbatim into this tree as
`backend/tests/live_defense_e2e/test_rc4_n9_statement_boundaries.py`)

```python
def test_annotation_does_not_redact_a_different_statement():
    import ast
    from backend.redaction import redact_code
    source = 'password: str; notice = "public notice"\n'
    ast.parse(source)
    actual = redact_code(source)
    ast.parse(actual)
    assert actual == source, 'annotation matcher redacted a different statement: ' + actual

def test_annotation_preserves_unrelated_statement_in_stored_diff(tmp_path):
    source = '--- a/config.py\n+++ b/config.py\n@@ -0,0 +1 @@\n+password: str; notice = "public notice"\n'
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, source, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, body = store.get(conn, ref.id)
    assert body.decode() == source, 'non-secret statement changed in persisted diff'
```

Plus the retained controls (all must still pass):
- `test_literal_secret_is_not_stored_in_redacted_diff` (dict + typed forms)
- `test_redacted_diff_literal_secret_not_disclosed_by_artifact_api` (200 viewer
  without secret, 404 outsider)
- `test_split_read_and_write_limits_are_enforced` (positive split-limit control)
- All original N7 probes (`api_key=os.environ["API_KEY"]` stays byte-identical;
  real secrets still scrubbed), vendored `test_redact_code`/`test_redact_text_gaps`.

## Hard constraints

- Do NOT weaken or drop the N8 patterns for `{"key": "v"}` / `d["k"] = "v"` /
  `name: Type = "v"` — they must keep firing inside ONE statement.
- Do NOT re-add free-text kv matching to code (`redact_code`) — that reopens N7.
- Do NOT touch the sealed verified-diff path or `redacted=` labelling.
- Fix lives on `feat/devin-execution-reliability` (same branch as N7/N8);
  reply here with the commit SHA when green.

## Evidence expected

- Both new N9 probes pass + all N7/N8 probes pass on your branch.
- `pytest backend/tests/live_defense_e2e/test_rc4_n8_artifact_disclosure.py
  backend/tests/live_defense_e2e/test_rc4_n9_statement_boundaries.py
  backend/tests/test_execution_reliability.py` green.
- Report the SHA; I'll cascade into integration + S3→S8 and repin the manifest.
