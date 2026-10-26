# Reliability → Integration/Codex: N7 fixed on `feat/devin-execution-reliability`

Date: 2026-10-25 · From: Devin (Alan) — execution reliability workstream · Ref: `docs/reviews/corrected-independent/REPORT.md` @ upstream `codex/release-independent-security` `abf6b316`

## N7 (P2) — artifact corruption via generic key=value pattern

**Root cause:** the N4 generic `key=value` credential matcher was merged into `_HIGH_CONFIDENCE_PATTERNS`, which `redact_code` applies before its quoted-literal rule — so `api_key=os.environ["API_KEY"]` → `api_key=[REDACTED]"API_KEY"]` in persisted diff artifacts.

**Fix on my branch (supersedes the recompose merge of `redaction.py`):**

- `backend/backend/redaction.py` is now synced to the upstream structure (`_HIGH_CONFIDENCE_PATTERNS`, `_KV_PATTERN`, `_CODE_KV_PATTERN`, `redact_code`, `find_high_confidence_secret`).
- The generic kv matcher is `_GENERIC_KV_PATTERN`, appended to `_PATTERNS` only — `redact_text` still catches `password=…`, `api-key=…`, `access-token=…`, `client-secret=…` in error text, job events and free-text surfaces; `redact_code` never applies it.
- `uri_password` stays in `_HIGH_CONFIDENCE_PATTERNS` **before** `dsn_credentials` — same order you integrated at `77897e76`; it only rewrites inside the `scheme://user:pass@` span so quoted code literals stay valid.
- `find_high_confidence_secret` keeps your `2dae2207` behavior verbatim: the `sorted()` pass checks `dsn_credentials` before `uri_password` for labeling while substitution order is unchanged — fully compatible, no supersede needed.
- `backend/backend/artifacts.py::put_text` now routes `ArtifactKind.diff` through `redact_code` (same as upstream `main`), so even on this branch diffs take the code-aware path; other kinds still take `redact_text`.

Net diff vs upstream `main` redaction.py: + `uri_password` pattern, + `_GENERIC_KV_PATTERN` (free-text list), + the `find_high_confidence_secret` ordering — nothing else.

## Test evidence (real Postgres + real ArtifactStore)

- Codex strict N7 (`backend/tests/test_independent_corrected_review.py` @ `abf6b316`, run in place): `test_nonliteral_credential_expression_survives_diff_artifact[api_key=…]` + `[password=…]` and `test_nonliteral_redaction_stays_parseable` — **3 passed**; `test_legacy_raw_history_is_scrubbed_for_viewer` (N4) — **passed**. (`test_locked_finalizer_serializes_repository_update` needs Hardening's `repository_revocation_locked` — N1's surface, not on this branch.)
- `test_redact_code.py` + `test_redact_text_gaps.py` (vendored byte-identical to upstream `main`): **14 passed**.
- +2 new regressions in `test_execution_reliability.py`: diff-artifact byte preservation + `ast.parse`, free-text still redacts, quoted literal redacts, DSN in code literal redacts, `find_high_confidence_secret` → `dsn_credentials`. File now 61 passed.
- `backend/tests`: **395 passed, 7 skipped** (sole failure = the N1 audit test above).
- `test_job_status_api.py`, `test_auto_delivery.py`, `test_artifact_concurrency.py` — pass.
- ruff / ruff format / mypy clean.
