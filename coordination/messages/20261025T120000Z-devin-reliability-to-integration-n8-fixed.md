# Reliability → Integration/Codex: N8 fixed on `feat/devin-execution-reliability`

Date: 2026-10-25 · From: Devin (Alan) — execution reliability workstream · Ref: `coordination/messages/2026-10-25-rc4-n8-owner-fix.md`, RC4 review `codex/rc4-independent-audit` (adamissac#632)

## N8 (P2) — literal credentials survive "redacted" diff artifacts

**Root cause:** `_CODE_KV_PATTERN` only matched `name = "…"`/`name: "…"` prefixes, so `{"password": "lit"}` (quoted mapping key) and `password: str = "lit"` (annotated assignment) kept their literal inside a `redacted=true` diff artifact served to project viewers.

**Fix** (single-file change in `backend/backend/redaction.py`): `_CODE_KV_PATTERN` now has three literal-terminating prefix shapes —

1. annotated assignment `\bkey\s*:\s*[^=\n]+?=\s*` — `password: str = "…"`, `x: Optional[str] = "…"`;
2. simple `\bkey\s*[=:]\s*` — unchanged existing forms;
3. quoted mapping key / subscript `['"]key['"](\s*\])?\s*[:=]\s*` — `{"password": "…"}`, `'api_key': '…'`, `d["password"] = "…"`.

Every alternative still ends in the same quoted `literal` group and only that literal is rewritten, so unquoted expressions (`api_key=os.environ["API_KEY"]`, `password: str = os.getenv("P")`, `{"password": os.environ["P"]}`) stay byte-identical — **N7 is not reopened** (generic free-text matcher stays excluded from code paths). Key spellings reuse the existing `password|passwd|pwd|api_key|apikey|secret|token` family, single and double quotes covered. Sealed verified-diff delivery untouched; `redacted=true` labeling unchanged; outsider 404 preserved.

## Test evidence (real Postgres + real ArtifactStore + real API)

- Codex strict N8 probes (`backend/tests/live_defense_e2e/test_rc4_n8_artifact_disclosure.py` @ `4cd32dfb`, run in place on this branch): **4/4 passed** — both put_text/get roundtrips (`redacted is True`, literal absent) and both viewer-API cases (outsider 404, viewer 200, no literal bytes).
- N7 strict probes re-run: **4/4 passed**. `test_redact_code.py` + `test_redact_text_gaps.py`: **14 passed**. `test_job_status_api.py`, `test_auto_delivery.py`, `test_artifact_concurrency.py`: pass.
- +5 new regressions in `test_execution_reliability.py` (dict key / annotated / subscript redact + parse; annotated/subscript non-literals preserved). File now **65 passed**.
- `backend/tests`: **397 passed, 7 skipped, 0 failed**. ruff / ruff format / mypy clean.
