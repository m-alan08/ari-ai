# WS2 E2E: rebased onto integration/2026-11-02-release; F7 xfail dropped

- Branch `feat/devin-live-defense-e2e` rebased onto `dd530ae3` (PR #8 head).
  Only my 2 post-`fcca6ed7` commits replayed; the sandbox-target commit was
  skipped — identical fix already landed via cb6de511 / upstream #533.
- **F7 verified fixed through the production enqueue path**: integrated
  `_enqueue_agent_step` passes `decision.deadline_at` to `jobq.enqueue`, so
  `jobs.deadline_at` is populated on `live_defense_agent_step` jobs.
  `test_agent_step_jobs_have_durable_deadline` now passes as a strict
  assertion (marker removed — your unmarked version was taken verbatim).
- All F1–F8 run strict: `pytest -m live_defense_e2e` → **26 passed, 4
  skipped, 0 xfailed** on the integrated head.
- Kept additive asserts only: scout `status != executed` on unresolved
  attempt (F1 test), transcripts>=1 + diagnose skipped_no_prerequisite
  (scenario E). No production code touched, no integration files overwritten.
- PR #2 base retargeted `base/phase7c-execution-reliability` →
  `integration/2026-11-02-release`; diff is test-only.
- Dropped `test_defer_does_not_consume_failure_attempts` (jobs_queue): the
  `test_wait` handler is gone on the integrated head and B's
  `test_deferral_does_not_consume_retry_budget` already covers it.
