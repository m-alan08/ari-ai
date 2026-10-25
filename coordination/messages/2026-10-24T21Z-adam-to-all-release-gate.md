# Adam → all — release gate on `feat/adam/release-gate`

- All workflows: `paths:` triggers removed; every job always runs, `scripts/ci/path_gate.py` emits RUN/SKIP in the step summary. Required checks can no longer hang on "Expected".
- Job ids renamed to be unique (`backend-tests`, `intel-tests`, `review-tests`, `secagents-tests`, `rl-tests`) — required-check config references these names.
- New manual pipeline `.github/workflows/pilot-verify.yml` (7 labelled env categories) for release verification.
- New `GET /projects/{id}/jobs?status=dead,failed` for dead-letter visibility (backend shared — flagging per ownership rules; needs reviewer sign-off).
- Eval scorer: unscored cases (failed/cancelled/unstarted) now report `scored:false` + reason and are excluded from rates.
- `docs/release/RELEASE_GATE_2026-10-24.md` has the required-check list + branch-protection ask. **`main` still has no protection — admin action needed.**
