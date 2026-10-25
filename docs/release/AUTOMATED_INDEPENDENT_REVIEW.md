# Ari automated independent review cycle

Implementation: versioned GitHub Actions and trusted controller tooling. Activation
requires installation on the fork's trusted branches and the one-time account
setup below. No paid calls or production release is implied by this document.

## Flow

1. A new commit on fork integration PR #8 triggers `Ari candidate review` from the
   PR's **trusted base**, currently `ci/upstream-main`. The controller pins the
   candidate, canonical main, policy, original regression suite and transfer heads.
2. Strict independent regressions run in a disposable, resource-limited container
   with an internal-only network to disposable PostgreSQL. Candidate dependencies,
   conftest and executable code never run on a credentialed runner. Independent
   tests come from the pinned prior audit, not from mutable candidate assertions.
3. Optional native Codex automatic review supplies supplementary GitHub findings.
   A deeper exact-SHA audit uses the commit behind `openai/codex-action@v1` with
   a version-controlled prompt and JSON schema, a separate read-only environment,
   shell tools disabled, and the source/test receipts supplied as untrusted data.
4. A deterministic publisher validates F1–F10/N1–N7 coverage, CI, migration receipt,
   source completeness and upstream/transfer production-tree equivalence. It updates
   one concise signed GO/NO-GO comment and stores full JSON/log artifacts.
5. Actionable findings become deduplicated GitHub issues assigned to `m-alan08`,
   with the **existing logical workstream and branch** explicitly recorded. The
   model cannot select arbitrary assignees or destinations.
6. If explicitly configured, a serialized notifier resumes the appropriate
   existing Devin session. Once all assigned workstreams push new fix heads and
   **those exact heads** pass required CI, it notifies the existing integration
   session. That owner prepares a new candidate on the integration branch; fresh
   CI and independent review follow. The workflow never merges a release PR.

Native review is preferred as an additional fast review surface. It is not a
substitute for execution evidence, P2 coverage or merge-equivalence verification.
Only the configured native bot's reviews bound to the candidate SHA are imported.
Native silence, a thumbs-up, missing evidence or pending CI cannot produce GO.

## One-time installation and authorization

1. Review and merge the automation-only installation PRs **manually**: canonical
   `adamissac/ari-ai:main` for the reusable source; fork `main` for trusted status
   refresh; fork `ci/upstream-main` for PR #8's pull_request_target controller.
   Do not merge the integration release as part of this setup.
2. A fork administrator runs `python3 -m scripts.ari_review.bootstrap --apply`.
   This creates restricted CI credential environments, requires human CODEOWNER
   review on trusted controller branches, protects the budget branch,
   creates a signed fast-forward-only budget ledger and generates an internal
   state-signing key. It never creates OpenAI/Devin credentials or enables paid
   calls. Existing protection/ledger/key material is not weakened or reset.
   A failed bootstrap preserves its private recovery file for an explicit retry;
   it is removed once setup succeeds. Never commit or share that file.
3. Connect **both relevant GitHub repositories** in Codex settings if the account
   has access, and enable native Automatic review with the desired update trigger.
   This requires an actual Codex account/repository connection; GitHub access in
   this chat does not establish it. Do not add bots that comment `@codex review`
   in response to review comments. Set native-account usage controls separately.
4. For the deeper audit, add `OPENAI_API_KEY` **only** to the fork environment
   `ari-independent-review`, not repository-wide secrets. Use a dedicated API
   project/service identity and authorize the pinned model. After verifying the
   key, dated pricing policy and billing controls, set `ARI_REVIEW_API_ENABLED=true`
   in `ari-review-control` variables. Project alert budgets are not a substitute
   for the enforced request/output ceilings. Native Codex access does not supply
   an API key automatically.
5. For existing-session Devin continuation, add `DEVIN_API_KEY` **only** to
   `ari-devin-handoff`. Use a service user with `ManageOrgSessions`, scoped to the
   intended org. Configure `ARI_DEVIN_BUDGET_ATTESTATION` in that environment's
   variables, then set `ARI_DEVIN_ENABLED=true`. Confirm real existing session IDs,
   org and native hard ACU limits; do not infer them from historical session links.
   Devin's v3 message API cannot set or retrieve the native hard cap. If the
   existing session's cap cannot be confirmed, automatic continuation stays OFF.
6. Run the fork's `Ari candidate review` workflow once after installation (or push
   a new candidate). Verify isolated CI receipts, signed artifacts, assignments
   and configured handoff before relying on unattended operation. No prompts or
   review results need to be copied between agents after activation.

Both trusted branches must have the complete human CODEOWNER guard before paid
execution is admitted. Personal repositories use required CODEOWNER PR reviews;
actor-specific push restrictions are available only where GitHub supports them.
Use the existing Devin GitHub App/write identity, not a human owner/admin PAT;
fixer identities must not bypass protected controller-branch review.

No one should paste credentials in chat, issues, review prompts or PR bodies.

Example non-secret Devin budget confirmation (replace with verified values):

