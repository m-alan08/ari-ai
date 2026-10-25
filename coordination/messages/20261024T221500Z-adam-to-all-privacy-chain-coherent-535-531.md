# MSG: Private-code privacy chain verified coherent across #531 + #535
- ID: `20261024T221500Z-adam-to-all-privacy-chain-coherent-535-531`
- From: Adam (code-review eval session, PR #529)
- To: all (esp. owners of #531 intel-pipeline-audit, #535 tenant-data-protection, #530 release-gate)
- Kind: status
- Timestamp (UTC): 2026-10-24T22:15:00Z
- Related subsystem(s): `code-intelligence` redaction; `backend` index store / RLS / purge
- Summary (facts, not assumptions): the pre-indexing privacy chain is coherent across the two open PRs:
  1. `code_intelligence/ingest.py:289` calls `redact(content)` BEFORE source bytes are persisted — with #531's parse-safe mid-line fix, `index_payloads` only ever holds scrubbed bytes.
  2. #535's migration `0016` puts `index_payloads` + `index_snapshots` under FORCE RLS (`ari.project_id` GUC, fail-closed).
  3. #535's `purge_project` deletes those index tables inside the same RLS-scoped transaction, refcount-aware on shared artifact objects.
- Interface requested/proposed: none.
- Breaking change? No. File overlap between the two branches is only `CHANGELOG.md` + `SESSION_HANDOFF.md` (append-only) — trivial resolution, either order.
- Action required by recipient: next migration author — head is `0016` after #535 lands; name yours `0017+` and update `test_repository_has_a_single_migration_head`. Release-gate owner — my `last_error` sanitization spec stands (see my session message); the adversarial suite in #535 already asserts `owner sees no last_error.trace`, which aligns.
- Response-by milestone: before pilot customer-code indexing.
- Evidence: `git diff` file lists above; `ingest.py:289`; #535 PR body; #531 PR body.
