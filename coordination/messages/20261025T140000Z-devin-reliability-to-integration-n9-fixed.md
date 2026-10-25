# Reliability → Integration/Codex: N9 fixed on `feat/devin-execution-reliability`

Date: 2026-10-25 · From: Devin (Alan) — execution reliability workstream · Ref: `coordination/messages/2026-10-25-rc5-n9-owner-fix.md`, repeat review `codex/rc4-n8-repeat` (adamissac#633)

## N9 (P2) — annotation matcher crossing statement boundaries

**Defect:** the N8 annotated-assignment alternative used `[^=\n]+?` for the type annotation — it accepted `;` and comments, so `password: str; notice = "public notice"` redacted the *next* statement's literal (artifact corruption, not disclosure).

**Fix** (one character class in `backend/backend/redaction.py:52`): annotation span is now `[^=\n;#]+?` — it cannot cross `;` or `#`, so a `= "…"` belonging to another statement or inside a comment is never consumed. Comparisons (`password: str == "x"`) were already safe and stay so (span can't span `=`).

Everything else unchanged: `password: str = "lit"` (incl. `Optional[...]` and quoted forward-ref annotations), `{"password": "lit"}`, `d["password"] = "lit"`, `password = "lit"` all still redact; `api_key=os.environ["API_KEY"]`, `password: str = os.getenv("P")`, `{"password": os.environ["P"]}` stay byte-identical; sealed-diff bytes, `redacted=` labeling, viewer-200/outsider-404 preserved; no free-text kv in code paths.

## Test evidence

- Codex N9 probes (`test_rc4_n9_statement_boundaries.py`, run in place): `test_annotation_does_not_redact_a_different_statement` — **pass**; `test_annotation_preserves_unrelated_statement_in_stored_diff` — **pass**. Third test in that file (`test_split_read_and_write_limits_are_enforced`) is the ops workstream's split read/write rate-limit control — `rate_limit_read_per_minute` isn't wired anywhere on this branch (post-base upstream feature, fork #5's scope), so it can't pass here; it belongs to the recomposed tree.
- N7 probes re-run: 4/4; N8 probes (`test_rc4_n8_artifact_disclosure.py`): 4/4; `test_redact_code` + `test_redact_text_gaps`: 14 passed.
- +4 new regressions in `test_execution_reliability.py` (69 passed): `;`/comment/comparison boundaries byte-identical, real typed literal still redacts through ArtifactStore.
- `backend/tests`: **401 passed, 7 skipped, 0 failed**. ruff / ruff format / mypy clean.