```json
{
  "org_id": "org-YOUR_ORG",
  "expires_at": "2026-11-25T00:00:00+00:00",
  "sessions": {
    "reliability": {
      "session_id": "devin-VERIFIED_EXISTING_SESSION",
      "native_hard_cap_confirmed": true,
      "hard_acu_limit_total": 12
    },
    "integration": {
      "session_id": "devin-VERIFIED_INTEGRATION_SESSION",
      "native_hard_cap_confirmed": true,
      "hard_acu_limit_total": 32
    }
  }
}
```

The notifier checks actual session identity, org, consumed ACU and idle/finished
state. The confirmed native cap must leave **at most 2 ACU**. Busy work, approval
waits, exhausted/error sessions, missing owners or expired confirmation block
continuation. It never creates sessions, guesses destinations or raises budgets.

## Explicit bounds and stop conditions

- At most **3 candidate regression cycles and 3 paid reviews per UTC day**.
- At most **one paid audit per immutable candidate SHA**. Job/workflow reruns do
  not spend again. No automatic paid retries after failures or ambiguous sends.
- Audit API gate: at most **10 Responses requests**, fixed snapshot
  `gpt-5.4-mini-2026-03-17`, at most **8,192 output tokens per request**, no hosted
  paid tools/background work/extended cache retention/nonstandard service tier.
- Conservative dated-price reservations: **$0.50/request, $5/review, $15/day**.
  The gate reserves before every send and does not refund ambiguous failures.
  These USD figures use the documented model prices; they are not a promise that
  provider prices can never change. Policy expires on **2026-11-25** and blocks
  paid requests until an owner reviews rates. Request/token ceilings remain hard
  controls. Configure native/provider billing controls for an invoice-level cap.
- At most **2 automated fix rounds per integration PR**, **6 existing-session
  messages total**, **4/day**. With confirmed native remaining caps, each resume
  can consume at most 2 additional ACU; no automatic budget top-ups.
- Regression runner: 45-minute job limit (15-minute dependency build and
  25-minute test process bounds); 4 CPU / 8 GiB / 512 processes for tests.
- Status refresh makes no OpenAI calls. It imports current CI/native findings and
  may invoke only the bounded, configured Devin handoff. It does not trigger more
  reviews merely because a review comment/artifact appeared.
- When a cap, integrity check or prerequisite fails: NO-GO / blocked owner action.
  Do not silently skip tests, weaken assertions or start replacement sessions.

Superseded queued events may coalesce; they are never certified. Every production
candidate actually reviewed is immutable. A changed candidate, policy or upstream
invalidates approval and requires fresh evidence. A required CI result that is
missing, pending, skipped, cancelled or failed is unverified, never green.

## Security and merge boundaries

Trusted source: protected fork `main` / `ci/upstream-main`. Candidate branches must
not access review environments. Internal state keys exist only in review-control
and Devin-handoff environments; the model has neither state nor Devin credentials.
The actual OpenAI key stays in a root-owned fixed-upstream proxy, while the action
receives only a local bounded capability and drops privileges. Its last step is
Codex; publication happens in a different job with no candidate execution.

Budget state is a signed snapshot on protected branch
`automation/ari-review-ledger`, with no force pushes/deletion and one signed Git
parent per update. Deleting bot comments cannot reset spend. Signed evidence and
summary verification also reject forged `github-actions[bot]` comments. Candidate
container output is mounted separately from trusted pins/receipts; symlink/entity
attacks against result ingestion are rejected.

The production-tree comparison is deliberately strict. An incomplete sequential
manifest or unresolved latest-main conflict is NO-GO; manually resolved unions
must be committed and tested, not inferred. The controller does not resolve
conflicts or promote a checkpoint. Same-SHA CI plus model prose alone cannot merge,
deploy or change customer enforcement. Human release authority remains separate.

## Maintenance and validation

Policy and owner/branch mappings: `.github/ari-review/policy.json`.
Prompt/schema: `.github/ari-review/review-prompt.md`, `review.schema.json`.
Original independent regressions remain pinned to audit commit `abf6b316`; changing
that reference is a reviewed security-policy change, not candidate-controlled.
SHA inputs in the transfer/latest-main probes are refreshed without changing their
assertions. A legitimate incompatible contract change requires an explicit reviewed
suite update rather than an automatic skip.

Local validation: `python -m pytest scripts/ari_review/tests -q` and Ruff checks.
`Ari review tooling` runs these in a networkless container and runs actionlint.
The implementation machine has no Docker daemon; full container/model/session
activation must be verified in GitHub before claiming an operational live loop.
No paid API calls or live Devin resumes were used for local testing.

Official sources assessed:
[Native Codex GitHub review](https://learn.chatgpt.com/docs/third-party/github),
[Codex GitHub Action](https://learn.chatgpt.com/docs/github-action),
[official action v1 source](https://github.com/openai/codex-action/tree/bdf19a4a223ec2549a3e2274a0cf61556bc07675),
[model limits/prices](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Devin v3 existing-session messages](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages),
[Devin v3 session details](https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session).
