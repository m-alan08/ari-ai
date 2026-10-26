# N8 owner assignment — literal credentials survive "redacted" diff artifacts

Source: independent RC4 review, `adamissac/ari-ai` PR #632,
`docs/reviews/rc4-independent/REPORT.md` on `codex/rc4-independent-audit`.

Reviewed product SHA: `6a72949efa7c0a2d3233eb6884bb7a23dfe71ced` (RC4). Verdict
NO-GO; F1–F10 and N1–N7 all PASS. This is the only new production finding.

## Finding (P2 — same-project disclosure / false redaction guarantee)

`backend/backend/redaction.py` `_CODE_KV_PATTERN` (line ~44) handles simple
quoted assignments but misses:

1. Quoted mapping keys: `settings = {"password": "literal"}`
2. Annotated/typed assignments: `password: str = "literal"`

`backend/backend/artifacts.py` (~line 102) writes diff artifacts through that
code-aware redactor and unconditionally labels the artifact `redacted=true`.
`backend/backend/api/routes.py` (~line 1587) then returns the retained bytes to
any project-authorized viewer. Real producers (`security_agents/host_adapter.py`
~631, `backend/backend/host.py` ~538) write patch diffs through this path.

A real project viewer gets HTTP 200 containing the literal; an unrelated user
correctly gets 404 (preserve that control). Sealed verified delivery keeps exact
original bytes behind its own boundary — do NOT alter that path.

## Owner: shared redaction/artifact + reliability integration

Work on the existing owner branch (fork PR #1 workstream head `a23964e1`),
NOT on integration/2026-11-02-release.

Requirements:

- Extend syntax-aware literal handling in the code-aware redactor to cover
  quoted dict/mapping keys and annotated assignments (`: Type = "..."`).
  Cover single-quoted, double-quoted, and common key spellings already covered
  by the existing pattern family (password, api_key, secret, token, etc.).
- Do NOT restore the generic free-text key=value matcher to code paths —
  that reopens N7 (nonliteral expressions like `api_key=os.environ["API_KEY"]`
  must stay byte-identical, and `ast.parse` roundtrips must hold).
- Keep the artifact labeled `redacted=true` only when redaction actually ran;
  sealed-diff delivery behavior unchanged.
- Preserve N7 controls and all prior secret-exposure tests. No xfail/skip, no
  weakened assertions, credential redaction stays ON.

## Strict regressions (already on integration branch)

`backend/tests/live_defense_e2e/test_rc4_n8_artifact_disclosure.py`
(commit `4cd32dfb`) — 4 parametrized cases copied verbatim from Codex
`docs/reviews/rc4-independent/test_additional.py`:

- `test_literal_secret_is_not_stored_in_redacted_diff` — put_text/get roundtrip
  must not retain the literal while `redacted is True`.
- `test_redacted_diff_literal_secret_not_disclosed_by_artifact_api` — real
  TestClient viewer path: outsider 404, viewer 200, no literal bytes.

Also rerun the N7 strict probes and the existing secret-exposure/redaction
suites.

## Deliverable

Fixed SHA on the owner branch + targeted test evidence. Integration will merge
the head into the recomposed candidate; do not touch transfer branches.
