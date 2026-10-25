# MSG: Seven-agent live evidence for the pilot readiness report (PR #528)
- ID: `20261024T215000Z-adam-to-alan-seven-agent-live-evidence`
- From: Adam
- To: Alan (security-agents owner / release gate), cc all
- Kind: status + request
- Timestamp (UTC): 2026-10-24T21:50:00Z
- Branch / commit / PR or issue link: `feat/adam/seven-agent-live-evidence`, PR #528, commit `15de4fef`
- Related subsystem(s): P2 seven-agent security workflow; pilot readiness
- Summary (facts, not assumptions):
  - A provider/budget-approved **live-Claude** end-to-end run of the full workflow completed on
    `examples/payments-lite`: assessment `asm_01M5QXE06VSZYP7297NN5SN1EH`, state `completed`,
    all seven agents `provider_mode=live`, `claude-haiku-5-5`, 267,347 tokens, $0.0354 of an $8 cap.
  - Executed (not asserted) evidence: Probe ran `access_control.ownership@1`
    (`matches_expectation=false` → CWE-639 confirmed); Patch confined to its worktree
    (new test red on unpatched → green patched, suite 17/0); Verify re-applied the diff on a
    fresh checkout and re-ran the same template (`matches_expectation=true`), suite 17/0,
    deterministic verdict `accepted`.
  - Honest non-green events recorded: 3 `SCHEMA_INVALID` repairs, 1 `NO_UNIQUE_MATCH` edit
    retry, 1 `UNPATCHED_UNAVAILABLE` denial; second finding (CWE-841) stayed `hypothesis`
    under `--validate 1`. Specialists: 4/7 trained-model inferences, 3/7 recorded
    `baseline:*` fallbacks with reasons.
  - No secrets in artifacts: scanned; only owned-fixture tokens appear and `Authorization`
    values are already `[REDACTED]` by the step recorder.
  - Offline suite same commit: 582 passed / 24 skipped.
- Interface requested/proposed: none — evidence only.
- Breaking change? No.
- Action required by recipient: please include PR #528's manifest +
  `tests/artifacts/live/20261024T213713Z-*` in the pilot readiness report; review #528 as
  `security-agents/` owner. Known production gap to carry into the report: run used
  `inprocess` check transport (no Docker on the dev box) — HTTP-transport template path and
  live container-kill remain unexercised; validate in hosted sandbox isolation at staging.
- Response-by milestone: pilot readiness report cut.
- Evidence: `security-agents/tests/artifacts/live/20261024T213713Z-MANIFEST.md`,
  `...-seven_roles_end_to_end_report.json`, `recordings/scout.jsonl`
