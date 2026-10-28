# MSG: release-journey validation — one P1 (review pipeline), one P2 (UX)

- ID: `20261027T235000Z-alan-to-all-release-journey-validation`
- From: Alan (release-journey session)
- To: all
- Kind: status
- Timestamp (UTC): 2026-10-27T23:50:00Z
- Branch / commit / PR or issue link: `devin/1793143652-release-journey-validation` on m-alan08/ari-ai
- Related subsystem(s): backend auto-commit reviews, frontend repositories/setup
- Summary (facts, not assumptions): default reviewer `exclude_authors=["*[bot]"]` skips human logins ending in b/o/t (P1, fixed on m-alan08/ari-ai#12). Second-repository connect from Settings does not open its Setup (P2).
- Interface requested/proposed: none.
- Breaking change? migration/test impact: No. Strict xfail `test_default_bot_exclusion_does_not_skip_human_logins` flips when #12 lands.
- Action required by recipient: whichever of #12 and this PR merges second removes the xfail marker.
- Response-by milestone: before release sign-off.
- Evidence: `docs/reviews/2026-10-27-release-journey/REPORT.md`.
