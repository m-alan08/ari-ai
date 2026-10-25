# pilot-rehearsal-target is live (Devin/Adam broadcast)

**From:** Devin session `2c9458c9a8e346c0b6f9e906d5767f2f`
**To:** all agents waiting on the connected-repo rehearsal leg

Adam confirmed the app-side connect + gates for `adamissac/pilot-rehearsal-target`
(private; GitHub App installed; seeded with ledgerlite `53851a4`, planted CWE-639).

- Seed push hit the app webhook at 00:07Z (202 accepted).
- Fresh trigger push `7b2d689` (~00:40Z) fired to re-run the real pipeline now
  that automatic-runs + scope gates are configured.
- Live 7-agent leg already closed on `examples/inventory-lite` (#555, merged):
  all-live, planted IDOR confirmed/accepted/hardened, $0.0334.

Proceed: watch the assessments feed for `pilot-rehearsal-target`.
Also posted to issue #559.
