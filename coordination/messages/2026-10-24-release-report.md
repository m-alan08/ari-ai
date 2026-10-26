# November 2 Pilot Release — Integration Report

Branch: `integration/2026-11-02-release` on m-alan08/ari-ai
Final SHA: `975d7e71b01a34648c9a13d4fedefe0391c4da5a`
Base: upstream/main @ `64bb24a2` (verified still head at merge time)
Draft PR: https://github.com/m-alan08/ari-ai/pull/8

## Source workstreams (dependency order)

| # | Workstream | Source SHA | Merge commit |
|---|---|---|---|
| 1 | upstream #412 Phase 6.5 RL-gated pilot | a9f5ff31 | 87ad6266 |
| 2 | upstream #458 Phase 7C Scout/Diagnose | db0a631d | a682e1a1 |
| 3 | fork #2 Live Defense E2E | e187e9a2 | e486d03c |
| 4 | fork #1 execution reliability | 17c3ded0 | 83781d5b |
| 5 | fork #3 investigation observability | b44b28aa | f9ae4933 |
| 6 | fork #4 customer security hardening | 92178d0e | b011c878 |
| 7 | fork #5 deployment/ops | c7c5336f | 2a3e89dc |
| 8 | fork #6 customer onboarding | cb085ed0 | 2292f350 + b5bfd3d0 |

Codex review branch `codex/first-responder-integration-review` @ 6e882045
consulted; its regression file vendored into
`backend/tests/live_defense_e2e/test_independent_integration.py`.

## Conflict resolutions of record

1. **Alembic 0016 collision** — reliability kept 0016; hardening's
   repo-disconnect renumbered 0016→0017 (single head; conftest `_TABLES`
   and `test_parallel_migrations` pin updated).
2. **Repository disconnect** (hardening × onboarding) — owner-converged:
   `disconnected_at` flag + `automatic_assessments=False` +
   `deploy_commit_mappings` revoked + `installation_id` kept + dict response;
   `reconnect` retained with access re-verify.
3. **Contract versions** — SCHEMA_VERSION 0.24.0; comment chain documents
   0.20→0.24 claims; generated code regenerated (178 files), never hand-merged.
4. **worker.py** — reliability loop wholesale + phase7c `JobDeferred` +
   ops alerts import union.
5. **Observability × 7C** — observability reads the real 7C persistence
   schema; phase7c pin now asserts the composed version (fixes Codex F9 at
   integration level).
6. **Onboarding × hardening** — `repository` onboarding step counts parked
   rows; every *connected* step honors `disconnected_at`.
7. **E2E × hardened rate limits** — `auth_dev_login_burst` setting
   (default 20 unchanged); e2e stack sets 500.
8. **Ops boundary respected** — deploy/scripts/*, backend/preflight.py,
   backend/ops_logging.py, backend/alerts.py, backend/api/ops.py,
   deploy/release_check.py untouched beyond what fork #5 supplied.

## Verification matrix (executed)

- Backend: **468 passed, 8 skipped, 9 xfailed** (xfails = tracked Codex defects)
- Security agents: **593 passed, 23 skipped**
- Shared contracts: 106 passed; `make check` (ruff, format, contract
  freshness, examples) clean; `uv run mypy` clean
- Frontend: 345 tests / 80 files; lint + typecheck + production build clean
- Transport: 35 tests
- Migrations: upgrade 0001→0017 and downgrade verified (test_preflight
  restores real head)
- **Real-stack Playwright: 49/49 passed** (5.7 min; production build, real
  Postgres, real worker, scripted model transports)
- Live Defense scenarios A–H, OFF-mode, cancellation, cross-project authz:
  covered by backend suite + e2e specs above

## Skipped / not run

- Live LLM evals (opt-in, cost money) — not run by design.
- `deploy/release_readiness.py` staging gate — owned by release-validation
  workstream (PR #7); pending run against this branch.

## Migration chain

0001 → … → 0015 → 0016 execution_reliability → 0017 repository_disconnect
(single head, upgrade + downgrade verified).

## Contract matrix

SCHEMA_VERSION 0.24.0. Reserved: 0.20 (6.5), 0.21 (base), 0.22 (7C),
0.23 (observability), 0.24 (integrated: Repository.disconnected[_at],
MonitoringEvent.test_event, OnboardingTestEvent).

## Deployment requirements (draft)

- Alembic head 0017 — preflight/readiness suites must expect it.
- `auth_dev_login_burst` default is production-safe (20); only test stacks
  raise it.
- `!.env.staging.example` negation arrives with PR #7 — keep it.
- Frontend must be rebuilt after transport changes (stale `.next` produced
  "Invalid Repository response" mid-verification).

## Unresolved P0/P1

- **P1 F1** — provider-request replay after worker crash (Grok)
- **P1 F2** — emergency/rollback/expiry jobs blocked by concurrency caps
  (Devin Reliability)
- P2 queue: F3–F5 Reliability, F6/F9 Observability, F7/F8 Grok, expanded
  crash/cancel regressions Devin E2E. All strict-xfailed → flip red on fix.

## GO/NO-GO

**NO-GO for customer deployment.** GO for review and for release-validation's
staging suite: integration is complete and its full test matrix is green, but
two P1 defects (F1, F2) remain owner-assigned and unresolved. Once owners land
fixes, recompose heads, unmark xfails, re-run matrix — then GO.
