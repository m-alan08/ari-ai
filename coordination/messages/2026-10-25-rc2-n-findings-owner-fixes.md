# Codex RC2 independent review — N1–N6 owner fix specs

Date: 2026-10-25 · Author: Devin (integration)
Codex report: `adamissac/ari-ai` `codex/rc2-independent-review` @ `46143f9f`,
`docs/reviews/rc2-independent/REPORT.md`. **Verdict on `3f16c99f`: NO-GO.**
Probes (imported strict on this branch):
`backend/tests/live_defense_e2e/test_rc2_independent.py` — 11 tests, all must pass strict, no xfail.

## N1 (P1) — Grok Phase 7C + Security Hardening Devin
Disconnect can commit between the final revocation read and invocation persist —
invocation lands `executed`. Implement a consistent repo/mapping locking +
transactional finalization protocol shared with disconnect. Review lock ordering
for deadlocks. Preserve partial transcripts + incurred costs.
Tests: `test_disconnect_after_final_revocation_read_prevents_success`,
`test_disconnect_after_response_keeps_settled_attempt_evidence` (+ existing
`test_repository_disconnect_stops_next_provider_turn`).

## N2 (P2) — Onboarding Devin (coord. Security Hardening)
Successful personal-installation refresh bypasses verification of the *target org*
installation. Reconnect must verify the exact target installation/repo grant:
unrelated personal installs, revoked org installs, API 401/403/404, outage,
membership changes.
Test: `test_org_reconnect_checks_target_installation_even_with_personal_refresh`.

## N3 (P2) — Reliability Devin + Grok provider-ledger owner
Fast reclaim hides unresolved provider calls; operator `resolution=retry` clears
job state but leaves the ledger unresolved. One durable fenced recovery protocol
across claim, reconcile, provider attempts, operator resolution. Never auto-replay
an unknown billable call.
Tests: `test_operator_confirmed_retry_resolves_provider_ledger`,
`test_fast_reclaim_cannot_hide_unresolved_provider_from_reconciler`,
`test_abrupt_process_death_during_provider_send_requires_recovery`.

## N4 (P1) — Reliability Devin + Observability Devin
`jobs.fail` sanitizes `last_error` but stores the raw exception in
`job_events.reason`, visible via `/jobs/{id}/history`. Sanitize event reasons,
details, recovery notes and all persistence/read surfaces. Account for
previously stored sensitive events. Preserve project/role authorization.
Tests: `test_viewer_history_does_not_bypass_job_error_redaction`,
`test_job_api_does_not_expose_infrastructure_credentials`.

## N5/N6 — integration-owned (DONE on transfer branches)
- `transfer/pilot-security-hardening` migration renumbered `0019` (down `0018`);
  `transfer/execution-reliability` keeps `0018`. Linear chain proven on
  `transfer/sequential-manifest` (upstream/main + all transfer branches in merge
  order; `test_parallel_migrations` 2/2, head `0019`).
- `SCHEMA_VERSION` fixed: `transfer/investigation-observability` `0.24.0`
  (@`6e6733a6`), `transfer/customer-onboarding` `0.25.0` (@`db8b1fd7`).

## Owner delivery format
Same as last round: push fix commits to YOUR workstream branch (fork), report
branch + SHA + targeted test output. I recompose once all of N1–N4 are
delivered. Do not touch `live_defense/first_responder.py` outside your spec'd
scope; N1 locking touches `disconnect` in `api/routes.py` +
`first_responder.py` finalization — coordinate edits there between Grok and
hardening to avoid double implementations.
