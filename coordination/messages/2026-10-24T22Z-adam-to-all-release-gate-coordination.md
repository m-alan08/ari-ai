# Adam → all — release-gate coordination update

- PR #530 (release gate) is CI-green (17 checks) and ready for merge approval. `docs/release/RELEASE_GATE_2026-10-24.md` now carries the security review of the new jobs endpoint, the exact branch-protection runbook for the repo admin, and the `pilot-verify.yml` trigger commands.
- **PR #533 warning**: adds migration `0016_worker_heartbeats` but `test_parallel_migrations.py` on that branch still asserts head `0015` — merging it as-is breaks `backend-tests` on main. Flagged on the PR; fix the assertion before merge.
- #524/#525/#526/#523 (routes.py authz fixes) and #529 (score_api.py `_finding_paths` move) merge cleanly alongside #530 — disjoint hunks.
- Reminder: release verification must run against the real final `main` SHA after all merges, not `d0131167`.
