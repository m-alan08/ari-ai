# Release-readiness harness available (feat/devin/release-validation)

To: all / integration workstream
From: Alan track (release validation)

`deploy/release_readiness.py` on `feat/devin/release-validation` (stacked on
`feat/alan/pilot-deployment-ops`) now produces an executed GO/NO-GO readiness
report for the pilot stack: 24 real checks — staging preflight (negative+positive),
migrations, API+worker boot+drain, smoke 15/15, job execution, alert webhook with
secret redaction, SIGKILL crash recovery via lease expiry, real
pg_dump/pg_restore round-trip, CSRF/non-member/owner-only authz gates,
emergency-stop terminal status, off-mode default, artifacts, release_check.py.

First run: **GO — 23 PASS, 1 WARN, 0 FAIL.** Reports land in `deploy/reports/`.

Two notes for the integration workstream:
1. When `integration/*` lands on the fork, this branch will re-run the full
   suite against it — treat its report as the pilot gate.
2. Bug found in PR #5: `.env.staging.example` was never committed — `.gitignore`
   `.env.*` swallowed it. Fixed on this branch with `!.env.staging.example`.
