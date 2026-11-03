# Staging pilot GO/NO-GO — recommendation-only, owner-authorized

- Base: upstream `adamissac/ari-ai` main `c03a5501`; branch `devin/*-staging-pilot-readiness`.
- Scope: one isolated staging deployment, owner-authorized test repositories,
  recommendation-only. No production deploy, no customer credentials, no AUTO-EXECUTE.
- Procedure: [STAGING_PILOT_RUNBOOK.md](../../deploy/STAGING_PILOT_RUNBOOK.md) ·
  gates: [RELEASE_CHECKLIST.md](../../deploy/RELEASE_CHECKLIST.md).

## Verdict

**Code + local staging rehearsal: GO. Staging pilot launch: NO-GO** until the
four staging-environment gates below (S1–S4) have evidence. Nothing in this
session had staging access, so none of them is claimed.

## Gate evidence

Evidence files are in [`evidence/`](evidence/) (copied from the gitignored
`deploy/reports/` of the local rehearsal run on commit `e986bcff`).

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Deployment config validation | PASS (local) | `test_staging_pilot.py` (isolation, posture, OPS_TOKEN, dev login, local sandbox; secret values never echoed); rehearsal `preflight.gate` / `preflight.config` |
| 2 | Migration + rollback safety | PASS (local) | rehearsal `migrate` (head `0021`), `restore` drill into scratch DB at head; `test_parallel_migrations.py` upgrade/downgrade round-trip; no new migration in this PR |
| 3 | Worker + queue health | PASS (local) | rehearsal `boot.worker`, `worker.sigterm`, `job.success`, `worker.recovery`; smoke `readyz`, `ops status exposes queue` |
| 4 | Structured logging + secret-safe diagnostics | PASS (local) | rehearsal `logs.json`, `logs.redaction`, `logs.redact_stream`; preflight-message test |
| 5 | Provider outage handling | PASS (scripted) | `test_provider_outage_fails_the_assessment_visibly` (retryable 503 → assessment `FAILED`/`LLM_UNAVAILABLE`, no fabricated result); `test_llm_gateway.py` retry/deadline tests. Live provider smoke not run (opt-in, costs money) |
| 6 | Stuck assessments + failed jobs monitoring | PASS (local) | new `ops/status` `stuck` / `failed_jobs_24h`; `test_ops_status_reports_stuck_*`, `test_assessment_awaiting_a_human_*`; smoke `no stuck jobs` / `no stuck assessments`; release check `stuck assessments/jobs` PASS |
| 7 | Backup + recovery documented and drilled | PASS (local) | rehearsal `backup` + `restore`; release check `backup readable` (`pg_restore --list`); runbook §7–8 |
| 8 | Rollback + emergency disable | PASS (local) | rehearsal `control.estop`, `control.estop_clear`, `authz.owner_gate`; release check cancellation + emergency-stop mounted; runbook §6–7 |
| 9 | Repeatable smoke suite | PASS (local) | [`evidence/smoke_staging.log`](evidence/smoke_staging.log): 20 passed, 0 failed, incl. recommendation-only and direct-delivery-disabled |
| 10 | Release checklist | PASS (local, non-strict) | [`evidence/release_check.log`](evidence/release_check.log): 21 checks, 0 failed; 4 WARN + 1 SKIP are local-only (http origins, unset SESSION_SECRET, intentional failed probe job, no PR CI yet) |
| 11 | Recommendation-only enforced | PASS | `test_recommendation_only_*` (no owner-delegated approval, run stops at `AWAITING_APPROVAL`, no `deliver_verified_patch` job, no GitHub call, `push_patch` → `FORBIDDEN`); smoke + release check posture rows |
| 12 | First-responder OFF / no AUTO-EXECUTE | PASS (local) | rehearsal `control.off_mode`; release check DB + ops rows; there is no AUTO-EXECUTE mode in the server |
| 13 | Full regression suite | PASS | `uv run pytest -q`: 1370 passed, 17 skipped (incl. opt-in live LLM smoke and specialist data-cache digests); `make check` clean; `uv run mypy`: no issues in 41 files |
| S1 | Staging deployed from this commit, preflight green at boot | **MISSING** | needs staging Railway access |
| S2 | Remote gates: `smoke_staging.sh` + `release_check.py --strict` against staging | **MISSING** | `--strict` must exit 0 (no WARN/SKIP) |
| S3 | Staging backup + restore drill on the staging database | **MISSING** | runbook §8 |
| S4 | One real-model assessment on an owner-authorized staging repository, ending at `AWAITING_APPROVAL` with no PR/push | **MISSING** | staging-only Anthropic key; never a customer key |
| S5 | PR CI green | PASS (e244dc26) | adamissac/ari-ai#695: 21 checks passed, 0 failed; skipped: `live`, `publish`, `pin` (opt-in/release-only), `strict-regressions`, `independent-codex`, `CodeRabbit` (external reviews). Re-verify on the final head |

## What changed (why the verdict needed it)

- Before this PR, `pull_request` (the default delivery mode) auto-approved
  remediation on the owner's behalf and opened PRs, so a pilot could not be
  recommendation-only. `PILOT_RECOMMENDATION_ONLY` (on by default in staging)
  removes that authority and refuses repository writes in `push_patch`. Build-recipe
  PRs are skipped too.
- Staging preflight now fails on production hosts in API/frontend/DB URLs,
  recommendation-only off, `ALLOW_AUTO_DELIVERY=true`, or missing `OPS_TOKEN`.
- `ops/status` now reports stuck work (`execution.stuck_jobs` plus the new
  `execution.stuck_assessments`), failed jobs in the last 24h, and the pilot
  posture. It also no longer crashes when a running job has no heartbeat yet.
- `release_check.py --strict` makes WARN/SKIP block, so "every gate has evidence"
  can be checked by a machine. Codex release review found two holes in the CI gate,
  both reproduced and fixed with regression tests: an empty check list reported
  `PASS all 0 checks green`, and SKIPPED/no-conclusion checks (e.g. gitleaks, audit)
  counted as green. Now an empty rollup FAILs, skipped checks are named and block
  under `--strict`, and only `--allow-skipped-check NAME` opt-in jobs may be skipped.
  Follow-up hardening of the same gate: commit-status rows (`state`/`context`, e.g.
  Devin Review) are read like check runs instead of being misfiled as pending, and
  strict sign-off requires every `REQUIRED_GATES` entry to have produced a row —
  a gate that never ran (`--no-http`, exception) is a FAIL, not silence.

## Residual risks

- `PRODUCTION_HOSTS` defaults to the two known production hosts. The operator
  must confirm the list matches the real production database host, which is not
  in the repository.
- Idle workers are not listed (lease-derived visibility). A dead worker fleet
  shows up as `queued_starved` after `JOB_STUCK_PENDING_S`, not immediately.
- Live provider behaviour is covered by scripted transports only, until S4.
