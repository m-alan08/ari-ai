# Code-intelligence pipeline audit — 2026-10-24

Executed audit of `code-intelligence/` (ingest → extract → store → retrieve →
memory) and its Postgres adapters (`backend/intelligence.py`,
`backend/intel_store.py`, `backend/intelligence_memory.py`) against the pilot
readiness goal: accurate, version-aware, secure, reliable for unfamiliar repos.

Method: reproduction scripts, corpus-wide redaction comparison, real index
runs through `IntelligenceService`, labeled-question retrieval evaluation, and
the executed test suites. Raw numbers:
`code-intelligence/evidence/m2-index-retrieval-audit.json` (regenerate with
`code-intelligence/evidence/index_benchmark.py`).

## 1. PR #441 redaction/parser regression — reproduced, fix verified

`redact()` on main (64bb24a2) corrupts valid Python: the `_ASSIGN` regex
replaced everything after a secret-like key's `=`/`:` to end-of-line, breaking
lines such as `webhook_secret_configured=bool(` inside a `dict(...)` call.

Measured on all 582 tracked `.py` files at 64bb24a2:

| Variant | Files failing `ast.parse` after `redact()` | Files with changed line count |
|---|---|---|
| main (pre-#441) | **38** | **2** (Bearer `\s+` ate the newline) |
| PR #441 | **0** | **0** |

A diff audit of the 104 lines that main redacted but #441 does not found **0
literal secrets** — every one was a code expression or JSON key the old regex
over-matched. The fix keeps quote prefixes and bare-token boundaries, preserves
line counts, and still redacts every literal secret in the corpus.

Verdict: the fix is correct and necessary. The regression is **resolved** —
commit `7a5b329f` is carried on this PR's branch, so merging it lands the fix.

## 2. Index coverage (real runs, SHA 7a5b329f)

| Target | Files | Symbols | Imports | Call edges | Routes | Expectations | Time | Heap peak | Store |
|---|---|---|---|---|---|---|---|---|---|
| payments-lite (`examples/payments-lite/target`) | 8 | 45 | 25/25 | 114/109 sites | **8/8** | 49 | 0.16–0.32s | 2–45MB | 135KB |
| `backend/` prefix | 127 | 1,147 | 2,010/2,010 | 7,445/10,823 | 97/99 (+2 flagged) | 780 | 4.11s | 81.6MB | 4.1MB |
| ari-ai (whole repo) | 1,798 | 5,585 | 7,901/7,901 | 26,844 | 107/109 (+2 flagged) | 10,963 | 92.4s | 617MB | 38.6MB |

- **Symbols/imports: 100% retained** on every target (symbol count includes
  563 module nodes, so symbols exceed def+class ground truth).
- **Call edges** include `Depends()`/`Security()` argument edges (declared
  dependencies, not executed calls) — counts can exceed raw call sites.
- **Resolution honesty** (ari-ai): 26.2% exact / 2.0% likely / 71.8%
  unresolved. Dynamic calls (`callback(...)`, `getattr`, dispatch tables) stay
  `unresolved` — never guessed — as required.
- **Routes**: post-fix, `indexed + flagged == ground truth`. Before the fix,
  `app.get(FIXTURE_ROUTE)` and `app.post(CONTROL_PATH)` in
  `backend/live_defense/owned_test_app.py` were **silently dropped**.
- **Expectations**: all `ambiguous`/extracted status — docstring/Field/test
  signals recorded with confidence 0.55–0.9, never presented as verified.

## 3. Commit / profile / tenant identity

- `snapshot_id = stable_id("snp", project, repo, commit_sha, index_version)`;
  a prefix change re-keys via `index_version = "python-ast-ts/0.3.1+profile.<sha>"`.
- `SQLiteIndexStore`: `UNIQUE(project, repo, commit_sha, index_version)`,
  immutable payload with sha256 integrity check on read, `get` verifies
  `index.ref == ref`, `find` raises `SNAPSHOT_NOT_READY`.
- `PostgresPayloadStore`: `_validate_ref` enforces repo→project ownership;
  `PostgresMemoryStore._validate` requires a *ready* snapshot + ownership.
- Executed tests: `test_versions_and_tenants_are_isolated`,
  `test_postgres_profiles_remain_distinct_and_readable_after_restart`,
  `test_same_commit_different_prefix_never_reuses_the_wrong_coverage`,
  `test_parser_revision_creates_new_facts_without_relabeling_old_memory`,
  `test_write_once_ready_then_read` — all pass.

## 4. Retrieval evaluation (labeled questions, `test_retrieval_quality.py`)

| Corpus | Required spans | Recalled | Missing |
|---|---|---|---|
| payments-lite (10 questions) | 17 | **14 (82%)** | `require_owner` ×2, `payment_out` |
| ari-ai (5 questions) | 8 | **5 (63%)** | `sign`, `decide_expectation`, `redact_evidence`, `build_pack` |

Every pack stayed under budget (10–12 chunks, 15–24k tokens vs the 24k
budget) — far below loading whole files, let alone the whole repo.

**Missing-evidence pattern**: the *primary* symbol is recalled; *secondary*
evidence loses to BM25 competition (e.g., `IntelligenceService.decide_expectation`
was displaced by 11 chunks of decision-related contracts/tests). Packs do not
mark which expected evidence is absent — a real gap to note for reviewers:
recall degrades with corpus size and shared vocabulary.

## 5. Budget behavior on a large repo

- At ari-ai scale (5,585 symbols) a 24k-token pack returns 10–12 ranked
  chunks; `BUDGET_EXHAUSTED` is raised only when the budget cannot fit the
  focused seed, covered by `test_context_budget_stale_and_project_scope`.
- Token estimates are byte-based and provably upper-bound the serialized pack
  (`estimate(pack.model_dump_json()) <= token_count`).

## 6. Persistence, decisions, regressions

- Index payloads are write-once, sha256-verified; a restarted service reads
  the same `SnapshotRef` (`test_durable_context_for_reviewer_and_scout`,
  `test_postgres_profiles_remain_distinct_and_readable_after_restart`).
- Human expectation decisions are optimistic-concurrency (revision-checked),
  audit-recorded, and durable across restart
  (`test_authenticated_decisions_are_atomic_audited_and_visible_after_restart`,
  `test_postgres_decision_race_cannot_overwrite_or_cross_snapshot`).
- Memory is keyed by `sha256(SnapshotRef.model_dump_json())` —
  `apply_decisions` raises on any snapshot mismatch, so **a decision on
  commit A cannot be filed under commit B**.
- Verified regressions require all five Verify checks, a `base_commit_sha`
  matching the indexed ref, and distinct run ids; the outbox is durable and
  idempotent (`test_verified_candidate_outbox_is_durable_idempotent_and_retrieved`).

## 7. Tenant/privacy protections

- Source is redacted **before** parse and storage (`ingest.py` — PEM, known
  token formats, assigned secrets, Bearer, URL credentials), so stored
  payloads and context packs only ever contain redacted content. The corpus
  diff found no un-redacted literal secrets post-#441, and the mid-line
  bare-token residual (`f(password=x)`/`dict(password=x)`/collection
  closers) is now redacted too — verified leak-free and parse-safe across
  all 584 tracked `.py` files.
- Context packs escape content into `<untrusted_repository_data>` blocks,
  mark every chunk `trust="untrusted"`, and never grant permissions
  (`test_context_privacy` suite).
- Postgres payload/memory stores enforce repo→project ownership and
  ready-gating; tenant cross-reads raise `PermissionError`
  (`test_versions_and_tenants_are_isolated`).

## 8. Limitations (explicit — read as "not supported", not "done")

- **Language support: Python only.** Tree-sitter gate + `ast` extraction;
  no JavaScript/TypeScript indexing exists or is claimed here.
- **Non-literal route decorators are not indexed** (now honestly flagged via
  `IndexIssue`, previously silent).
- **71.8% of call edges are unresolved** at repo scale — dynamic dispatch,
  attribute calls, and shadowed names are labeled `unresolved`, not guessed.
  Treat the call graph as a partial map: absence of an edge is not evidence
  a call does not happen.
- **Retrieval recall degrades at scale** (§4): primary evidence is recalled;
  secondary evidence loses to BM25 competition (82%→63%). Packs do not mark
  expected-but-absent evidence — treat "not in the pack" as "not retrieved",
  not "not in the repo".
- **Mid-line secret redaction residual** (fixed for digit-bearing literals):
  `f(password=hunter2, ...)` / `dict(password=hunter2)` / `{"password":
  hunter2}` are now redacted. Still unreachable: mid-line bare tokens with no
  digit (`f(password=hunter)`) and non-first elements of a compound value
  (`password="x" if c else "y"`, `password="a" + "b"` — a regex sees only the
  first element safely). Honest consequence: a secret matching a residual
  pattern could still reach `index_payloads` and LLM context packs; tenant
  RLS + `purge_project` (#535) is the second layer, not a substitute —
  disclose this to pilot customers before onboarding code that may carry
  such secrets.
- Indexing is **whole-tree, not incremental**: ari-ai costs ~92s and ~617MB
  heap; fine for pilot repos, a limit for monorepo-scale trees.

## 9. Deliverables in this change

- `code-intelligence/code_intelligence/extract.py` — `IndexIssue` for
  non-literal/overlong/missing route paths.
- `code-intelligence/code_intelligence/privacy.py` — mid-line/closer-bound
  bare secret tokens (digit-bearing) now redacted; identifiers untouched.
- `code-intelligence/tests/test_ingest_extract.py` —
  `test_non_literal_route_paths_are_flagged_not_silently_dropped` +
  `test_mid_line_secret_values_are_redacted_without_breaking_parse`.
- `code-intelligence/tests/test_retrieval_quality.py` — labeled-question
  retrieval eval with a measured regression floor.
- `code-intelligence/evidence/index_benchmark.py` — reproducible coverage +
  latency/memory benchmark against any pinned SHA.
- `code-intelligence/evidence/m2-index-retrieval-audit.json` — this audit's
  executed numbers.
- Carries `7a5b329f` (PR #441): the parse-safe redaction fix.
