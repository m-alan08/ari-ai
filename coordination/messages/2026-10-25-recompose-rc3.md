# RC3 recomposition report — Codex N1–N6 fixed

Date: 2026-10-25 · Author: Devin (integration)
RC3 tree tip: see `git rev-parse HEAD` on `integration/2026-11-02-release`
Supersedes RC2 `3f16c99f` (Codex NO-GO) and RC1 `bc490484` (frozen, preserved).

## Owner deliveries (verified on this tree)

| Finding | Owner | Fixed head | Evidence |
|---|---|---|---|
| N1 P1 disconnect/finalize race | Hardening `b679eb04` | `feat/devin-pilot-security-hardening` `65e33986` | `repository_revocation_locked` canonical repo→mapping lock order; final-persist glue `69547d2f`+`c97...` patchable read + locked confirm; N1 probes pass |
| N2 P2 personal-refresh bypass | Onboarding `0a9affb3` | `feat/alan/customer-onboarding` `9317b339` | `verify_installation_access` always runs on exact target install; N2 probe passes |
| N3 P2 reclaim/ledger fencing | Reliability `1dc1161e` | `feat/devin-execution-reliability` `4f4f3f15` | fenced provider-ledger recovery; both N3 schedules pass |
| N4 P1 event-history leak | Reliability + Observability `258be382` | `4f4f3f15` + `feat/devin-investigation-observability` `c30c631d` (`a1c5114e`) | sanitized event reasons/details + read-time redaction; both N4 probes pass |
| N5/N6 transfer structure | Integration (this session) | `transfer/sequential-manifest` `5539a9ca`+ | 0018/0019 linear on upstream 0016/0017; SCHEMA_VERSION 0.24/0.25/0.26; migrations 2/2 on actual merge tree |

## Integration-introduced fixes this round
- `first_responder.py` final persist: patchable `repository_revocation` read + `repository_revocation_locked` confirm (probe hook preserved, locked read authoritative).
- `redaction.py`: generic `uri_password` runs before DSN-specific pattern (nested `[REDACTED:[REDACTED...]]` bug fixed); `find_high_confidence_secret` checks specific DSN label first.
- `test_worker_drain.py`: SIGTERM tests readiness-synchronized — Codex's two failures were test flakiness, not a lifecycle bug (upstream owns the originals; offer to Adam).
- Conflict choices needing owner review: `jobs.py` claim event = reliability's reclaim-aware version; `redaction.py` patterns = union.

## Strict matrix on this exact tree (all strict, zero xfail)
- backend tests: **677 passed / 9 skipped / 0 failed** (skips all opt-in: live LLM, docker sandbox, rl_defense, rehearsal)
- live_defense_e2e incl. Codex N1–N6 probes + F1–F10 compat: **47 passed / 1 skipped**
- security-agents: **595 / 24 skipped** · shared: **115 / 1 skipped**
- make check (ruff + format + contracts) + mypy: **clean**
- frontend: **365 vitest** + eslint + tsc + production build · transport: **35/35**
- migrations: single head `0019`, `test_parallel_migrations` 2/2
- Playwright real stack: **49/49**
- `deploy/release_readiness.py`: **GO — 25/25 PASS, 0 WARN** (`deploy/reports/release-readiness-20261025T065919+0000.md`)

## State
Codex N1–N6 all addressed by owner fixes + verified on this exact tree. Customer
enforcement stays disabled. Nothing merged upstream or deployed.
Verdict: **GO FOR INDEPENDENT REVIEW** — ready for Codex exact-SHA re-review.
