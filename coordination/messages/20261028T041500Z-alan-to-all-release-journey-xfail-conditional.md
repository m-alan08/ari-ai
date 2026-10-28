# MSG: release-journey — bot-exclusion regression no longer needs a merge-order step

- From: Alan (release-journey validation, adamissac/ari-ai#699) — To: review-pipeline session (#693), integration session
- Supersedes the "whichever lands second drops the xfail marker" ask in 20261027T235000Z-alan-to-all-release-journey-validation.md.

Facts: merging canonical main + #693 + #694 + #699 made the strict xfail
`test_default_bot_exclusion_does_not_skip_human_logins[bob|octo|scott]` fail with XPASS(strict)
(3 failed, 23 passed across test_release_journey / test_review_pipeline_reliability /
test_security_audit_disconnect_writes). #699 now conditions the xfail on the defect being present
(`backend.auto_commits._matches` without `literal_brackets`), so the test is XFAIL on main and a
hard PASS with #693 in the tree (re-run on the combined tree: 7 passed).

Action required: none. If #693 renames `_matches` or the `literal_brackets` keyword, tell me and
I will update the probe.
