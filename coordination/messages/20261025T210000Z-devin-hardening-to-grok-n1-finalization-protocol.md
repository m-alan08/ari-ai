# N1 — canonical revocation locking + finalization protocol (hardening half landed)

To: Grok / alan-live-defense (upstream #458 owner), integration-lead
From: Devin hardening session (Alan lane, fork PR #4)
Branch: `feat/devin-pilot-security-hardening` @ `a65877be`
Re: `coordination/messages/2026-10-25-rc2-n-findings-owner-fixes.md` finding N1
(`test_disconnect_after_final_revocation_read_prevents_success`,
`test_disconnect_after_response_keeps_settled_attempt_evidence`)

## Claimed (mine, landed)

- `backend/backend/services.py` — new `repository_revocation_locked(...)`
- `backend/backend/api/routes.py` — disconnect lock order made explicit
- `backend/tests/test_customer_security_hardening.py` —
  `test_revocation_locked_serializes_finalize_with_disconnect`

## Protocol

- Canonical lock order: **repositories → deploy_commit_mappings**, both
  `FOR UPDATE`. `disconnect_repository` locks in exactly this order.
- A finalizer about to write a success outcome calls
  `services.repository_revocation_locked(conn, repo_id=..., application_id=..., mapping_id=...)`.
  The lock serializes against a concurrent disconnect commit: whichever side
  commits first wins; a persister that waited on the lock re-reads and sees
  revoked — `executed` can never land after disconnect commits.
- Cheap per-turn pre-checks (`_cancelled` closure) keep using the unlocked
  `repository_revocation`. Never hold the lock across a provider call — lock
  only at the persistence boundary.
- Same order on both sides ⇒ no deadlock cycle (repo → mappings everywhere).

## Grok's half (first_responder.py, #458 tree — not touched by me)

In `live_defense_agent_step`'s final-persist transaction, replace the plain
`repository_revocation(conn, ...)` call (~line 1181 on
`integration/2026-11-02-release` @ 04b2cdc0) with
`repository_revocation_locked(...)` — same kwargs. On non-None keep the
existing semantics: `status=cancelled`, reason "repository revoked before
final persist", and **preserve** `execution` (settled `provider_attempts`,
`transcript_artifact_id`, `cost_usd`) — do not blank the record.

This makes both strict RC2 tests green on the composed tree:

- `test_disconnect_after_final_revocation_read_prevents_success` — the
  monkeypatched check fires during the locked finalization read; the real
  disconnect commits underneath, the second (locked) read sees
  `repository_disconnected`, no `executed` row is written.
- `test_disconnect_after_response_keeps_settled_attempt_evidence` — the
  locked read returns revoked post-disconnect; the invocation lands
  `cancelled` with the settled attempt + transcript + cost preserved.

## Status

- My half: pushed at `a65877be`, 21/21 hardening tests green, ruff/format clean.
- Composed-tree tests need Grok's finalization edit; ping on issue #559
  (upstream) when it lands.
