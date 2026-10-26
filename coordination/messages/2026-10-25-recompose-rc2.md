# Release candidate 2 — recomposition report

Date: 2026-10-25 · Author: Devin (integration) · Branch: `integration/2026-11-02-release`
Canonical repo: `adamissac/ari-ai` (upstream main @ `f8ab19e7`)

## New release-candidate SHA

**RC2 candidate: `3f16c99fb728323ae464890862f84e16981e5f71`** (branch tip at report time)
Previous frozen RC preserved for comparison: `bc490484`.

## Exact source SHAs

| Source | SHA | Notes |
|---|---|---|
| upstream/main (adamissac/ari-ai) | `f8ab19e7587614b9741e2cd3433807c348b799d2` | merge-base; includes #570 C4 redaction fixes, #574 idempotent 0016/0017, #582 MIGRATION_DATABASE_URL, #584 per-project quota, #585 credential fixtures, #587 sealed-diff delivery, #572 cross-org jobs test |
| fork #1 exec-reliability (R5) | `6063d9b5` | abrupt-death → recovery_required via `execution.reconcile` + provider-ledger bridge |
| fork #4 hardening (R2) | `356cd535` | disconnect row-lock, cancels queued/waiting steps, `services.repository_revocation()` |
| fork #6 onboarding (R3/R4) | `41b77fc2` | fresh `verify_installation_access` on reconnect; tenant-scoped `connected_repo_id` join |
| fork #7 release-validation | `16fa8122` | docs + gate records through RC1 |
| fork #2 e2e | `247c2312` | F7 unxfail verification docs |
| fork #3 observability | `92e00096` | unchanged since RC1 |
| fork #5 deployment-ops | `c7c5336f` | unchanged since RC1 |
| Integration glue (this session) | `c44fea5f` | first_responder `_cancelled` closure + final-persist `repository_revocation` checks (R2 provider-turn half) |

## Conflict resolutions (Adam-preserving)

- **Frontend rail**: upstream's new GROUPS structure (Review/Defend/Configure) adopted wholesale;
  Onboarding link added inside 'Configure'. Nav test + demo-recovery spec updated to 12 links.
- **`assessment_starts_per_hour`**: dropped — upstream replaced it with per-project quota
  (`check_project_quota`, #584). `auth_dev_login_burst` retained.
- **`reconnect_repository` duplicate**: union merge produced old `can_connect` variant + new
  strict `verify_installation_access` variant — kept the R3 strict version only.
- **Migrations**: no new upstream revisions; ours stay 0018 (exec-reliability) → 0019
  (disconnect) on upstream's 0016/0017. Single head 0019.
- **SCHEMA_VERSION `0.26.0`** — chain: upstream 0.22 ops readiness; 0.23 reserved #458;
  0.24 observability; 0.25 onboarding; 0.26 disconnect.

## Test evidence (all strict, zero xfails)

| Suite | Result |
|---|---|
| backend | **653 passed**, 9 skipped |
| security-agents | **595 passed**, 24 skipped |
| shared | **115 passed**, 1 skipped |
| make check + mypy | clean |
| frontend vitest | **365 passed** (86 files) + lint + typecheck + `next build` |
| transport | **35 passed** |
| migrations | single head `0019`; parallel-migration test 2/2 |
| Codex F1–F10 + R2–R5 probes + exec-reliability compat | **20 passed, strict — zero xfails** |
| Playwright real-stack e2e | **49/49 passed** (5.7m) |
| `deploy/release_readiness.py` | **GO — 25/25 PASS, 0 WARN, 0 FAIL** (report: `deploy/reports/release-readiness-20261025T052931+0000.md`) |

## Codex re-review status

R2–R5 + C1–C5 findings from the `005ea42d` re-review: all now fixed and asserted strict
on the RC — R2 (provider-turn + final-persist revocation checks), R3 (fresh reconnect
authz), R4 (tenant-scoped picker join), R5 (recovery_required on abrupt death), C4
(upstream #570, now merged). Hand RC SHA `3f16c99f` to Codex for independent re-review.

## GO/NO-GO

**GO for review + staging validation. NO-GO for customer deploy** until Codex's
independent re-review signs off on `3f16c99f` and Adam confirms the upstream merge
order. Operational pre-pilot items unchanged: real secrets, backup scheduling, first
real staging deploy → `release_readiness.py --remote`.
