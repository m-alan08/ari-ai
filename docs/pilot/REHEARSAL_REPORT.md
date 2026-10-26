# Staging Rehearsal Report

- **Rehearsal date:** 2026-10-24 (local legs); production Leg 7 2026-10-25 01:26–02:14 UTC, Leg 8 04:16–05:50Z, and Leg 9 at 09:21Z
- **Code under test:** local legs `main` @ `d0131167`; production Leg 7 Railway `readyz.git_sha` = `0f311913` (`main`, schema 0.22.0, migration head 0017); Leg 8 ran `497372ff` (#587, delivery from a delivery-only sealed artifact); Leg 9 followed successful deploy `f2733519` at 09:09Z
- **Environments:** (a) production — Railway API `ari-ai-production-76d4.up.railway.app` + Vercel frontend `ari-ai-lake.vercel.app` + real GitHub App + live Anthropic; (b) local real stack — FastAPI TestClient + real PostgreSQL + in-process worker + scripted `FakeTransport`.
- **Honesty rule (per Adam):** each stage is labelled `live Claude` / `scripted` / `sandbox` / `real I/O`. Nothing skipped is claimed as passed.

## Result summary

| # | Verification | Leg | Result |
|---|---|---|---|
| 1 | Tenant isolation (cross-org ID substitution) | Local real stack | **PASS** — 34 probes, uniform 404, no existence leak |
| 2 | Real execution / labelling | Local + prod | **PASS** — live leg executed: every agent run `provider_mode=live`; scripted stages labelled |
| 3 | GitHub safety (PR/manual only, scoped write tokens, no agent merge/close) | Code audit + prod | **PARTIAL** — Leg 9 left exact-bytes PR #5 open and unmerged; C9 awaits Adam's personal review, and the no-touch directive was posted to #559 |
| 4 | Monitoring: opt-in controls, limits, cancel, budget caps | Local + prod | **PASS** — prod cancel, budget, scope-revoke drills; opt-out toggle persisted (no push event landed in the window — behaviour evidence is the local leg) |
| 5 | Defense Lab: recommendation-only | Code + tests | **PASS** |
| 6 | Data privacy: storage, provider processing, artifacts, deletion | Local + code + prod | **PASS for DB isolation after the `ari_app` cutover** — the hosted cross-org drill passed under the app role (Leg 9); app-layer membership checks also held (P2) |
| 7 | Reliability: failure recovery, persistence, restarts, UI errors | Local + prod | **PASS with findings** — prod outage recovered; UI showed accurate `SUBSYSTEM_UNAVAILABLE`; see D2–D4 |
| 8 | Hosted C9 re-run: Verify-tested sealed artifact → PR | Production | Delivery mechanism **PROVEN** (#2 and #4); C9 closure **OPEN** pending Adam's personal review of #5 |
| 9 | Hosted C9 repair as `ari_app`: Verify-tested bytes → open PR | Production | **PASS** — PR #5 is open and unmerged; exact-bytes tree match verified; C9 awaits Adam's personal review |
| P1 | Prod journey: install → connect → index → live assessment → findings → repair → verify → results | Prod | **PASS through independent Verify; exact-byte PR delivery proven in Leg 9; C9 remains OPEN for Adam's personal review** |
| P2 | Prod tenant isolation and least-privilege DB role | Prod | **PASS** — app runs as non-superuser `ari_app`; hosted cross-org drill returned 404 on all 14 GETs |

## Leg 1 — Two-org isolation (real stack, scripted LLM)

**Test:** `backend/tests/test_pilot_rehearsal.py::test_two_org_isolation_by_id_substitution`.
Real FastAPI app + real PostgreSQL + real worker (`worker.drain`), two independent
personal orgs (alice+bob vs eve), full scripted seven-agent loop completed first so
every resource type genuinely exists.

**Probe matrix (eve, authenticated non-member, substituting org-A IDs):** project,
repo list/detail, commits, snapshots list+POST, scopes list, scope approve+revoke,
assessments list+POST+GET+runs+events+events/log+report+RoE+cancel, findings
list+detail+patches+accept-risk, artifacts (diff + LLM transcript), project events,
reassessments, approvals, defense-recommendations, defense-health, pilot-report,
ingest-tokens, applications, control-grants, context-pack POST, jobs, plus
fabricated `fnd_/asm_/art_` IDs.

**Result:** every probe → **404** (uniform not-found, no existence leak). Eve's own
API works (200) with empty lists — isolation, not broken auth. Context-pack POST on
a foreign project is denied. `provider_mode=fake` is asserted on every agent run —
scripted turns are identifiable in stored data, matching the "labelled" contract.

## Leg 2 — Monitoring controls, cancel, opt-out

`test_cancel_optout_and_reoptin_drills` + existing
`test_cancel_before_start_and_revoke_stops_assessments`:

- `POST /assessments/{id}/cancel` → terminal `CANCELLED`; second cancel → honest 409.
- `PATCH /repositories/{id} automatic_assessments=false` → subsequent webhook push
  yields **zero** reassessments; re-enabling restores the trigger.
- Scope `revoke` → outstanding assessment `CANCELLED`, new ones `SCOPE_DENIED`.
- Budgets (code-verified): 400k tokens / $10 / 1800s per manual assessment; 150k /
  $3 / 900s monitoring; 5 starts/hour; 60 req/min API; auto runs deferred at 5/h &
  20/d per project; $50/day global auto cap.

## Leg 3 — Operator deletion procedure

`test_operator_deletion_procedure` (direct-SQL teardown) + PR #535's own
`test_purge_project_deletes_every_surface` (all 17 adversarial tests pass —
verified by re-running them on the PR's code). **Finding:** foreign keys have
**no cascades** — there is no single-row delete. The canonical operator entry
point is `purge_project` (`backend/privacy.py`, PR #535 +
[TENANT_DATA_PROTECTION](https://github.com/adamissac/ari-ai/pull/535)),
which performs the org-scoped teardown below:

1. Delete org-scoped rows across every table having `organization_id` (enumerated
   via `information_schema`), child tables first (FK-safe order or a maintenance
   transaction).
2. Delete `repositories` and `projects` rows.
3. Delete disk objects: artifact files under `$ARTIFACT_STORE_URI` (**note:**
   content-addressed sharing means cross-tenant refcounting is required before
   unlinking — an open gap, R-11-adjacent) and the bare clone at
   `$SECURITY_WORKDIR/repos/{repo_id}.git`.
4. Verify: after the drill every endpoint (project, repo, assessment, finding,
   events) returned 404.

**30-day SLA enforcement (operational):** deletion requests are logged in
`coordination/DELETION_LOG.md` (to create) with timestamp + requestor; an operator
runs the procedure above, records completion, and the SLA clock is tracked by a
weekly automation checking open requests older than 25 days. There is no
self-serve deletion and no automated purge — this is disclosed in
`DATA_HANDLING.md`.

## Leg 4 — Reliability

- Worker-death job reclaim: `test_a_claim_held_by_a_job_that_stopped_running_can_be_reclaimed` — PASS (lease expiry lets another worker resume; no stranded assessment).
- Persistence: all assessment/agent/artifact/finding state is Postgres + artifact store; report served "from persisted records".
- UI error reporting: frontend `assessment-recovery.test.tsx` + error/retry states covered in the 135-case vitest suite; API returns structured `error.code` envelopes.
- `readyz` on production: database/queue/llm_mode=github_app/webhook_secret all healthy (checked 2026-10-24).

## Leg 5 — GitHub App permissions reconciliation

- Deploy doc previously claimed **Contents read-only** — wrong. Actual requirement:
  Contents **read+write** and Pull requests **read+write** at app level.
- Tokens are least-privilege per call: read tokens `{contents:read, metadata:read}`;
  write tokens `{contents:write, pull_requests:write, metadata:read}` pinned to a
  single repository; 1h GitHub TTL, ~50min in-memory cache, never persisted.
- Fix delivered via PR/manual delivery modes only; `auto` exists in the enum but
  is not enabled for pilot (pilot mode = manual/PR). Nothing merges or deploys.
- Doc fix committed to `docs/deploy/RAILWAY_AND_GITHUB_APP.md` (PR #527).

## Leg 6 — PR #441 (redaction parse fix) — reviewed, **merged**

Fixed a real defect: `redact()` broke Python syntax on 36 files, silently
dropping them from indexing. Merged to `main` (3d023d55). Residual gap
disclosed: unquoted values followed by more code on the same line are no
longer redacted; token-format patterns still catch real keys.

## Leg 7.5 — Live-provider + real-sandbox leg (executed 2026-10-24)

`backend/tests/test_pilot_rehearsal_live.py` (opt-in: `REHEARSAL_LIVE=1` +
`ANTHROPIC_API_KEY` + `VERCEL_TOKEN`). Same two-org real stack (Postgres,
API, worker), but `orchestrator_factory` live, `security_sandbox=vercel`
(prod driver), ledgerlite bound via `repository_bindings_file`.

**Result: PASS.** Assessment `asm_01M5QZY2E3Y0D2D44Q0PG6QBWX` on ledgerlite
`53851a4f` ran CREATED → SCOPE_CHECK → INDEXING → FUNCTIONAL_REVIEW → RECON →
… → **COMPLETED** in ~37s. Usage: **13 LLM calls, 30 tool calls,
$0.012649, 13.2s real Vercel Sandbox time**. Every `agent_run` reports
`provider_mode=live` (asserted — nothing scripted claimed).

Scout found the planted defect: `fnd_01M5QZYM2RB0QE96G3ZQQJ8J5G`
"GET /accounts/{account_id} may not enforce account ownership", CWE-639,
confidence 0.9, status **inconclusive** — honestly flagged, not presented
as sandbox-validated. Cross-tenant: eve → 404 on the finding and on every
transcript artifact of the live run. Known caveat: live runs need
`VERCEL_TOKEN` + `VERCEL_PROJECT_ID` + `VERCEL_TEAM_ID` env for the sandbox
driver (prod supplies these; the test documents the requirement).

## Leg 7 — Production journey (LIVE, executed 2026-10-25)

**Environment:** Railway API `ari-ai-production-76d4.up.railway.app`
(`readyz`: database ✓ queue ✓ 12 workers, llm `live` `claude-haiku-5-5`,
sandbox `vercel`, artifact store writable, GitHub App + webhook secret
configured, `git_sha` `0f311913`). Target: private
`adamissac/pilot-rehearsal-target` (ledgerlite, planted CWE-639), Ari GitHub
App installed with Contents RW + Pull requests RW + Metadata read.

**Identities (owner-approved plan B):** the owner's browser session could not
be used (Desktop tab not interactive), so two rehearsal users/orgs were minted
server-side in the production DB using the same session-row mechanism OAuth
uses — `rehearsal-alice` (org A, added as owner of the project's org) and
`rehearsal-eve` (independent org B). No owner cookie or credential was
requested or used. Requests sent `X-CSRF-Token` from `GET /api/v1/me`.

### Journey

| Step | Evidence | Result |
|---|---|---|
| App install + connect | repo `rep_01M5R7RNZCJP4NVF6MRC0RC46M`, project `prj_01M55VPRZP4SY423ETVF3K3TBW` | PASS |
| Build-recipe PR | Ari opened [pilot-rehearsal-target#1](https://github.com/adamissac/pilot-rehearsal-target/pull/1) (`build_recipe.json`, drafted with Claude, flagged the planted route honestly). Merged 00:40:38Z under the App identity (`ari-security[bot]`) by a development-agent session holding the App key — **not** product code (no merge API call exists in `backend/` or `security-agents/`). Which session is not provable from GitHub; installation-token issuance is the audit trail | PASS (operator action recorded) |
| Scope | `scp_01M5R7S1M769KA3ZESGXRZSWVK` active, `sandbox_build`, 3 check categories, owner-approved | PASS |
| Index @ `53851a4` (first try) | `snp_01M5RATJZT1HG804SVAFM6BJRY` FAILED `SUBSYSTEM_UNAVAILABLE: code_intelligence.api:build_service failed to initialize … Set INTELLIGENCE_REPOSITORIES_FILE …` during the outage window (D3) | FAIL → recovered |
| Assessment @ `53851a4` (pre-recipe commit) | `asm_01M5RCNXY6CTM8S8S3MDQ6ZFBD` COMPLETED, 4 live runs, **$0.014881**, 3.3 s sandbox. Planted IDOR reported `inconclusive`: Probe stated the recipe's route ids/identities were unavailable and "I did not guess parameter values" | PASS (honest inconclusive) |
| Assessment @ `7cf48de` (recipe merged) | `asm_01M5RCVC20ZMYH8EKTZQP40W73` COMPLETED; **9 runs, all `provider_mode=live`** (scout, reviewer, probe×2, trace, diagnose, patch, verify, fortify); **$0.029415**, 30 LLM calls, 59 tool calls, **29.2 s real Vercel sandbox** | PASS |
| Finding lifecycle | `fnd_01M5RCVZQ51J9XM65P4VM6BVNZ`: hypothesis → **validated** ("probe confirmed via access_control.ownership") → diagnosed → patch_proposed → **verified** ("independent Verify accepted") → hardened. Four other findings stay `hypothesis`/`inconclusive` and are labelled so | PASS |
| Repair | `pat_01M5RCXK2DXWZZCZ7NEC91KVFE`: one-condition ownership check in `ledgerlite/app.py` + new `tests/test_get_account_ownership.py` | PASS |
| Independent Verify | `ver_01M5RCY0DX9CTXZTFZCM04MXDZ` verdict **accepted**: `diff_scope_ok`, `no_test_tampering`, `original_check_now_fails_to_reproduce`, `functional_suite_passes`, `generated_tests_pass` — each with a sandbox/tool artifact | PASS |
| Approval | `apv_01M5RCX247KZGRRK43F6G8TZQY` "auto delivery: pre-approved by the project owner" — this owner project runs `delivery_mode=auto`; pilot orgs must not (C2) | PASS (as configured) |
| Fix-PR delivery | `job_01M5RCY0HPR2EE47YVK8VSMXKK` `deliver_verified_patch` **FAILED** `CONFLICT_STATE: the stored diff was redacted; Verify tested different bytes` — see **D1**. No PR opened; `main` still `7cf48de`; nothing merged or deployed | **FAIL (fail-closed)** |
| Result retrieval | `/assessments/{id}/report`, `/projects/{id}/findings`, `/findings/{id}/patches`, `/artifacts/{id}` all 200 for org A | PASS |

### Tenant isolation on production (org B → org A)

`rehearsal-eve` replayed 34 reads and mutations using org A's **real** IDs
(project, repo, scope, snapshot, assessments, runs, events, report, findings,
patches, artifacts, jobs, approvals) plus fabricated IDs, including
`POST …/assessments`, `…/cancel`, `/scopes/{id}/revoke`: **34/34 → 404
`NOT_FOUND`** (no existence leak). `GET /audit?project_id=<A>` as Eve → 404.
Org A's scope remained `active` after Eve's revoke attempt.

### RLS on production (non-superuser probe; app role is superuser, see D8)

Migration head `0017`. `index_payloads` and `index_snapshots` have `FORCE ROW
LEVEL SECURITY` + policy `tenant_isolation` on `ari.project_id`. A throwaway
non-superuser role saw 0 rows without tenant context, only the scoped
project's rows with it, 0 rows for a fake project, and writes were denied.
Probe role revoked and dropped afterwards.

### Operations drills on production

| Drill | Evidence | Result |
|---|---|---|
| Cancel running assessment | `asm_01M5RD308T0RER91FNMQ0JGP0Q` cancelled in RECON → `CANCELLED`, $0.00, no runs; second cancel → 409 `CONFLICT_STATE` | PASS |
| Budget enforcement | `asm_01M5RD406NQC4GRRT48N64ANNY` with `max_usd=0.002` → `BUDGET_EXHAUSTED` "USD budget 0.002 exceeded" at $0.002388 (overshoot = one in-flight call) | PASS |
| Scope revoke | `scp_01M5RD3YN9JH9V652ATKS93GKV` created → approved → revoked; new assessment on it → 403 `SCOPE_DENIED` | PASS |
| Monitoring opt-out / opt-in | `monitoring_policy.enabled` false 02:10:07Z → true 02:13:27Z, settings restored byte-identical; 0 monitoring assessments in the window, but no push to a connected repo arrived in it, so the suppression path itself is evidenced by the local leg only | PARTIAL |
| Automatic limits | 499 monitoring assessments / 24 h on `adamissac/ari-ai` (the team's own repo), every one a distinct SHA; per-hour count plateaus at exactly 60 → hourly cap enforced, but at a value above the code default (5/h, 20/day) — **inferred** env override (D4) | PASS (enforced) / config finding |
| Cost | Rehearsal total **$0.0443** (2 assessments) + $0.0024 budget drill. Org-wide monitoring spend last 24 h: **$17.16** (499 runs, max $0.40/run, cap $3/run) | Recorded |

### Production findings

- **D1 — Verified fix cannot be delivered when the diff contains credential-shaped text (pilot-blocking for fix delivery).**
  Patch's generated test inlined the fixture bearer tokens; the stored diff
  (`art_01M5RCXK2V4Q6WXAHDCB87CG9Y`) is redacted to `[REDACTED:bearer]`;
  Verify records `redacted: true` with differing hashes (`host_adapter.py`),
  and `delivery._unverified_bytes` refuses. The refusal is correct (never
  deliver bytes Verify did not test, never persist secrets), but the journey
  ends without a PR. Fix direction is a trust-boundary decision — handed to
  the delivery/redaction lane for authorized review.
- **D2 — Outage 01:26–~02:00Z.** Postgres restarted cleanly at 01:26:51Z and
  was ready in 3 s; the API/worker returned 502/503 until redeployed. The
  frontend showed the accurate "The API is restarting" error throughout.
  Root cause of the API not reconnecting is **not established** (Agent-service
  logs not captured). Separately, 14 assessments between 2026-10-23 11:12Z
  and 2026-10-24 18:14Z failed `CONFLICT_STATE: paused orchestrator state was
  lost (worker restarted…)` — honest for runs without a recoverable
  checkpoint. `HostOrchestrator` can restore an `AWAITING_APPROVAL`
  checkpoint on a fresh worker; interrupted active runs without a
  checkpoint still end as `CONFLICT_STATE` and need a new start.
- **D3 — Code-intelligence configuration undocumented for Railway.** The
  failed snapshot shows `build_service` running without
  `INTELLIGENCE_REPOSITORIES_FILE` + a persistence option. After recovery,
  indexing worked, so the running deployment evidently has a valid
  configuration, but `deploy/railway/start.sh` and
  `docs/deploy/RAILWAY_AND_GITHUB_APP.md` do not set or list these variables.
  Not reproduced; recorded so the next redeploy is checked.
- **D4 — Production automatic-assessment limits above pilot values.** Another
  agent reported `AUTOMATIC_ASSESSMENTS_PER_HOUR=60` and `_PER_DAY=500`, which
  matches the observed plateau at 60/h. Manual starts are limited per user
  in-process only (5/h, reset on restart). Under #607, automatic starts count
  all project triggers toward 10/hour and 50/day, while manual starts count
  only manual-trigger assessments toward those same limits. Manual starts
  therefore count against both limits, and combined starts can reach up to
  20/hour and 100/day. The $50 rolling-day spend cap applies to all starts;
  see C10 in `LAUNCH_DECISION.md` for the owner's deviation decision.
- **D5 — Sandbox plan quota.** 19 earlier assessments failed
  `SandboxApiError: HTTP 402: Hobby plan usage limit exceeded` (snapshot
  storage / usage). Pilot capacity needs a paid sandbox plan or a hard cap.
- **D7 — `delivery_mode=auto` on the rehearsal project.** In `auto` mode,
  Verify-accepted fixes are committed to the default branch, which is outside
  pilot scope. The C9 re-run must use `pull_request`.
- **D8 — App DB role is a superuser.** `pg_roles` contains only `postgres`
  (`rolsuper`, `rolbypassrls`), and all 16 app connections use it. RLS on
  `index_payloads`/`index_snapshots` is therefore not a production control
  until the app moves to a least-privilege role (DB_ROLE_MIGRATION.md).
- **D6 — Credential hygiene.** The production Postgres public URL was pasted
  into chat during setup; the admin password has since been rotated, and
  public networking remains open for removal last. The rehearsal
  users/orgs/sessions (and Alice's membership in the owner org) were written
  directly to the production DB; cleanup was approved and executed. See
  `DB_ROLE_MIGRATION.md` §Rehearsal cleanup.

Evidence files (operator machine, not committed — contain tenant IDs):
`prod_report.json`, `prod_asm2_report.json`, `prod_findings.json`,
`prod_patch_diff.txt`, `prod_patch_verification.json`, `prod_eve_probes.txt`,
`prod_drill_cancel_revoke.txt`, `prod_drill_monitoring_optout.txt`,
`prod_cost_ops.txt`.

## Leg 8 — hosted C9 re-run on production (2026-10-25)

At the time of the re-run, production ran `497372ff` (#587), which delivered
from a delivery-only sealed artifact. Rehearsal project
`prj_01M55VPRZP4SY423ETVF3K3TBW` was changed from `delivery_mode=auto` to
`pull_request` through `PATCH /api/v1/projects/{id}`; the audit recorded
`delivery.pull_request` and `project.updated`.

### Run 1 — sealed delivery and PR #2

Manual assessment `asm_01M5RMCW5QNM53Y9R53NT5PYRM` started at 04:16:44Z and
completed through every state. The planted IDOR finding
`fnd_01M5RMDNR0FGEJD0VFCANBRSG2` was patched as
`pat_01M5RMFBQWDZQKAAQV74ZY2XXF`; Verify
`ver_01M5RMFQ0XMKSW25P2H8HET1ER` was accepted.

Sealed artifact `art_01M5RMFBRYH69CRQVAGXGSRDD9` was 2,036 bytes with SHA-256
`0a86ea56…9756`, identical to the member-visible diff. No redaction was needed,
so this run did not re-exercise D1's redaction path. Audit recorded
`delivery.pr_opened` at 04:18:20Z by `auto-delivery`, opening
[pilot-rehearsal-target#2](https://github.com/adamissac/pilot-rehearsal-target/pull/2)
on branch `ari/fix-pat_01M5RMFBQWDZQKAAQV74ZY2XXF`, head `faada009`. The PR
changed two files, +40/−4: `ledgerlite/app.py` and new
`tests/test_account_ownership.py`; those changes match the verified patch.
A byte-for-byte comparison of GitHub's diff with the stored artifact was not
possible because the formats differ. Exactness rests on delivery's sealed-SHA
check passing before push.

At the end of Leg 8, no owner-witnessed open PR remained. PR #2 was open and
unmerged from 04:18:20Z to 05:03:31Z, then approved and
squash-merged as `d6acccf` by another agent session (`devin-defense-lab`)
using Adam's GitHub account under delegation. Its review was not a personal
review by Adam. Ari's backend has no merge call; the merge was an
operator/agent action.

### Run 2 — protected-tests proof and PR #4

Assessment `asm_01M5RSKEZDZWYBKRASGE3QJA0S` ran at 05:47:42Z on
`rehearsal/protected-tests-proof`, using production `95e90861` (including
#599). Verify `ver_01M5RSNZHCXH5XAMQMRMG9MST0` was accepted with
`no_test_tampering` = `"protected: 1 file(s)"`. Delivery opened
[pilot-rehearsal-target#4](https://github.com/adamissac/pilot-rehearsal-target/pull/4)
at 05:49:08Z; an agent closed it unmerged at 05:50:42Z as proof-only.

### Quotas and findings

For Run 1, the daily limit was temporarily raised from 50 to 500 with owner
approval and restored to 50 around 05:05Z. The hourly limit was found at 60
after an unknown-lane change and restored to 10. Another lane temporarily
raised the daily limit to 600 for Run 2 and restored it. Final verified
Railway values were `PROJECT_ASSESSMENTS_PER_HOUR=10`,
`PROJECT_ASSESSMENTS_PER_DAY=50`, and `ASSESSMENTS_USD_PER_DAY=50`;
`/readyz` was ready with 24 workers and 0 queued.

- **D9:** 429 `resets_at` was wrong when the count greatly exceeded the limit.
  #596 fixed the hour/day cases; the spend cap remained affected and needed a
  follow-up fix.
- **D10:** Agent sessions merged or closed Ari fix PRs on the rehearsal target
  using the owner's account. At the end of Leg 8 no owner-witnessed open PR
  remained, and branch protection is unavailable on the free-plan private
  target repository.

## Leg 9 — final hosted C9 repair (as `ari_app`)

The `ari-ai` service had been cut over to the least-privilege `ari_app` role.
The rehearsal project's `delivery_mode` had been reset to `auto` by a settings
save at 08:28:57Z (`project.updated`, fields `name` and `settings`). It was
restored to `pull_request` through the settings API at 09:20:52Z
(`delivery.pull_request`), before delivery.

`PROJECT_ASSESSMENTS_PER_DAY` was temporarily 500 for the run and then restored
to 50 by successful deploy `fa0dad39`. Final verified values were 10/hour,
50/day, and $50/day.

Finding `fnd_01M5S5R3J354Z1P07A23C251GT` was repaired by patch
`pat_01M5S5SMZ0XHF5Y82DMYNBR855`; verification
`ver_01M5S5T4M34N3ZPD6KKZ527WD0` had verdict `accepted`.

Diff artifact `art_01M5S5SMZAQ56GW2X5ZWTT02KH` and sealed artifact
`art_01M5S5SMZEPXHAJ9VSADCE8F8S` were both 1,864 bytes with SHA-256
`978898903e0488bf041fc0571668f5f1179f30378a2c609141400e9f94a2061c`.
The API served the diff artifact (200) and refused the sealed artifact (404).

At 09:21:08Z Ari's `auto-delivery` actor recorded `delivery.pr_opened` and
opened [pilot-rehearsal-target#5](https://github.com/adamissac/pilot-rehearsal-target/pull/5)
on branch `ari/fix-pat_01M5S5SMZ0XHF5Y82DMYNBR855`, commit
`a4c567109ec7f500a803491110d8bebf791a6399`, authored by `ari-security[bot]`.
Applying the verified 1,864-byte diff to base `010cc2dd` produced tree
`a63138fa021a59b0ab8f06ea2d969b32fe802490`, identical to PR #5's commit tree.
PR #5 is open and unmerged. GitHub reports a merge conflict, expected because
PR #2 already merged the same fix into `main`.

The no-touch directive was posted to #559 (comment `6395894348`). C9's
production delivery evidence condition is met; C9 remains open for Adam's
personal review of PR #5.

## Open findings for the launch decision

1. **No automated retention; deletion is operator-invoked** — `purge_project`
   performs the multi-table teardown (no FK cascades) + disk objects; artifact
   store needs refcounting before per-tenant unlink. Drill verified the data is
   gone afterward (uniform 404). No self-serve delete or TTL.
2. **4 executable Probe check templates** — `access_control.ownership`, `access_control.authentication`, `access_control.role` (PR #534, merged), `input_validation.bounds`; other CWEs are flagged-not-validated and must stay reflected in customer-facing claims (done in FEATURE_MATRIX).
3. **Isolation = membership checks + RLS on source-content tables** — PR #535
   adds FORCE RLS on `index_payloads`/`index_snapshots` via a transaction-local
   `ari.project_id` GUC (verified firsthand: 17/17 adversarial tests pass on the
   branch, including `test_rls_blocks_reads_without_tenant_context`). Membership
   checks remain the primary boundary elsewhere and are covered by the
   ID-substitution drill.
4. **Assistant-role turns are re-sent unredacted** to the provider (thinking-block
   signature constraint) — disclosed; mitigated by inbound redaction.
5. **Production fix-PR delivery** — Leg 7's delivery attempt failed closed
   (D1); Legs 8 and 9 proved the PR-delivery mechanism. PR #5 is open and
   unmerged with an exact Verify-tested tree; C9 remains open for Adam's
   personal review.
6. **PR #535 (RLS + `purge_project`)** — merged and verified on production
   (migration head 0017, non-superuser RLS drill passed).
7. **`jobs.last_error` redaction** — #530 merged; `jobs.py` stores
   `last_error=redact(...)` on `main` @ `0f311913`, which production runs.
8. **D9 — quota reset timestamps (resolved)** — #596 fixed hour/day reset
   times; #605 fixed spend-cap reset times and is deployed in `a85885aa` (C14).
9. **D10 — owner-witnessed PR handling** — agents merged/closed earlier PRs
   using the owner's account. Leg 9 left PR #5 open and unmerged under the
   no-touch directive; branch protection is unavailable on the free-plan
   private target.
