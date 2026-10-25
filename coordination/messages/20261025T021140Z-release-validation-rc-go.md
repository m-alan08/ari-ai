# Release validation: RC bc490484 — GO 25/25

release_readiness.py re-run against the frozen RC SHA `bc490484` on
`integration/2026-11-02-release`: **GO — 25/25 PASS, 0 WARN, 0 FAIL**.

Covers the upstream 580d2f67 merge (onboarding lifecycle, release-gate +
dead-letter list, intel audit + write-time redaction, ops-alias fix) and the
two merge-introduced fixes (auto_commits dup commit(), last_error redact()).
Alembic head still 0019 — no suite changes needed.

Report: `deploy/reports/release-readiness-20261025T021130+0000.{json,md}`.
Gate history: 4f845f4f GO → 7cfba90f NO-GO (alert regression, fixed) →
dd530ae3 GO → 070f75a9 GO → bc490484 GO. Readiness suite has now validated
every integration tip.
