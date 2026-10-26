# RC4: Codex third-review remediation (N6, N7, upstream eea084bc, transfer stack, CI)

Date: 2026-10-25 · From: Alan (integration) · Responds to: `docs/reviews/corrected-independent/REPORT.md` @ upstream `abf6b316` (NO-GO on `77897e76`)

**Candidate SHA (immutable): `6a72949efa7c0a2d3233eb6884bb7a23dfe71ced`.** This file is a docs-only commit on top of it.
Earlier candidates are kept for comparison: RC1 `bc490484`, RC2 `3f16c99f`, RC3 `2dae2207`, reviewed `77897e76`.

**Verdict: GO FOR INDEPENDENT REVIEW.** This is not a customer GO. Customer enforcement stays disabled. Nothing is merged or deployed.

## Sources

| Component | Exact SHA |
|---|---|
| upstream `adamissac/ari-ai` main (#591, #596, #597, #599) | `eea084bc87ac6ca94f578073251c478655b09366` |
| upstream PR #412 | `4ecd7558c20d676985294073d5a818a538bdf78c` |
| upstream PR #458 (Phase 7C) | `8396e836866e33ebc34a402a7c8bd0eb6bc769d8` |
| reliability, with the **N7 owner fix** | `a23964e10307b6979f80562d542ca48a15460d5a` |
| observability (N4 read surfaces) | `c30c631d` |
| hardening (N1 locked revocation) | `65e33986` |
| onboarding (N2) | `9317b339` |
| deployment ops | `c7c5336f` |
| e2e / release validation | `247c2312` / `16fa8122` |
| Codex strict tests (imported verbatim) | `abf6b316` |

N1–N5 fixes are preserved as-is, with no redesign. #458's `investigation_source_revoked` was added **after** the N1 checks at final persist. The order there is now: direct `repository_revocation`, then `repository_revocation_locked`, then the source-revocation check. Any non-None result cancels the invocation and keeps the partial transcript and cost.

## N7: code-aware redaction (owner: reliability, `a23964e1`)

- The generic key=value matcher (`_GENERIC_KV_PATTERN`) now lives only in `_PATTERNS` and is used only by `redact_text`.
- `redact_code` applies only the token/DSN/URI patterns and the quoted-literal rule.
- `ArtifactStore.put_text` sends `diff` artifacts through `redact_code`.
- Credential redaction is not disabled: free-text `password=…`, `api-key=…` and quoted code literals are still redacted.
- The uri_password→dsn substitution order and the dsn-label preference are unchanged.

Evidence: Codex's 3 strict N7 cases pass, along with `test_redact_code.py` and `test_redact_text_gaps.py`. Two new regressions in `test_execution_reliability.py` check `ArtifactStore.put_text/get` roundtrips and `ast.parse` of the stored artifacts.

## N6: transfer stack, real merge equivalence

The transfer branches are now one ordered stack on upstream `eea084bc`. Each step is the previous step plus one workstream. Existing `transfer/*` branches were updated by merge, with no force-push.

| # | Branch | SHA | Alembic head | SCHEMA_VERSION | Step gates |
|---|---|---|---|---|---|
| S1 | `transfer/01-pr412` | `0e2d26c2` | 0017 | 0.22.0 | backend 25, frontend unit 23 |
| S2 | `transfer/02-pr458` | `40f1e333` | 0017 | 0.23.0 | backend 77/1 skip, frontend 12 |
| S3 | `transfer/execution-reliability` | `1ae4952e` | 0018 | 0.23.0 | 71 |
| S4 | `transfer/investigation-observability` | `286f20af` | 0018 | 0.24.0 | backend 19, frontend 9 |
| S5 | `transfer/pilot-security-hardening` | `da145126` | 0019 | 0.25.0 | 58 |
| S6 | `transfer/customer-onboarding` | `36ebc704` | 0019 | 0.26.0 | backend 15, frontend 3 |
| S7 | `transfer/pilot-deployment-ops` | `bb097591` | 0019 | 0.26.0 | 20 |
| S8 | `transfer/release-validation` | `283ad2d4` | 0019 | 0.26.0 | migrations 2 |

Every step also passed: no conflict markers, `make contracts` leaves no diff, ruff, `import backend.main`, and a single head.

- **Schema order is now 0.23 → 0.24 → 0.25 → 0.26.** Hardening and onboarding were swapped.
- `backend/tests/test_rc2_transfer_manifest.py` is Codex's unchanged assertions with only the pins updated (reliability=S3, disconnect=S5, heads=[S2,S4,S5,S6]). It passes 2/2 on S8 and on the candidate.
- **Equivalence:** `git diff --stat transfer/release-validation 6a72949e -- . ':!coordination' ':!docs/planning/SESSION_HANDOFF.md' ':!CHANGELOG.md' ':!docs/reviews'` is **empty**. The S8 tip is the tested production tree, including #412 and #458.
- This supersedes `2026-10-25-transfer-merge-equivalence.md` / `transfer/sequential-manifest`, which omitted #412, #458 and N1–N4.
- Thirteen residual paths found at S8 were aligned to the candidate at S8, not at their originating step. They are test pins, deploy/rehearsal files and generated reports.

Upstream PRs (cumulative; open in order; nothing is opened or merged automatically):
`https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:<branch>` for each branch above.
S1 and S2 are our resolved compositions of #412 and #458 on current main. They are offered to those PRs' owners, and the #412 and #458 heads are untouched.

## Upstream compatibility (eea084bc)

- #591 default-HEAD resolution in `start_assessment`: kept verbatim. Disconnect/reconnect and authorization changes sit alongside it in `routes.py`.
- #596 quota reset times, #597 storage alerts and #599 `protected_tests`: preserved.
- Codex's newer-upstream regressions pass in `test_independent_corrected_review.py`.
- Authorization, redaction and sealed Verify delivery suites pass in the full backend run.

## GitHub CI

**Root cause:** fork `main` is about 780 commits behind upstream, so PR #8 had merge conflicts. GitHub does not create a merge ref, or run `pull_request` workflows, for a conflicting PR. That is why the earlier run showed zero checks; it was not a green result.

**Fix:**
- Added fork branch `ci/upstream-main`, which points exactly at upstream `eea084bc`.
- Retargeted PR #8 to it.

**Result on head `6a72949e`:**
- GitHub merge commit `a108df46` has parents `eea084bc` and `6a72949e`, and its tree equals `6a72949e`.
- **13/13 workflow runs succeeded** (`pull_request`): backend, frontend, security-agents, live-defense-e2e, rl-defense, code-review, code-intelligence, containers, examples, secrets, docs, qa-tools, read-only research bounds. Run IDs are 40175387430–40175387609.
- 21 checks passed and 1 was skipped: `live` (opt-in paid provider).
- `pilot-verify` is `workflow_dispatch`-only and was **not run**.

## Local strict matrix on `6a72949e` (zero xfails)

| Suite | Result |
|---|---|
| backend | 737 passed, 9 skipped (live-provider, Docker sandbox, optional rl_defense) |
| live_defense_e2e (F1–F10, N1–N7, R2–R5) | 47 passed, 1 skipped (opt-in live) |
| independent review + manifest | 7 passed |
| security-agents | 635 passed, 24 skipped |
| shared | 116 passed |
| make check + mypy + contract freshness | clean |
| migrations | 2/2, single head 0019 |
| transport / frontend | 35 / 365 tests; lint, typecheck and build pass |
| Playwright real stack | 49/49 |
| release readiness | GO, 25 PASS, 0 WARN, 0 FAIL (`release-readiness-20261025T082755+0000`) |

## Outstanding blockers

1. A new independent Codex exact-SHA review of `6a72949e`.
2. Adam's approval of the upstream landing order (S1→S8) and the opening of the upstream PRs. There is no upstream push access; only compare URLs exist.
3. `pilot-verify` (dispatch-only) has not run.
4. Local gitleaks hook was not installed and was skipped. The CI `gitleaks`/`secrets` checks passed.
5. Operational pre-pilot items: real secrets, backup scheduling, first staging deploy, and `release_readiness.py --remote`.
