# Session handoff log

Append-only. Newest entry at the top. Every development session (human or agent)
adds an entry before ending.

## Template

```
### YYYY-MM-DD — <who / agent> — <task IDs>
- Did:
- State of the code (what exists now, verified how):
- Commands run and results:
- Decisions made (ADR links):
- Assumptions / open questions (RQ links):
- Next steps:
- Warnings for the next session:
```

---

### 2026-10-24 — Devin (agent, Adam) — leaked Vercel snapshot cleanup, push→agents verification
- Did: verified the named-sandbox snapshot-leak fix already on main (`persistent: False` on run boxes + `destroy` in teardown + `_sweep_stale`); opened PR #495 making the sweep also delete stale run boxes' orphan snapshots so leaked quota is actually freed. Confirmed `delete_orphan_snapshots` only removes snapshots created *by* the destroyed sandbox — the shared `ari-base-*` snapshot can never be an orphan of a run box. Verified on main: security-agents suite 581 passed / 24 skipped, frontend typecheck + 330 vitest + lint clean, standards smoke (CWE/OWASP/CVSS/ATT&CK/D3FEND/SARIF/SP 800-115) passes. Confirmed agents are standards-grounded in code: Scout CWE/OWASP, Probe CWE, Trace ATT&CK/CVSS, Diagnose D3FEND, Fortify CWE/D3FEND; Patch/Verify get standards context via the tagged finding + remediation plan, not prompts.
- State of the code: push→agents path gates are code-verified (`pipeline.process_github_delivery` → `auto_commits.schedule` → `assess_github_commit`): `repositories.automatic_assessments` (per-repo opt-in, UI switch exists), `GITHUB_AUTO_COMMIT_AUTHORS` (Railway entrypoint defaults `*`), `monitoring_policy.enabled` (project setting, UI switch), active `AuthorizedScope`. All four must be on for pushes to spawn runs — they are product gates, not bugs.
- Commands run and results (executed): `pytest security-agents/tests -q` (581 passed), `pytest tests/test_vercel_sandbox.py` (5 passed incl. new sweep test), `npm run typecheck/lint/test --prefix frontend` (clean, 330 passed), `python -m security_agents.standards.smoke` (validated).
- Warnings for the next session: prod-side flags still need flipping per repo (Automatic runs ON, monitoring enabled, scope approved) — DB/config, not code. Already-orphaned snapshots (sandbox deleted without orphan cleanup) can't be reached by the sweep; they expire on their TTL or need `sandbox snapshots delete` with Vercel creds.

### 2026-10-24 — Cursor (agent, Alan) — rebase #439 onto main (docs conflicts)
- Did: left PR #439 **open** (no merge, no Phase 7B, no RL training, no customer enforcement). GitHub had blocked required checks with **Checks awaiting conflict resolution** on only `CHANGELOG.md` and `docs/planning/SESSION_HANDOFF.md`. Fetched `origin/main` `c5c2b47d` (PR #453) and rebased `cursor/live-defense-first-responder-0044`. Resolved those two files by keeping **both** sides: main’s Unreleased fixture-only research bullet and Adam 2026-10-23 build-recipe handoff, plus this PR’s Phase 7A 0.21.0 Unreleased bullet and Alan 2026-10-24 Codex/Phase 7A handoff entries. Did **not** drop the four Codex security fixes (verified evidence, genuine provider acks, telemetry-window mapping, OFF-mode race). Schema still **0.21.0**; Alembic still `0015`. Main remains 0.19.0 / `0014`. Open #412 still **0.20.0** with no Alembic — no collision, no bump.
- Commands run and results (executed):
  - `git fetch origin --prune`; rebase onto `origin/main` `c5c2b47d`
  - `uv run ruff check` / `ruff format --check` — pass (195 files formatted)
  - `uv run python -m shared.export --check` — 154 files, schema 0.21.0
  - `uv run pytest -q backend/tests/test_live_defense_phase7a.py backend/tests/test_jobs_queue.py backend/tests/test_rl_no_action_path.py` — 33 passed
- Decisions: do not merge #439. Owner/security approval remains separate.
- Next: push the rebased branch; check whether GitHub Actions automatically starts (especially `backend / test`). If Actions still never queues, report that separately — do not fake a pass.
- Warnings: **leave the PR open**. No Phase 7B. Live seven-agent Claude loop still UNVERIFIED.

### 2026-10-24 — Cursor (agent, Alan) — Phase 7A Codex review fixes on #439
- Did: left PR #439 **open** (no merge, no Phase 7B, no RL training, no customer enforcement). Fixed Backend/test CI (`test_rl_no_action_path`: removed `/first-responder/auto-execute`; absence is the control, 404). Fixed four Codex findings with regressions: (1) `evidence_state=verified` only with independently executed `verification_results`; (2) no fabricated handoff receipts — UI does not invent acks; synthetic provider ack tests are labeled; (3) deploy mapping resolved against telemetry window, else UNKNOWN; (4) OFF serialized with dispatch (`pg_advisory_xact_lock`), claim skips `cancel_requested`, handlers `check_cancelled` and abort when OFF is effective. Schema **0.21.0** (0.20.0 reserved for open #412); Alembic stays `0015`. Orchestrator `7a.1.1`.
- Commands run and results (executed):
  - `uv run pytest -q backend/tests/test_live_defense_phase7a.py backend/tests/test_jobs_queue.py backend/tests/test_rl_no_action_path.py` — 33 passed
  - `make check` inputs: ruff pass; `shared.export --check` 154 files; fixtures regenerated for 0.21.0 / 7a.1.1
- Decisions: do not merge #439. Schema plan vs #412: this branch 0.21.0 + 0015; #412 keeps 0.20.0 and no 0015. If #439 lands first, #412 rebases to 0.22.0.
- Next: re-run required GitHub checks on the final head; resolve the four Codex threads only after those tests; owner/security approval still required.
- Warnings: **leave the PR open**. No Phase 7B. Live seven-agent Claude loop still UNVERIFIED.

### 2026-10-24 — Cursor (agent, Alan) — Live Defense Phase 7A first-responder
- Did: branch `cursor/live-defense-first-responder-0044` from live `origin/main` (fast-forwarded through `211c2b24` / PR #435 to `a97820f1` / PR #438). Confirmed PRs #411 MERGED, #412 OPEN, #422/#430/#433/#435 MERGED, #438 MERGED (Adam status only). Schema 0.20.0, Alembic `0015`. Added fail-closed modes OFF/MONITOR|SHADOW/ASSISTED RESPONSE (no AUTO-EXECUTE), deterministic orchestrator `7a.1.0`, capability registry from real seven AgentSpecs, owner-attested deploy mapping, analyst handoff with provider-ack delivery, First responder UI tab. Did **not** train RL, edit Codex/v6/holdout, create AUTO-EXECUTE, expand production enforcement, or start Phase 7B.
- Commands run and results (executed):
  - `make check` — ruff pass; format 193 files; `shared.export --check` 154 files; examples check pass
  - `uv run mypy` — Success: no issues found in 40 source files
  - `uv run pytest -q backend/tests/test_live_defense_phase7a.py backend/tests/test_parallel_migrations.py backend/tests/test_live_defense_authorize.py backend/tests/test_live_defense_phase5.py backend/tests/test_live_defense_phase6.py` — 43 passed
  - `uv run pytest -q shared/tests/test_contracts.py backend/tests/test_live_defense_phase7a.py` — 95 passed, 1 skipped (TSC unset)
  - frontend lint + typecheck pass; vitest 330 passed / 78 files; transport 35 passed
  - Playwright `tests/e2e/live-defense-phase7a.spec.ts` via `stack.py` + Chrome — 1 passed (2.5s); screenshots in store `media/live-defense-phase7a/`
- Decisions: **ENGINEERING GO FOR OWNED STAGING**. PR https://github.com/adamissac/ari-ai/pull/439 left open for human security review (mode / privilege / trust-boundary expansion). Eval: `docs/LIVE_DEFENSE_PHASE7A_FIRST_RESPONDER.md`.
- Warnings: **leave the PR open**. #412 also claimed 0.20.0; rebase that branch to 0.21.0 if both land. Do not start Phase 7B. Live seven-agent Claude loop is still UNVERIFIED.

### 2026-10-23 — Claude Code (Adam) — build-recipe PR for connected repositories
- Did: `backend/backend/recipes.py`. Connecting a repo with an `installation_id` (and `installation` / `installation_repositories` webhooks for repos already connected) queues `propose_build_recipe`: fetch the default-branch tip into the bare checkout, skip if `build_recipe.json` exists, index with code intelligence, draft the recipe (FastAPI app/factory, mount prefixes, single-param GET routes, test roots, exact pins from `uv.lock`/`poetry.lock`/`==` requirements minus the sandbox runner wheels), let the `recipe` agent (Haiku 5.5 in production) add identities, resources, routes and a `deploy/sandbox_target.py` harness, validate deterministically (`sandbox_setup`, `app_factory`, routes in the index, every identity owns every route's resource, fixture values literal in the harness, harness parses), push `ari/build-recipe` with `push_patch` and open a PR. Audit: `recipe.pr_opened|skipped|unsupported|invalid|failed`.
- State: tests in `backend/tests/test_build_recipes.py` (real git over a file:// remote, real Postgres, GitHub App and Claude faked). Dry run `uv run python -m backend.recipes <checkout>` on ari-ai with its recipe removed reproduces the hand-written `app_factory` choice (`backend.main:create_app` behind a harness), `/api/v1` prefixes and the same six pip pins; on payments-lite it reproduces `payments_lite.app:create_app` and `-q tests`.
- Not verified: a live Claude draft (no key locally) and a real GitHub PR; the first connect in production is the first live run.
- Warnings: code-intelligence redaction can turn valid Python into a syntax error (e.g. `backend/backend/main.py` line 137, `webhook_secret_configured=bool(` becomes `[REDACTED]`), so the index silently drops those files; the generator parses the raw blob locally for app/prefix discovery, but routes in such files are missing from the index and so never proposed.

### 2026-10-23 — Cursor (agent, Alan) — Phase 6 Codex P1/P2 before merge
- Did: on `feat/live-defense-phase6-pilot-audit` (PR #411), locked the application row in `gate_for` and disconnect so leftover execute cannot race a concurrent disconnect; counted `run_assessment` jobs via `assessment_id`; kept recovered `last_error` on the pilot trail as `job.recovered`. Tightening only; `AUTHORIZATION_CHECKS` still 15. No Phase 7.
- Commands: `uv run pytest -q backend/tests/test_live_defense_phase6.py backend/tests/test_live_defense_authorize.py` — 26 passed.
- Next: wait for CI on the new head of #411; merge only if MERGEABLE, required reviews satisfied, no enforcement expansion.

### 2026-10-23 — Cursor (agent, Alan) — Live Defense Phase 6 post-merge audit
- Did: branch `feat/live-defense-phase6-pilot-audit` from live `origin/main` `02ab34de`. Confirmed PR #393 merged `57e0326` and PR #395 merged `8bbc01e`. Schema 0.19.0, Alembic head `0014`. Proved shadow cannot execute with grant+channel present; denied leftover authorize/execute after disconnect; scoped queue depth; failed jobs on pilot trail. UI copy splits shadow vs Controls. No Phase 7, no v6/dataset/Codex edits, no production grants.
- State of the code: recommendation-only shadow path on main stays non-executing; owned-test execute remains a separate owner POST. Disconnect now fails the existing `application_registered` check.
- Commands run and results (executed):
  - `make check` — ruff pass; format 184 files; `shared.export --check` 140 files
  - `uv run mypy` — Success: no issues found in 40 source files
  - live-defense + authorize + contracts + migrations pytest — 149 passed, 1 skipped (TSC unset)
  - frontend lint + typecheck pass; vitest 330 passed / 78 files; transport 35 passed
- Decisions: **CONDITIONAL GO** for one company-owned staging app in recommendation-only shadow mode if the owner does not create grants/channels or use Controls. Eval: `docs/LIVE_DEFENSE_PHASE6_READINESS.md`.
- Next: hosted ingest of one company-owned staging app; do not start Phase 7.
- Warnings: Playwright e2e and gitleaks were not run in this environment. v6 inference is `skipped_unavailable` without `rl_defense`/torch here; hash-mismatch refuse-to-load was tested.

### 2026-10-23 — Cursor (agent, Alan) — Phase 4 overnight hardening (do not merge #393)
- Did: confirmed and fixed A–D on `feat/live-defense-phase4-controlled-enforcement`. Exact-source allowlist is only `198.51.100.66` in authz+adapter+fixture; idempotent apply 409s on binding mismatch; `increase_logging` restores prior log level; `x-ari-source` confined to owned-test probe IPs. Grant revoke TOCTOU re-gate, grant-row FOR UPDATE for budget, HMAC body+id regression. ADR-0020 and eval updated. **Do not merge PR #393** (executable controls; human security review).
- Codex/`rl-defense/` untouched (no training, promotion, v6, or research-file edits).
- Next: Phase 5 shadow-pilot from `origin/main`, not this branch.

### 2026-10-23 — Cursor (agent, Alan) — Live Defense Phase 4 controlled enforcement
- Did: branch `feat/live-defense-phase4-controlled-enforcement` from `origin/main` (`5be62de`, PR #392 merged); later merged `09e8cab` (constrained-RL Phase 1, Codex ADR-0019). Extends Phase 1–3. Shared 0.18.0 `LiveControl` lifecycle + 15-check `authorize.check` (ADR-0020). HMAC loopback adapter to owned-test fixture; verify uses real HTTP probes; app-local TTL; emergency stop. Controls UI with a **separate** authorize endpoint. Analyst accept still does not execute. `rl-defense/` untouched. No Phase 5 / production / autonomous.
- State of the code: owner grant + loopback channel → authorize (not review) → worker execute → verify with probes → dashboard labelled OWNED TEST ENVIRONMENT → rollback / TTL / kill switch. Recommendation cannot jump to EXECUTING.
- Commands run and results (executed):
  - `make check` — ruff pass; format 175 files; `shared.export --check` 137 files; examples check pass
  - `uv run mypy` — Success: no issues found in 40 source files
  - `uv run pytest -q backend/tests/test_parallel_migrations.py shared/tests/test_contracts.py backend/tests/test_live_defense_phase4.py backend/tests/test_live_defense_authorize.py` — 100 passed, 1 skipped (TSC unset)
  - `uv run pytest -q backend/tests/test_live_defense_phase3.py backend/tests/test_live_defense_phase4.py backend/tests/test_live_defense_authorize.py` — 44 passed
  - `npm run lint && typecheck` — pass; `npm test --prefix frontend` — 327 passed / 77 files; `npm test --prefix frontend/transport` — 35 passed
  - `npm run build --prefix frontend` — pass; routes include `/live-defense/controls`
  - `cd frontend && E2E_SKIP_BUILD=1 E2E_BROWSER_CHANNEL=chrome ../.venv/bin/python tests/e2e/stack.py tests/e2e/live-defense.spec.ts tests/e2e/live-defense-phase4.spec.ts` — 2 passed (14.4s)
- Decisions: independent gate is final authority (ADR-0020); do not overload NIST statuses; HTTP 200 ≠ effectiveness; do not train or promote. Numbered 0020 because Codex used 0019 on main.
- Eval: `docs/LIVE_DEFENSE_PHASE4_EVAL.md`. Ready-for-review PR. **Do not auto-merge** (executable-action trust boundary).
- Warnings: `/live-defense-pilot` remains sample layout. Do not begin autonomous enforcement or production grants.
### 2026-10-23 — Cursor (agent, Alan) — Phase 5 rebased onto main after PR #396
- Did: merged `origin/main` `7adf509` (owned payment-read context checks) into `feat/live-defense-phase5-pilot-readiness`. Kept both Unreleased changelog bullets. Did **not** edit Codex research files. PR #395 had become CONFLICTING while frontend e2e was still running. **Do not merge #393.**
- Next: wait for CI on the new head; merge Phase 5 only if green, no conflicts, no enforcement.

### 2026-10-23 — Cursor (agent, Alan) — Live Defense Phase 5 e2e green after main merge
- Did: merged `origin/main` `cb40ca6` into `feat/live-defense-phase5-pilot-readiness` (kept both Unreleased changelog bullets; did not edit Codex research files). Playwright 2 passed (9.6s) after waiting for rotated token text. `make check` 166 files; mypy 40 files; pytest 274 passed / 8 skipped; vitest 325/77; transport 35; gitleaks no leaks. PR #395. **Do not merge #393.**
- Next: merge Phase 5 only if GitHub Actions are green, no required-review gap, no conflicts, no executable enforcement. Else leave #395 open.

### 2026-10-23 — Cursor (agent, Alan) — Live Defense Phase 5 e2e locator + main merge
- Did: merged `origin/main` `cb40ca6` (PR #394 Codex RL-CMDP-002) into `feat/live-defense-phase5-pilot-readiness`. Kept both Unreleased changelog bullets. Did **not** edit Codex research files, train, or promote. Playwright Phase 5 disconnect locator now uses exact badge + `live-defense-app-disconnected` (strict-mode matched badge and note). PR #393 still open; Phase 4 enforcement is not on this branch.
- Next: re-run Playwright; merge Phase 5 only if CI green, no required-review gap, no conflicts, no executable enforcement. Do not merge #393.

### 2026-10-23 — Cursor (agent, Alan) — Live Defense Phase 5 shadow-pilot readiness
- Did: branch `feat/live-defense-phase5-pilot-readiness` from `origin/main` (`09e8cab`). Does **not** include Phase 4 enforcement (PR #393 stays open). Adds rotate/disconnect/shadow-verify/pilot-report. Health worker/telemetry/policy stay unknown. Codex/`rl-defense/` untouched.
- Next: run full check/test suite; merge Phase 5 only if CI green, no enforcement, no trust-boundary expansion, protection allows. Do not merge #393.

### 2026-10-22 — Cursor (agent, Alan) — Live Defense Phase 3 shadow runtime
- Did: branch `feat/live-defense-phase3-shadow-runtime` from `origin/main` (`6e98b23`, PR #391 merged). Extends Phase 1/2. Shared 0.17.0 `LivePolicyDecision` (NOT EXECUTED). Authenticated `owned_staging` ingest, deterministic rules, frozen-v6 only when serving assumptions hold (quiet window → abstain; no torch in this env → `skipped_unavailable`). Analyst review of shadow decisions does not execute. `rl-defense/` training/checkpoints/promotion untouched. No Phase 4 enforcement APIs.
- State of the code: owned staging app → authenticated ingest → rules (or validated v6) → persisted shadow decision → dashboard → review → no automatic traffic control. Authenticated runtime events no longer force `Incident.simulated`.
- Commands run and results (executed):
  - `make check` — ruff pass; format 165 files; `shared.export --check` 124 files; examples check pass
  - `uv run mypy` — Success: no issues found in 40 source files
  - `uv run pytest -q backend/tests monitoring/tests/test_detect.py shared/tests/test_contracts.py` — 239 passed, 8 skipped (`rl_defense` missing; Docker/live LLM opt-in; TSC unset)
  - `npm run lint && typecheck` — pass; `npm test --prefix frontend` — 325 passed / 77 files; `npm test --prefix frontend/transport` — 35 passed
  - `npm run build --prefix frontend` — pass; routes include `/live-defense`, `/live-defense/explain`, `/live-defense/apps`
  - `cd frontend && ../.venv/bin/python tests/e2e/stack.py tests/e2e/live-defense.spec.ts` — 1 passed (6.6s)
  - `gitleaks git --log-opts='origin/main..HEAD'` — 3 commits, no leaks
- Decisions: v6 quiet-window → explicit abstention (ADR-0017); HTTP 200 without principals is unverified noop, not IDOR; accept ≠ execute; do not train or promote.
- Eval: `docs/LIVE_DEFENSE_PHASE3_PILOT_EVAL.md`. Draft PR #392. Merge only if CI is green and the slice stays shadow-only.
- Warnings: `/live-defense-pilot` remains sample layout. Do not begin autonomous enforcement. Frozen v6 inference is `skipped_unavailable` without `rl_defense`+torch; CI `rl-integration` job is the path that can run it.

### 2026-10-22 — Cursor (agent, Alan) — Live Defense Phase 2 CI: Alembic head pin
- Did: GitHub `backend / test` failed on PR #391 because `test_repository_has_a_single_migration_head` still expected head `0009`. Phase 2 adds linear revision `0010` (`down_revision = 0009`). Updated the pin to `["0010"]`. Single-head rule unchanged. `rl-defense/` untouched. `origin/main` still `53bf03c` (no rebase needed).
- Commands run and results:
  - `uv run pytest -q backend/tests` — 170 passed, 7 skipped
  - `backend/tests/test_parallel_migrations.py` + live-defense backend tests — 11 passed
  - `make check` — pass; gitleaks on `origin/main..HEAD` — no leaks
  - `npm run lint && typecheck` — pass; vitest 323/77; live-defense unit 18 passed; `npm run build` — pass
- Next steps: push, wait for required CI green, mark #391 ready, merge if branch protection permits.

### 2026-10-22 — Cursor (agent, Alan) — Live Defense Phase 2 shadow pilot
- Did: branch `feat/live-defense-pilot-phase2` from `origin/main` (PR #390 already merged). Extended Phase 1; did not rebuild it. Shared 0.16.0: `ConnectedApplication`, `IngestTokenView`, `DefenseHealth`, `ComponentGraph`, `AnalystReview`. Backend migration 0010 + `backend/api/live_defense.py`. Frontend wires existing APIs end-to-end and adds Staging apps + analyst review. `rl-defense/` untouched. Draft PR #391.
- State of the code: analyst can register sandbox/owned_test apps, mint a token (shown once), inspect ingested evidence, read a stored recommendation, and record accept/reject/needs-investigation without executing a control. Health status is always `unknown`. No live policy-decision producer.
- Commands run and results (executed):
  - `make check` — ruff pass (157 files formatted); `shared.export --check` 123 files; examples check pass
  - `uv run pytest -q shared/tests/test_contracts.py backend/tests/test_live_defense_pilot.py backend/tests/test_rl_no_action_path.py backend/tests/test_runtime_ingest.py` — 64 passed, 2 skipped (TSC missing; rl_defense not installed)
  - `npm run lint --prefix frontend` — pass
  - `npm run typecheck --prefix frontend` — pass
  - `npm test --prefix frontend` — 323 passed / 77 files
  - `npm test --prefix frontend/transport` — 34 passed
  - `npm run build --prefix frontend` — pass; routes include `/live-defense`, `/live-defense/apps`, `/live-defense-pilot/apps`
  - `E2E_SKIP_BUILD=1 ../.venv/bin/python tests/e2e/stack.py tests/e2e/live-defense.spec.ts` — 1 passed (4.0s)
  - `gitleaks git --log-opts='origin/main..HEAD'` — no leaks found (4 commits). Full-history 5 hits are pre-existing `api.py` digest / `*_COMMIT` SHA false positives, not this PR.
- Decisions: do not fake a live inference feed; do not infer healthy from HTTP 200; accept ≠ enforcement approval.
- Next steps: customer shadow-pilot blockers listed on PR #391 (hosted ingest of a real staging app, live-inference producer if/when one exists, no autonomous enforcement).
- Warnings: `/live-defense-pilot` remains sample layout. Do not begin autonomous enforcement.

### 2026-10-22 — Cursor (agent, Alan) — Live Defense Phase 1 UI
- Did: branch `feat/live-defense-pilot-ui`. Customer dashboard at `/projects/{id}/live-defense` (Overview, Investigation, Explainability) plus isolated `/live-defense-pilot`. Live mode uses existing project/events/incidents/recommendation/explanation/evaluation APIs. Layout-preview fixtures in `frontend/lib/live-defense-preview.ts` are labelled SAMPLE LAYOUT and never mixed with records. No apply/isolate/rate-limit. `shared/`, `backend/`, `rl-defense/` untouched. Handoff for missing APIs: `docs/planning/LIVE_DEFENSE_FRONTEND_API.md`.
- State of the code: frontend-only; draft PR #390. Navigation: Live Defense under More; Monitoring links in.
- Commands run and results (executed):
  - `npm run lint --prefix frontend` — pass
  - `npm run typecheck --prefix frontend` — pass (after query-helper split fix)
  - `npm test --prefix frontend` — 323 passed / 77 files
  - `npm run build --prefix frontend` — pass; routes include `/live-defense` and `/live-defense-pilot`
  - Headed Chrome against `next start :3000` `/live-defense-pilot`: SAMPLE LAYOUT banner present; 0 apply/isolate/rate-limit buttons
- Decisions: missing component-health API → no live map (docs/14: healthy only from a recorded check). NIST `Incident` is not overloaded with DETECTED→EXECUTING.
- Next steps: backend/shared owner implements the handoff types; frontend then drops empty panels. Do not merge until review. Do not treat preview fixtures as live telemetry.
- Warnings: `/live-defense-pilot` is public (no session) on purpose for layout review; it must keep the sample banner.

### 2026-10-20 — Claude (agent, Alan) — live RL v8: low-intensity train mix — gate `recommend_promote`, hypothesis not supported
- Did: branch `feat/alan/live-retrain-v8`. One change from v7: `--train-dir train_v8` (train_v3 copied unchanged plus `rare_delegate_runs` and `trickle_outsider_quiet`, low-intensity and mostly quiet). It was chosen from train-band evidence: on `rare_delegate_runs`, v7 scores −3.61 against noop's −3.14 (`results/live-retrain-v8/pre_run_evidence.json`). `--reference-checkpoint` is now repeatable as `NAME=PATH` (v6 and v7). There is a fresh `holdout_v8` (two of its three scenarios are low-intensity). Pre-registered and pushed as `15e25f9`. `promotion.yaml`, val_v4 and the selection rule are unchanged.
- Result: the gate returns **`recommend_promote`**, 7/7 checks, +4.47 [3.14, 5.81] over deployed. **The hypothesis is not supported:** v8 trails v7 on both low-intensity holdout scenarios and overall (−0.46 [−0.96, −0.07]) and ties v6 (+0.08 [−0.40, 0.50]). It trails noop on the train scenario `rare_delegate_runs` (−4.01). It keeps the quiet-window fix: noop 178/178, block 102/102, lab/platform agreement 280/280 on train_v8 windows. holdout_v8 is easier than holdout_v7 for the per-scenario rule (v7's numbers clear deployed there too). Online learning moved val for the first time (−7.03 → −6.74 at 105k).
- Not done: promotion. It needs a human `usr_*` approver. v8 is the first gated candidate with the ADR-0017 behaviour; whether that is worth approving given the v7 comparison is that human's call (PROVENANCE.md, "What this means").
- Verified: `tests/test_live_v8.py` (4), `tests/test_v8_evidence.py` (8, including the platform agreement test that reproduces `platform_agreement.json`), the full rl suite, ruff.
- Next: give selection a view of the low-intensity regime (a quiet val scenario, or a per-scenario-regression-aware criterion), as one change with a fresh holdout. Do not tune on holdout_v7 or holdout_v8.

### 2026-10-20 — Claude (agent, Alan) — live RL v7: platform finding severities (ADR-0017 gap) — `reject`
- Did: branch `feat/alan/live-retrain-v7`. Opt-in `LoopConfig.finding_severity_context="platform"`: per-episode severities drawn from `platform_mapping.SEVERITY_SCORE`. Payments has its BOLA finding with p 0.5, gateway/api/auth have unrelated findings with p 0.25, db is always 0, and the draw is seeded by the episode only. `online.py --severity-context/--reference-checkpoint`, `rl_defense/live/platform_check.py` (lab vs platform recommendations on train windows), `evidence.py pack`. Pre-registered (`4b537d2`) with a fresh `holdout_v7`; `promotion.yaml` unchanged.
- Result: **`reject`**, 6/7 checks pass. On `quiet_week` it scores −3.62 vs deployed −3.15. Held-out −5.38 vs v6 −5.55: a tie (+0.17 [−0.14, 0.47]). Secondary: with real severities v7 recommends noop in 106/106 quiet train windows (v6 0/106) and block in 94/94 flagged; lab/platform agreement is 200/200 (v6 also 200/200: agreement measures the mapping). Nothing promoted; v6 stays promoted.
- Verified: `tests/test_live_v7.py` (7), `tests/test_v7_evidence.py` (6, incl. the agreement test reproducing `platform_agreement.json`), full rl suite, ruff.
- Next: a candidate that keeps v7's quiet-window behaviour and passes the gate needs a new holdout. Do not tune on holdout_v7.

### 2026-10-19 — Claude (agent, Alan) — runtime principal detector: the RL defense in the product path (contracts 0.14, ADR-0017)
- Did: branch `feat/alan/runtime-principal-detector`.
  - `RouteWindow.principals` (`PrincipalCounts`, pseudonymous `prn_…` refs) and `DefenseRecommendation.principal_targets`.
  - The backend stores the counters with the route windows (no migration; `latest_route_detections`, drills excluded). `rl_recommend` fills the live v3 `PlatformFacts` fields and the lab's node detection signals via `platform_mapping.RouteDetection` / `principal_facts`, and attaches block targets.
  - Lab gateway records gain `owner`. `rl_defense/live/report.py` is the reference reporter: aggregated, HMAC pseudonyms, grants counted as delegated. Its CLI generated `backend/tests/fixtures/principal_window_lab.json`.
- Verified:
  - `rl-defense/tests/test_principal_detection.py` (6): platform fields and node signals equal the lab loop's for the same window; no raw ids leave; grants count as delegated.
  - Backend: ingest validation and storage, drills excluded, and end to end the promoted v6 policy recommends `block_principal` targeting exactly the flagged pseudonym.
  - Suites: backend 159 passed (3 opt-in skips), rl 200 passed; `make check` clean.
- Known gap (ADR-0017): v6 was trained with fixed baseline finding severities. With real (0) severities on clean nodes it says `escalate_to_human` instead of `noop` in quiet windows. Train with finding-derived severities (follow-up).
- macOS note: if `shared`/`backend` import as empty namespace packages, run `chflags nohidden .venv/lib/python3.12/site-packages/*.pth`.

### 2026-10-19 — Claude (agent, Alan) — v6 evidence restored (original bytes, hash-pinned)
- Did: branch `feat/alan/v6-evidence-restore`. Adam's #362 review found the v6 checkpoint and experience bytes were not durably stored. They were still in the run's git-ignored `rl-defense/runs/live/retrain-v6/` and match the committed hashes, so they are restored unchanged. `results/live-retrain-v6/` now has `candidate_online.pt` (`8113a37f…`), its sidecar, and `candidate_online_experience.jsonl.gz` (268 MB → 6.9 MB; uncompressed `bc04e57b…`, as pinned by the checkpoint's lineage). It also has `EVIDENCE.json`, which pins the checkpoint, the full log, and the derived 108k-line selected prefix (`95ac4160…`). `promotion_report.json` gains only the `artifacts` block in the #362 format; the rest is byte-identical. New `rl_defense/live/evidence.py` (`verify` / `restore`, streaming, hash-checked); restored files are git-ignored.
- Verified: `rl-defense/tests/test_v6_evidence.py` (5). They prove the hashes agree across index, report and manifest, that a streaming verify of all bytes passes, that the checkpoint loads and serves, that tampering is refused, and that the registry's durable-evidence gate passes on a restored temporary copy. Nothing was promoted.
- Needs humans: approve committing the 6.9 MB gzip in the repo (proposal in the PR); Alan to decide on promotion.

### 2026-10-19 — Claude (agent, Alan) — live RL v6: online DQfD — first `recommend_promote`
- Did: branch `feat/alan/live-v6-dqfd`. Alan's direction: RL stays the defense; rules only teach. Added the DQfD large-margin loss (`DDQNConfig.demo_lambda`, default 0) and `rl_defense/live/online.py`. The teacher is chosen on train only. The network is fresh, with no sim prior. It gets 3k demonstration transitions and 5k pretraining updates, then 150k online live steps on `train_v3` with 25% demonstrations in every batch, selection on `val_v4`, and the v4 loop. Pre-registered in `rl-defense/results/live-retrain-v6/PREREGISTRATION.md` (`c788b5d`) with a fresh `holdout_v6`. Tests: `rl-defense/tests/test_live_v6.py` (8).
- Result: **`recommend_promote`**, all 7 checks pass. Candidate −5.30 vs rules −7.58; +9.51 [8.17, 10.79] vs deployed; +2.28 [1.80, 2.76] vs its teacher. Caveats are in PROVENANCE: fixed `always_block_principal_long` scores −3.89 on this holdout; val was flat from pretraining onwards; nothing was promoted.
- Next (needs humans): Alan to decide on promotion via `rl_defense.live.registry promote` (usr_* approver), and on the long-block action contract extension in `shared/`. That extension is required before `PolicyRunner` can serve live_v3 checkpoints.

### 2026-10-19 — Claude (agent, Alan) — contracts 0.13: serve live_v3 RL policies (long blocks)
- Did: branch `feat/alan/serve-live-v3`. Alan, as interim `shared/` approver, chose the first shape in the long-block proposal (decision `coordination/messages/20261019T110000Z-alan-to-all-long-block-actions-decision.md`). `DefenseActionName` gains `block_principal_long` and `block_top_principal_long`. `PlatformFacts` gains the v3 detector fields, all with defaults. `SCHEMA_VERSION` goes 0.12.0 → 0.13.0, which also covers #324 and #334. `PolicyRunner` serves live_v3 checkpoints and now uses the checkpoint's own greedy rule (noop margin included), so the served action equals the evaluated one. The live loop is the `PlatformFacts` producer (`platform_facts()`) and builds its obs through the serving mapping. The #335 items are done: `rl_defense/layouts.py` (inference imports no live lab), the `live/run.py` layout, the neutral caveat, docs, and CHANGELOG.
- Verified: `rl-defense/tests/test_v3_recommendations.py` (10); rl suite 179 passed; `make check` clean (contracts fresh); shared + backend rl tests pass, and `test_rl_integration.py` runs with rl_defense installed. The real v6 candidate (`8113a37f4ff9`, from `runs/`) was served once on a train scenario after a temporary merge with the v6 branch: it recommends `block_principal` then `block_principal_long` while principals are flagged.
- Not done / needs humans: promoting the v6 candidate (`rl_defense.live.registry promote`, `usr_*` approver). The backend has no principal detector, so a v3 policy served there never recommends a block until a producer fills the fields.

### 2026-10-19 — Claude (agent, Alan) — live RL v5: conservative Q-learning (CQL); learned-policy track closed
- Did: branch `feat/alan/live-v5-cql`. At Alan's request, one more pre-registered run (`rl-defense/results/live-retrain-v5/PREREGISTRATION.md`, committed in `687629d` before the run). It added a discrete CQL(H) penalty, `DDQNConfig.cql_alpha` (default 0, so v1-v4 are unchanged), set to `cql_alpha=1.0`, fixed beforehand. Everything else is v4. Fresh `configs/live/holdout_v5/` (seeds 44500/49200/49600). The report now includes each round's val curve. Tests: `rl-defense/tests/test_live_v5.py` (5).
- Result: **reject**. CQL fixed v4's degradation (snapshots selected at 6000/10000/12000 updates), but the candidate stays close to the behavior data. Blocks are only 3–5% of it, so the candidate rarely blocks: −12.17 vs rules −7.41, compromise 0.675, availability 0.991. Fixed `always_block_principal` scores −4.79. As pre-registered, the learned-policy track on payments-lite is closed. Never promoted.
- Next (needs humans): deploy the grant-aware detector plus short-block rule as the live defense, or keep RL only as a lab challenger on a harder environment.

### 2026-10-19 — Claude (agent, Alan) — live RL v4 (final iteration): gate-aligned reward, grant-aware detector, multi-val selection
- Did: branch `feat/alan/live-v4`. Fixed the three v3 reject causes as decided by Alan before the run: `c_disrupt=1.0` (opt-in `--c-disrupt`, `evaluate.V4_LOOP_OVERRIDES`; `LoopConfig` defaults unchanged so v1-v3 reproduce), a delegation-aware detector (`--delegation-aware-detector`: the instrumentation gets the business's grant table, `adversary.delegation_grants`; never reads `is_attack`), and selection on the unweighted mean over four val scenarios (`--val-selection scenario_mean`, `configs/live/val_v4/`). Fresh `configs/live/holdout_v4/` (payroll_rush, two_rogue_delegates, outsider_flood; seeds 43000–44029) was committed and pushed in `0220e7d` before any evaluation. `promotion.yaml` is unchanged; v3 hyperparameters were used. Tests: `rl-defense/tests/test_live_v4.py` (13).
- Result (`rl-defense/results/live-retrain-v4/`): **reject**. Fails availability 0.847 < 0.85 and no-attack duty 0.587 > 0.5; passes the other five checks (+7.89 [6.39, 9.34] vs deployed, beats tuned rules −7.85 vs −12.15, no per-scenario regression). The fixed `always_block_principal` baseline scores −7.31 with availability 0.882 under the same detector, and the best val snapshot is update 2000 in every round, so further offline updates degrade Q. Never promoted.
- Next (needs humans): no v5 is planned. Open questions: do the duty cap and floor fit principals who are both legitimate users and attackers, and is a learned policy worth it here versus short blocks with a grant-aware detector. The long-block contract proposal (`coordination/messages/20261019T053641Z-…`) is still open.

### 2026-10-19 — Claude (agent, Alan) — live RL v3: delegated reads, long/targeted blocks, v3 held-out verdict
- Did: branch `feat/alan/live-v3`. Scenarios gain legitimate delegated cross-tenant readers (bookkeeper fixtures `dave`, `erin`; `delegations` in `LiveScenario`). The detector flags them, so blanket blocking costs availability. Live action space v3 is a strict extension of v2: `block_principal_long` (23, block or extend every flagged principal for 10 windows) and `block_top_principal_long` (24, only the heaviest flagged principal), plus six detector features (obs 49..54), all live-only. The proposal (interim `shared/` approver, since Srihith has left) is in `coordination/messages/20261019T053641Z-alan-to-srihith-adam-long-block-actions.md`. Diagnosed v2's `raise_monitoring` waste: at the update-2000 snapshot, Q was still at sim scale (Q ≈ +1.5 vs returns ≈ −3.7), and about ten effect-free actions tie with noop within noise. The fix is a val-selected greedy `DDQNConfig.noop_margin`. The rules get every new control (`tune_rules_v3`, 384 grid points, train only). New `train_v3`/`val_v3`/`holdout_v3` (seeds 48000–49029) were committed before any evaluation. `promotion.yaml` and DefenseSimEnv are unchanged.
- Result (`rl-defense/results/live-retrain-v3/`): **reject**. Candidate −13.87 vs rules −10.98, vs deployed +2.01 [0.94, 3.06]. Fails availability (0.797), no-attack duty, beats-rules and per-scenario regression (heavy_reconciliation). On val the candidate beat the rules by about 3 using margin 0.5 (edge of the grid), which did not transfer. No containing policy reaches the 0.85 availability floor on holdout_v3.
- Commands: `.venv/bin/python -m pytest -q` (rl-defense) → 145 passed before the run; `ruff check` + `ruff format --check rl_defense tests` clean.
- Next (needs humans): decide whether the 0.85 floor fits scenarios with standing legitimate cross-tenant reads; the `shared/` approver to choose the shape of the new action names. A v4 needs per-principal targeting (or a richer selection signal than one val scenario) and a new unseen holdout; holdout_v3 is now exposed.

### 2026-10-19 — Claude (agent, Alan) — live `block_principal` control + v2 held-out verdict
- Did: branch `feat/alan/live-block-principal`. Added a principal-scoped containment control: the gateway refuses (403) requests from principals its instrumentation flagged for cross-tenant attempts, on the node they were flagged on, for 3 windows, whichever token they present. Implemented as a **live-only** action space v2 (`rl_defense/live/actions.py`: 22 sim actions + `block_principal` at index 22, obs + `principal_block_active`); `shared.rl.DefenseActionName` and DefenseSimEnv are unchanged. Proposal in `coordination/messages/20261019T043718Z-alan-to-srihith-adam-block-principal-action.md`. `PolicyRunner` refuses v2 checkpoints (lab-only). New unseen holdout `configs/live/holdout_v2/` (two identities, token rotation, outsider+insider), committed before the run; `promotion.yaml` unchanged; the rules baseline may also block.
- Result (`rl-defense/results/live-retrain-v2/REPORT.md`): **reject**. Candidate +7.30 [6.27, 8.39] vs deployed, availability 0.889 (floor now reachable; v1 0.759), but rules that may also block score −6.96 vs −7.29 (candidate wastes steps on `raise_monitoring`). Fixed-duration blocks leak against two alternating identities (64% compromise on `two_identities`).
- Commands: `.venv/bin/python -m pytest -q` (rl-defense) → 130 passed before evidence; `ruff check` + `ruff format --check` clean.
- Next: Srihith to ack/object to adding `block_principal` to `DefenseActionName`; consider block duration/identity-switch handling and an action cost tweak only via a new pre-registered holdout.

### 2026-10-19 — Claude (agent, Alan) — live retraining pipeline + pre-registered promotion gate
- Did: branch `feat/alan/live-rl-retrain`. Fixed two #315 bugs (stored obs came from an empty telemetry window; telemetry read the generator's `is_attack` label and counted our own blocking as anomalous — detection now uses gateway instrumentation only). Added bursty attack schedule, versioned live scenarios with disjoint train/val/holdout seed bands, train-only SHA-pinned experience datasets, offline Double-DQN candidate retraining (warm start, val selection, lineage manifest), paired held-out evaluation with live-tuned threshold rules, a deterministic promotion gate (`configs/live/promotion.yaml`, committed before the final run), and a registry requiring a human `usr_*` approver and a passing report bound to the checkpoint bytes, with rollback.
- Result (`rl-defense/results/live-retrain/REPORT.md`, 3 holdout scenarios × 30 paired episodes): candidate +5.38 return [4.54, 6.35] vs deployed sim-trained policy, compromise 100% → 28.3%, no per-scenario regression — but verdict **reject**: live-tuned rules score higher (−6.69 vs −7.98) and availability 0.76 < 0.85. The floor is unreachable with node-level isolation while attacks are active ~45% of windows (rules get 0.74).
- Honesty note: holdout was evaluated 3× during development and two design changes followed (disclosed in `results/live-retrain/PROVENANCE.md`); v2 needs new holdout scenarios.
- Commands: rl suite 114 passed; ruff check/format clean; pipeline run deterministic (round val returns reproduced exactly).
- Open for humans: add a finer-grained control (block a principal), revisit availability floor or `c_disrupt`. Nothing was promoted; no registry exists in the repo (runs/ is git-ignored).

### 2026-10-19 — Adam — connected commit real assessment
- Did: Added helper functions for default assessment scopes, repo-covering scope selection, and commit assessment filtering; connected the commit page to project scopes and persisted assessments, with connect/request-authorization/start flows. Kept the single Claude call as a clearly labelled narrated quick read and updated the provenance test and frontend documentation.
- State of the code (what exists now, verified how): `frontend/lib/real-assessment.ts`, `frontend/components/real-assessment-panel.tsx`, and unit tests are in place. Lint, typecheck, all frontend tests, and production build passed. The connected page was not visually checked, and no live backend assessment was started. No matching connect-commit e2e spec was found.
- Commands run and results: `npm test --prefix frontend/transport` — 33 passed; `npm run lint --prefix frontend` — passed; `npm run typecheck --prefix frontend` — passed; `npm test --prefix frontend` — 68 files, 275 tests passed; `npm run build --prefix frontend` — passed.
- Decisions made (ADR links): Reused the existing transport signature for `startAssessment` (idempotency key as its third argument); no transport contract changes.
- Assumptions / open questions (RQ links): The connected repository ID is used in the default scope name because `scopeBody` accepts a repo ID.
- Next steps: Review the branch, then commit and push through the authorized lead workflow; exercise the panel against a backend with an active scope and a different project owner available for approval.
- Warnings for the next session: Dependency installation reported audit advisories (one moderate in transport; six frontend vulnerabilities: one moderate and five high). A Vitest jsdom canvas warning appeared, but the suite passed. The pre-existing untracked `frontend/AGENTS.md` was left untouched.

### 2026-10-19 — Claude (agent, Alan) — Live RL Defense Lab audit + live-loop vertical slice
- Did: audited Phases 0-9 against spec for the RL pillar (`rl-defense/LIVE_RL_GAP_AUDIT.md`): env/DDQN/eval/inference are real but simulation-only; no telemetry source, no action execution, no live loop, no reward-from-real-outcome. Then built the highest-priority gap as a vertical slice: `rl_defense/live/` closes the loop against examples/payments-lite (real telemetry -> trained DQN -> enforced permitted action -> measured outcome -> reward -> LiveTransition). Branch `feat/alan/live-rl-loop` off main.
- State of the code: `rl_defense/live/{gateway,telemetry,adversary,loop,contracts,topology,run}.py` + README; tests `tests/test_live_gateway.py` (7) + `tests/test_live_loop.py` (4) + `tests/conftest.py`. Full RL suite 103 passed (was 92). Reproducible evidence in `results/live-slice/`.
- Commands run: `.venv/bin/python -m pytest -q` -> 103 passed; `ruff check` clean; `python -m rl_defense.live.run --steps 30000 --episodes 3 --horizon 30` -> transitions.jsonl (90 valid transitions).
- Key finding: mechanism works end-to-end; the sim-trained policy UNDER-CONTAINS on real telemetry (raises monitoring instead of isolating -> 180/180 attacks still compromise). Causal test proves isolate:payments drives compromise to 0 and raises reward, so the reward signal is correct; the policy just hasn't learned to use it on real signals. This sim-to-real gap is the honest motivation for live/adversarial training.
- Assumptions/open: route->node topology is hand-written (configs/topology/payments_lite.yaml); LiveTransition is a local realization of the PROPOSED shared experience schema (not in shared/ yet). venv set up at rl-defense/.venv; macOS needs PYTHONPATH=.:../shared:../standards:../examples/payments-lite/target for the CLI.
- Next steps: offline-retrain / live-train a policy on live transitions to close the sim-to-real gap; wire backend/UI. Do NOT merge without review; Phase 10 stays deferred (PR #313 untouched).
- Warnings: the loop enforces real controls against the demo app only, in-process, no egress.
### 2026-10-18 — Adam (Claude Code) — seven trained specialist models (ADR-0014)
- Did: branch `feat/adam/specialist-models`. New workspace package `specialists/` with one trained model per runtime agent (datasets, NumPy training, held-out evaluation vs deterministic baselines with bootstrap CIs, SHA-256-verified checkpoints, pure-Python inference). `security_agents/specialist_bridge.py` + orchestrator: each agent step gets its specialist's ranked output as an advisory `trained_model` data block; Scout hypotheses are re-ranked; Verify's evidence model can only add a review note; every call is a `specialist.inference` audit event. `backend/Dockerfile` copies `specialists/`.
- Results (held-out, see `specialists/README.md`): promoted on evidence — Scout (macro-F1 0.298 vs regex 0.090), Probe (acc 0.631 vs CWE lookup 0.606), Verify (macro-F1 0.976 vs heuristics 0.859). Not promoted, baseline used — Trace, Fortify (gains not significant on 21/32 test rows), Diagnose (tie), Patch (worse). payments-lite OOD: Scout ranks the planted IDOR 2/8, refund bug 5/8.
- Commands and results: `pytest specialists/tests` 31 passed; `security-agents/tests/test_specialist_integration.py` 5 passed; full suites in the PR description.
- Not done: Claude-only vs hybrid comparison (no live key/budget in session); real-world training data; deployment (Railway redeploy after merge).
- Warnings: the root test suite needs a virtualenv path without spaces on macOS (pgserver's psql call breaks on "Application Support"); `UV_PROJECT_ENVIRONMENT=/tmp/...` works.

### 2026-10-18 — Adam (Claude Code) — live commit analysis, landing explainer video
- Did: branch `feat/adam/live-commit-analysis-video`. (1) `/connect/[org]/[repo]/commit/[sha]` no longer shows the illustrative replay: `POST /api/analyze` reads the commit's real diff from GitHub and runs one Claude call (`claude-opus-5-5`, JSON-schema output, server-side refusal fallback) that writes the code review, findings, proposed fix and the seven agents' steps; the existing commit view replays it. Static only and labelled so (no execution, proposed tests not run, fix not applied). Access is checked against `/github/repositories` with the caller's session cookie. (2) Landing: `components/explainer-video.tsx` scroll chapter with the explainer video (`frontend/public/videos/ari-explainer.mp4`, 42 MB) and a download link.
- State: lint, typecheck clean; `npm test` 271 passed; `next build` ok. Browser: video chapter checked at 1440x900 (grows 0.62 → 1.0 with scroll, autoplays muted, 3m48s). `/api/analyze` guards checked with curl (401 without session, 400 on bad input).
- Not verified: an end-to-end live analysis. No `ANTHROPIC_API_KEY` locally or on Vercel, so the model call has not run; the JSON schema has not been accepted by the API yet.
- Next steps: set `ANTHROPIC_API_KEY` (and `GITHUB_TOKEN` for private repos) on the Vercel project, deploy, open a commit and check the first live run.
- Warnings: `/projects/[id]/simulate` still uses the labelled illustrative drill (unchanged). Continuous 24/7 defense is unchanged.

### 2026-10-19 — Claude (agent, Alan) — Phase 10 seven-agent → RL adversarial integration (design only)
- Did: wrote the eventual-integration design `docs/architecture/seven-agent-rl-adversarial-integration.md` (grounded in code: agents are a Scout→Probe→Trace→Diagnose→Patch→Fortify→Verify pipeline, not 7 attackers; RL is the fixed 5-node DefenseSimEnv with a PlatformFacts→obs bridge). Proposed reusable schemas (OffensiveScenario, EpisodeSpec, Transition/ExperienceDataset, PromotionReport) — NOT committed to shared/. Added ADR-0014 (decoupled, offline, instrumented-ground-truth-only, reproducible-split, gated) + index row. Cross-linked from architecture/README and docs/09.
- State of the code: no code changed; docs only. Schemas are marked PROPOSED and must go through contract-first (Srihith) before any build.
- Decisions: ADR-0014.
- Assumptions/open: projection of real findings onto the 5-node sim is lossy (RQ-005). Split assignment is by scenario-id hash (proposed).
- Next steps: do NOT implement until the standalone live RL pipeline (telemetry→inference→action→reward→training) is functional/tested; then ratify schemas into shared/ first.
- Warnings: nothing wires offensive output into training/orchestration; keep it that way per ADR-0014.

### 2026-10-17 — Adam (cloud session) — scroll performance, demo-only hosted build
- Did: branch `feat/adam/hosted-demo-scroll` (stacked on #226). Fixed landing scroll jank: the mean frame went from 82 ms to 21 ms (headless Chromium, software rendering). Added a `NEXT_PUBLIC_DEMO_ONLY` build flag (Vercel only): `/projects*` redirects to `/demo` and `/` renders without a backend. Fixed the blank white-button label in dark bands. Redeployed production from this branch: https://ari-ai-lake.vercel.app.
- Commands: `npm run lint`, `tsc`, `npm test` (202 passed); demo-only build with an unreachable backend: `/projects` returns 307 to `/demo`.
- Open: the user asked for pages that look "just like kairo.security". The site cannot be reached from the cloud sandbox (DNS/egress blocked), so an exact visual match needs screenshots from the user.

### 2026-10-17 — Adam (cloud session) — Vercel deployment of the frontend
- Did: created Vercel project `ari-ai` (team "Adam's projects"), linked to this repo, root `frontend/`, install `npm ci --prefix transport && npm run build --prefix transport && npm ci`, Node 24.x. Deployed `feat/adam/simulated-demo` @ `06f8af2` to production: https://ari-ai-lake.vercel.app (`/demo`, `/lab`).
- State: READY. Checked `/demo/lumen-pay/payments-api` (200, demo footer) and `/lab/bundles` (200, protocol-v1 and v0). Vercel Authentication is on (`all_except_custom_domains`), so `.vercel.app` URLs need a Vercel login on the team. No `BACKEND_ORIGIN` is set, so console pages (`/`, `/projects`) cannot reach a backend there.
- Warnings: the project is Git-connected, so pushes to `main` deploy to production and branches get previews. Until #226 merges, the next `main` push replaces production with a build without `/demo`.

### 2026-10-17 — Adam (cloud session) — simulated demo, Defense Lab command center, events-log cursor
- Did: (1) `feat/adam/event-log-cursor-follow`: phase views walk `/events/log` through `next_cursor` (cap 20 pages / 10,000 events); the honesty note only shows at the cap (Srihith's ask). (2) `feat/adam/simulated-demo`: new `/demo` route (ADR-0013) with GitHub-style sign-in → org → repo → recent commits → per-commit animated Overview / Code review / Seven agents / Defense Lab; new `components/defense-map.tsx` (docs/14 continuous-defense structure); `/lab` opens with `LabCommandCenter` replaying a recorded DDQN game from the validated bundle with headline scores.
- State of the code: both branches pushed; neither merged. The demo is hand-written illustrative data in `frontend/lib/demo/` and calls no backend.
- Commands run and results: frontend `lint`, `typecheck` clean; `npm test` 178 passed; `next build` ok; `tests/e2e/stack.py` 20 passed (Linux, Python 3.12, Chromium headless shell 1194 mapped to the path Playwright 1.64 expects).
- Decisions made (ADR links): [ADR-0013](../decisions/0013-labelled-simulated-demo.md) (Proposed).
- Assumptions / open questions: kairo.security could not be fetched from the cloud sandbox (egress denied), so the design follows the existing Ari system and the hero's prior Kairo pattern study. Sai's #214 Lab replay landed on main during this session; the command center sits above it as the overview, so the same recorded game is drawn twice — Sai may want to merge or trim.
- Next steps: open PRs for both branches; get Vihaan/Sai review of `/demo` and the Lab command center; ratify ADR-0013.
- Warnings for the next session: if agent roles, contracts or payments-lite change, update `frontend/lib/demo/scenarios.ts` (unit tests in `tests/unit/demo.test.ts` check order and consistency).
### 2026-10-17 — Adam (cloud session) — RL-005 no-action-path test; RL-001..005 marked DONE
- Did: added `backend/tests/test_rl_no_action_path.py`. The API schema exposes no route that applies a recommendation or acts on a defended system; the only write on recommendations is the enqueue POST; `DefenseRecommendation` always carries the simulation caveat and cannot set `simulation_derived=False`. Marked RL-001..005 DONE (code merged; acceptance criteria covered by the suites below).
- State of the code: branch `feat/adam/rl005-no-action-path`.
- Commands run and results: `uv run pytest -q backend/tests/test_rl_no_action_path.py backend/tests/test_rl_integration.py backend/tests/test_rl_expectations.py` 10 passed (rl_defense + torch installed, so the integration test ran, not skipped); `cd rl-defense && ../.venv/bin/python -m pytest -q` 92 passed (CPU, Linux, Python 3.12, torch 2.14.1).
- Decisions made (ADR links): none.
- Assumptions / open questions: one route is hidden from the OpenAPI schema (`/incidents/{incident_id}/transition`); the test reads the schema, so a future hidden route that acts on systems would not be caught. UI-007 (monitoring RL panel with caveat) appears implemented on main (`data-testid="rl-caveat"`) but is still marked BLOCKED/UNASSIGNED; left for its owner to update.
- Next steps: review and merge #225, #226 and this PR.
- Warnings for the next session: none.

### 2026-10-17 — Adam — create incident note
- Did: The open-incident form states it records a title and report. It does not contain systems, notify outsiders, or attach findings/events until linked. Default simulated drill stays labelled.
- State of the code: branch `feat/adam/create-incident-note`.
- Commands run and results: `npm test -- --run tests/unit/create-incident.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run..
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if createIncident starts linking evidence or notifying, change the note.
### 2026-10-17 — Adam — request scope note
- Did: The request-scope form states it records a pending authorization. Activation needs a different member's approval; it does not start a check or open access outside the listed targets.
- State of the code: branch `feat/adam/request-scope-note`.
- Commands run and results: `npm test -- --run tests/unit/scope-note.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if createScope activates immediately, change the note.
### 2026-10-17 — Adam — start assessment note
- Did: The start-assessment form states that it enqueues work for the selected repo, approved scope, and commit. It does not merge, deploy, or prove the app is secure. Stacked on event-log + cancel notes.
- State of the code: branch `feat/adam/start-assessment-note`.
- Commands run and results: `npm test -- --run tests/unit/start-assessment.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if start begins merging or deploying, change the note.

### 2026-10-17 — Adam — event log page note
- Did: Journey/assessment phases treat a full first `events/log` page (500) as truncated. Skipped-phase copy and a visible note say a later page may still hold the missing state. Merged cancel-assessment note (#191) under this branch so the assessment page carries both.
- State of the code: branch `feat/adam/event-log-page-note`.
- Commands run and results: `npm test -- --run tests/unit/lib.test.ts tests/unit/assessment-phases.test.ts` 41 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): backend still returns `next_cursor: null` even when truncated.
- Next steps: open the PR.
- Warnings for the next session: if events/log gains a real cursor, wire pagination instead of the full-page heuristic.

### 2026-10-17 — Adam — cancel assessment note
- Did: Assessment cancel copy matches POST /assessments/{id}/cancel: queued stops now; running gets a stop request; findings and recorded work stay. The UI shows the API's immediate vs requested outcome.
- State of the code: branch `feat/adam/cancel-assessment-note`.
- Commands run and results: `npm test -- --run tests/unit/cancel-assessment.test.ts` 2 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if cancel starts deleting findings, change the note.

### 2026-10-17 - Alan (Claude) - status accuracy, template review packet
- **Did:** merged #142 (Codex: Docker evidence reconciliation) and #143 (accurate verified and unverified list in `coordination/status/ALAN.md`). Opened #146: `python -m security_agents.checks.review_packet` and `docs/security/CHECK_TEMPLATE_REVIEW.md` so a human can sign off the two Probe templates against exact code and a renderer sha256 (SEC-005).
- **State of the code:** main @ `39186cb` had root 415 passed, 9 skipped and security-agents 451 passed, 18 skipped. #146 brings security-agents to 455 passed.
- **Open:** M3 live seven-role run (key holder); human template sign-off (SEC-005); published sandbox image digests (INFRA-005 needs a registry, which may cost money, so ask first).
- **Warnings:** Codex and Claude both work in the `hungry-einstein-178b03` worktree. Codex switched the checkout to its own branch mid-session, and a Claude commit landed on Codex's branch (it was moved before anything was pushed). Each agent should use its own `git worktree add` checkout.

### 2026-10-17 — Adam — index commit note
- Did: The repository list says indexing records symbols, calls, entry points, and expectations. The button enqueues `index_snapshot` only.
- State of the code: branch `feat/adam/index-note`.
- Commands run and results: `npm test -- --run tests/unit/index-commit.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if indexing starts an assessment, change the note.

---

### 2026-10-17 — Adam — create project note
- Did: The new-project form states that it records a name. The form sends `settings: null`, so monitoring uses the default enabled policy. It does not connect a repository or start a check.
- State of the code: branch `feat/adam/create-project-note`.
- Commands run and results: `npm test -- --run tests/unit/create-project.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if the form starts sending settings, update the monitoring sentence.

---

### 2026-10-17 — Adam — honest headline
- Did: Replaced "Every fix proven" and "Ship fast. Prove every change." A fix counts only when independent, executed tests agree.
- State of the code: branch `feat/adam/honest-headline`.
- Commands run and results: `npm test -- --run tests/unit/brand.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: do not put a ship or deploy claim back into the tagline.

---

### 2026-10-17 — Adam — connect repository note
- Did: The repository form states that it records a name and default branch. The form sends `installation_id: null`.
- State of the code: branch `feat/adam/connect-note`.
- Commands run and results: `npm test -- --run tests/unit/connect.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: if the form starts sending a real installation id, change the note.

---

### 2026-10-17 — Adam — monitoring summary
- Did: The monitoring page leads with recorded-event and simulation-recommendation counts. Unloaded lists are not counted. The sentence says this page is not the Defense Lab.
- State of the code: branch `feat/adam/monitoring-summary`.
- Commands run and results: `npm test -- --run tests/unit/monitoring.test.ts` 3 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none. A full page of 50 events is described as the newest page because the API does not return a cursor.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: do not describe monitoring events as a live network defense.

---

### 2026-10-17 — Adam — assessment phase list
- Did: The assessment page lists Understand, Review, Security test, Repair, and Verify from that check's event log. A missing log stays unmarked. Verify still needs a finding verdict. Monitor is not shown here.
- State of the code: branch `feat/adam/assessment-phases`.
- Commands run and results: `npm test -- --run tests/unit/assessment-phases.test.ts` 3 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): while the check is running, the phase list polls the event log every 5 seconds. It is not painted from the partial stream buffer.
- Next steps: open the PR.
- Warnings for the next session: do not add Monitor to this list unless the project monitoring feed was actually loaded.

---

### 2026-10-17 — Adam — finding list groups
- Did: The findings list open tab is no longer labelled "Validated and beyond." Open means not a hypothesis and not refuted, accepted, or closed.
- State of the code: branch `feat/adam/finding-groups`.
- Commands run and results: `npm test -- --run tests/unit/findings.test.ts` 1 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): verified and hardened stay on the open tab so a finished fix remains visible.
- Next steps: open the PR.
- Warnings for the next session: do not describe the open tab as confirmed exploits.

---

### 2026-10-17 — Adam — assessment phase sentence
- Did: The assessment page states its recorded state in one sentence. Completed does not mean the application is secure. Approval does not mean a merge.
- State of the code: branch `feat/adam/assessment-phase`.
- Commands run and results: `npm test -- --run tests/unit/phase.test.ts` 2 passed; `npm run typecheck` clean; `npm run lint` clean. Browser was not run.
- Decisions made (ADR links): none. The sentence uses only `assessment.state`.
- Assumptions / open questions (RQ links): none.
- Next steps: open the PR.
- Warnings for the next session: do not add phases the event log did not enter to this sentence.

---

### 2026-10-17 — Adam — lab step observation
- Did: Defense Lab replay adds a sentence for the current recorded step: highest alert rate and the recorded action. Hidden attacker stage is not rendered.
- State of the code: branch `feat/adam/lab-step-story`.
- Commands run and results: `npm test -- --run tests/unit/lib.test.ts` 26 passed; `npm run typecheck` clean; `npm run lint` clean. Browser replay was not run.
- Decisions made (ADR links): none. Alert rate is the observable used because the episode step records it and does not record an action mask.
- Assumptions / open questions (RQ links): a tie lists every node at that rate, in recorded order.
- Next steps: open the PR.
- Warnings for the next session: do not describe hidden_stage_ground_truth as something the defender saw.

---

### 2026-10-17 - Alan (Claude) - P1 SARIF and OWASP LLM agent evidence
- **Did:** #108 (shared 0.10.0 `cvss_v31`, worker `host.record_roe`) was merged by Adam at an older head. #112 reverted the Trace data-sensitivity fallback (main had been fixed by #107 instead). #109 updated the SEC task board. #114: SARIF 2.1.0 export validated against the pinned OASIS schema. #116: OWASP LLM 2026 matrix rows gain security-agents controls and root tests.
- **State of the code:** all offline or replay. M3 is still not met (no live seven-role run). NIST SP 800-61 alignment was already on main.
- **Commands run:** security-agents 452 passed, 12 skipped. standards 190 passed. tests/security plus standards 203 passed. Report 225 passed. Smoke writes `findings.sarif.json`.
- **Decisions:** severity to SARIF level is a project mapping. P1 started before the M3 live run because only a key holder can complete it and the user granted full control.
- **Next steps:** a key holder runs `RUN_LIVE_LLM_EVAL=1 LIVE_SEVEN_MAX_USD=8 .venv/bin/python -m pytest -q -m live tests/test_live_provider.py -s` in security-agents. Srihith: backend `record_roe` persistence and the RoE endpoint.
- **Warnings:** merge only after `gh pr checks` shows every job complete on the PR's current head (GitHub's PR head can lag a push).

### 2026-10-17 — Adam — project journey
- Did: Project page shows Understand, Review, Security test, Repair, Verify, and Monitor from the latest assessment's event log and the finding list. A phase the log did not enter stays skipped or not started. Verification is completed only when a loaded finding is verified or hardened.
- State of the code: branch `feat/adam/project-journey`. Frontend unit tests, typecheck, and lint passed locally (34 unit tests). Browser e2e was not run.
- Commands run and results: `npm test -- --run tests/unit/lib.test.ts` 34 passed; `npm run typecheck` clean; `npm run lint` clean.
- Decisions made (ADR links): none.
- Assumptions / open questions (RQ links): the event log read is one page of 500. A longer log can leave a later phase unmarked; the README says so.
- Next steps: open the PR. Do not treat a simulated monitoring event as a live check.
- Warnings for the next session: the branch was created tracking `origin/main`. Push with an explicit refspec.

---

### 2026-10-17 - Alan (Claude) - standards addendum P0, SEC-002, SEC-006, SEC-008, M3
- **Did:** Codex had moved the standards catalogs to `standards/` (#74). New branch `feat/alan/standards-roe-wiring` off main carried only the agent side. It was merged as #100 (`831edfa`). Changes: a rules-of-engagement gate in `ToolRegistry.invoke` (SCOPE_DENIED plus `tool.roe_denied`); the RoE is issued at SCOPE_CHECK and re-issued on host resume. Scout validates the CWE and takes its OWASP API tag from code. Trace proposes the eight CVSS v3.1 metrics with evidence; code computes the level (ADR-0012). Trace carries an optional validated ATT&CK id, and Diagnose and Fortify an optional D3FEND id. Added the offline report and smoke commands. Follow-up PR #103 adds an opt-in live seven-role smoke and partial M3 evidence.
- **State of the code:** replay/offline only. No live seven-role run has been observed. The demo finding is now CVSS 3.1 base 6.5 medium; labels.yaml says high.
- **Commands run:** `cd security-agents && .venv/bin/python -m pytest tests -q` gave 442 passed, 10 skipped. `cd standards && pytest tests -q` gave 189 passed. `python -m security_agents.standards.report` gave 217 passed, 0 failed. Smoke wrote 3 files.
- **Decisions:** ADR-0012 (CVSS v3.1 sets the level; v4.0 alongside). RQ-016 answered (proposed).
- **Open questions:** Srihith decides the v3.1 shared field (Option A or B) and the RoE endpoint. Adam decides on CWE-840 (Prohibited) in labels.yaml.
- **Next steps:** a key holder runs the live seven-role smoke (message `alan-to-srihith-adam-live-seven-role-run`). After M3: SARIF 2.1.0, SP 800-61 alignment, OWASP LLM self-assessment.
- **Warnings:** macOS keeps re-hiding the venv `.pth` files; set `PYTHONPATH=.:../shared:../standards:../code-intelligence:../examples/payments-lite/target`. Do not merge the old `feat/alan/seven-agent-runtime` branch; it carries duplicate catalogs.

### 2026-10-17 — Alan — verify honesty + CheckCategory
- **Did:** New branch `feat/alan/verify-honesty-check-category` off current main after #83 merged. Did not push `feat/alan/host-resume-hardening`. Honesty: kept #84's `provider_mode="none"` for functional Verify (did not merge #71/`mode=functional` while #85 is open). `_pinned_snapshot` already uses `SCHEMA_INVALID`; added a regression test. Canonical `CheckCategory` in `shared` 0.9.0 with Probe ids + V0 fixture aliases; scope_guard treats aliases as equal. Message to Srihith posted.
- **State of the code:** Fixture `AuthorizedScope.json` values unchanged. Probe templates unchanged.
- **Commands run:** `uv run pytest -q shared/tests/test_contracts.py security-agents/tests/test_scope_guard.py security-agents/tests/test_verify_followups.py security-agents/tests/test_host_adapter.py security-agents/tests/test_functional_verification_guards.py security-agents/tests/test_contracts.py` → **128 passed**, 1 skipped (`tsc` not on PATH). Frontend CI follow-up: `npm ci --prefix frontend/transport && npm ci --prefix frontend && npm run build --prefix frontend/transport && npm run typecheck --prefix frontend` (pass); `npm test --prefix frontend/transport` (27 pass); `npm run lint --prefix frontend` (pass). Local `npm test --prefix frontend` failed to start Vitest workers (Node v22.14 vs undici requiring ≥22.19; CI uses Node 24).
- **Decisions:** Honest option = label `none` (Srihith #84), not a fake/live Verify agent turn. Do not take #71's verify.py rewrite.
- **Next steps:** Land this PR; Srihith ACK of CheckCategory; #85 can land independently.
- **Warnings:** Do not push merged branches. Skip live Anthropic / Docker. Do not edit Adam's CWE-840 labels.

### 2026-10-17 — Alan — Codex P1/P2 host-resume hardening after #73
- **Did:** #73 merged to main. Did **not** push to `feat/alan/host-finding-persistence`. Diffed Codex P1/P2 against `origin/main` (`236838e`): all seven items were missing. New branch `feat/alan/host-resume-hardening` off current main. Implemented: resume `host.repo_id` filter; `claim_checkpoint` (assessment row lock, consume); finish Verify before `record_verification`; truncated pytest lists rejected; `external_pending` includes already-approved functional work; full recipe `target_dir` strip; persist `patch_rejected`. Asked to close #35 (do not merge).
- **State of the code:** Hosted resume is single-winner per checkpoint; functional Verify publishes only after the run finishes.
- **Commands run:** `uv run pytest -q security-agents/tests/test_host_adapter.py security-agents/tests/test_functional_verification_guards.py security-agents/tests/test_e2e_replay.py backend/tests/test_e2e_host.py backend/tests/test_intelligence_memory_api.py` → **82 passed**.
- **Decisions:** Follow-up PR rather than amending the merged branch. Did not merge #71 or #35.
- **Next steps:** Land the follow-up PR; close #35.
- **Warnings:** Do not push to `feat/alan/host-finding-persistence`. Do not merge #35 or #71.

### 2026-10-17 — Alan — port c75a56e pause gates onto #73
- **Did:** Ported Srihith follow-up `c75a56e` (not #71): `_report_or_wait` for early security exits; unconditional pause while `external_pending`; kept existing `_functional_reproduced` complete-run rule (no error / not timed out / pytest exit 1) and added the hang/`exit_code=None` case.
- **State of the code:** Standalone orchestrator now walks RECON→…→AWAITING_APPROVAL when a functional approval is owed; security auto-approve no longer skips a pending functional gate.
- **Commands run:** `uv run pytest -q security-agents/tests/test_host_adapter.py security-agents/tests/test_e2e_replay.py security-agents/tests/test_functional_verification_guards.py` → **68 passed** in 114s.
- **Decisions:** Did not merge #71; did not take `verify.py` `_functional_reproduction` or `compute_verdict` masking.
- **Next steps:** Reply on PR #73 comment; remaining Codex P1/P2 on #73 still open except this follow-up.
- **Warnings:** Do not merge #71.

### 2026-10-17 — Alan — host finding persistence, durable resume, functional Verify
- **Did:** New branch `feat/alan/host-finding-persistence` off current main. Did **not** continue `feat/alan/security-standards` (already on main via #49/#57/#69) and did **not** merge PR #35. Host adapter now: (1) `set_finding_status` from orchestrator status/patch/verify/fortify; (2) durable `save_checkpoint`/`load_checkpoint` (salvaged from #35, dropped its duplicate `standards/` and weaker `llm/schema.py`); (3) Sai's `request_functional_approvals` / `repair_approved_functional_findings` / `functional_verification_inputs` with `Orchestrator.external_pending`; (4) `tool_calls` + `sandbox_seconds` on the assessment ledger.
- **State of the code:** Fake-provider unified assessment: security finding `hardened` with full history; functional finding `verified` with five passing checks; resume after `pipeline._ORCHESTRATORS.clear()`. Live Anthropic still unverified (issue #10). Docker sandbox still UNVERIFIED (INFRA-005).
- **Commands run:** `make check` (ruff + shared export/examples); `uv run pytest -q security-agents/tests/test_host_adapter.py backend/tests/test_unified_assessment.py backend/tests/test_security_agents_host_mode.py backend/tests/test_e2e_host.py` → **27 passed**.
- **Decisions:** Functional Verify verdict is computed from executed tests, not an LLM turn (`llm_calls=0` on `functional-verify:*` runs). PR #35 should be closed, not rebased.
- **Next steps:** Draft PR to main; comment on #35 to close; Vihaan re-check finding status in the UI.
- **Warnings:** Do not merge #35. Do not reopen `feat/alan/security-standards`. CWE-840 on payments-lite labels is Adam's file.

### 2026-10-17 — Alan implementation agent — standards follow-up after #49/#57
- **Did:** Merged `origin/main` (Srihith's #57 already landed catalogs, incidents API, OWASP 2025/2026 matrices, producer CWE validators). Follow-up on `feat/alan/security-standards`: `Finding.standards.cvss` is now derived from recorded Probe/Trace facts via `standards.cvss_assess` (LLM eleven-metric dict stays provisional on `SeverityAssessment.cvss` only). Fortify D3FEND is copied onto `Finding.standards.d3fend`. Orchestrator `_classify` + `public()["standards"]`. LLM07:2025 coverage moved `gap` → `partial` with a system-prompt secret-shape scan. ACK'd Sai's CWE-adjudication review. Did not touch `frontend/` or add incident APIs (already on main).
- **State of the code:** One planted IDOR (CWE-639 / T1190 / fact-derived CVSS / D3-AMED proposed) classified in the fake-provider e2e replay. Not certification or compliance.
- **Commands run:** see the follow-up PR body (actual pytest output).
- **Decisions:** Authoritative CVSS is never the model's metric dict. Recorded attack-path preconditions map to CVSS AT:P.
- **Next steps:** Srihith ACK of the host-integration assertion change; frontend already renders `Finding.standards` via Vihaan's pages.
- **Warnings:** Do not claim the LLM cannot leak a system prompt; the new test only asserts prompts contain no secret-shaped literals.

### 2026-10-16 — Claude Code (development agent) — DOCS-003
- **Did:** Added interim proprietary `LICENSE` (all rights reserved) at the owner's
  request; updated LICENSE-DECISION.md, README status, task board, CHANGELOG. Set the
  GitHub repo description and topics. Corrected the INFRA-001 note below.
- **State of the code:** Still no application code.
- **Next steps:** Revisit the long-term license before the repo is made public.

### 2026-10-16 — Claude Code (development agent) — DOCS-001, INFRA-001
- **Did:** Completed Phase 0. Wrote docs 00–26, `architecture/`, `agents/`,
  `prompts/`, `planning/`, `decisions/` (template + ADRs 0001–0010, all
  *Proposed*), `research/`; READMEs for all eleven top-level code directories.
  Pushed to the private GitHub repo `adamissac/hackatl-agentic-security`, which
  had already been created with the base commit (`a59f0a8`) by the repository owner.
- **State of the code:** No application code exists. Only Markdown, repository
  config files and GitHub templates. Verified with a file listing of non-Markdown
  files.
- **Commands run:** internal Markdown link check (script, not committed) — all
  links resolve; secret-pattern scan over the tree — no matches beyond
  placeholders in `.env.example`.
- **Decisions:** All architecture choices recorded as *Proposed* ADRs pending
  team ratification (DOCS-002).
- **Assumptions:** Doc numbers 02, 03, 14, 18–22, 26 were not named by the
  original README/AGENTS/CLAUDE files; titles chosen from context (e.g. 14 = UX,
  18 = infrastructure per `.env.example`, 20 = demo, 26 = risks for architecture
  review). Demo target name `payments-lite` is a proposal.
- **Open questions:** RQ-011 (HackATL date/format), RQ-014 (team), and the rest
  of [RESEARCH_QUESTIONS](../research/RESEARCH_QUESTIONS.md).
- **Next steps:** Human authorizes Phase 1; assign roles; ratify ADRs; start
  CORE-001, DEMO-001, RL-001, INFRA-003, INFRA-005 in parallel.
- **Warnings:** Do not start Phase 1 work without explicit authorization. CI
  link/secret checks are not automated yet (INFRA-003).


### 2026-10-17 — Sarosh implementation agent — INTEL-001..008, PR #6

- Implemented persistent SHA-pinned Python intelligence on `feat/sarosh/code-intelligence`.
  Git object reads never execute target code; include_prefix excludes labels/answer keys.
  Tree-sitter/AST structure, static graph confidence, route dependencies, extracted intent,
  optional bounded Claude inference, delimited context and symbol-span impact are implemented.
- Uses canonical shared models, SQLite local and backend-owned PostgreSQL PayloadStore.
  Real signed-webhook integration persisted symbol-level head impact.
- Executed: producer/PostgreSQL **22 passed, 1 opt-in skip**; broader shared/monitoring/backend/
  intelligence **105 passed, 4 skipped** (RL package, TypeScript compiler, two live suites).
  Ruff/format/strict mypy pass; 337 Markdown files checked, 0 link problems.
- Actual-target evidence and canonical ContextPack fixture are in `code-intelligence/evidence`
  and `code-intelligence/fixtures`; small labeled exact graph TP=3 FP=0 FN=0.
- Live attempt skipped because authorized key unavailable here. FakeTransport exercised the
  shared gateway, denied a shell tool and persisted low-confidence inference metadata.
- Next: sync latest main and final CI; Sai/Alan replace temporary runtime indexing with this
  service. Human decisions, promoted regression/finding joins, router composition and parse
  caching remain follow-ups. No full runtime or production claim. Unrelated untracked
  `.serena/` and nested checkout were left untouched.

- Sarosh final verification (2026-10-17T01:56Z): on main sync `4a8d692`, broader suite
  **113 passed, 4 skipped** with isolated TypeScript 5.6.3 compiler. Skips: RL-dependent
  collection/test and two live suites. Intelligence/store/webhook acceptance previously
  **22 passed, 1 skipped**; subsequent actual factory/exclusivity check passed. PR CI for
  pushed `87e898d`: both test jobs, links and gitleaks passed. Check latest PR #6 revision
  and consumer adapter messages before further integration.

- Sarosh final failure-path check (2026-10-17T01:59Z): producer/PostgreSQL/webhook suite
  **23 passed, 1 opt-in skip**; invalid commit/path ingestion returns terminal failed
  Snapshot for the backend to persist. Ruff/format/strict mypy pass. PR #6 carries final
  commits and CI status; live inference remains blocked on an authorized key.

- Sarosh ownership review fix (2026-10-17T02:03Z): route-specific ownership rules and
  sibling-call-site evidence now reach the vulnerable payment read; get_store is not auth.
  A two-commit actual-target test proves the possible-inconsistency warning disappears when
  require_owner is restored. Broader suite: **115 passed, 4 skipped**. Index version 0.2.0
  requires re-indexing old 0.1.0 payloads; all fact/source keys remain immutable. Updated
  producer evidence/context fixture have 49 expectations and a 23,267 / 24,000 pack.

- Sarosh merged producer (2026-10-17T02:06:06Z): PR #6 → `bcc2e23`, exact tested tree
  `d608af5`. Final CI passed both test jobs, links and gitleaks. Final focused suite:
  **24 passed, 1 skipped**; broader suite: **115 passed, 4 skipped**. Consumers should pull
  main and re-index at python-ast-ts/0.2.0. Full reviewer/security pipeline proof and live
  inference remain outstanding; see the immutable merged-producer message.

### 2026-10-17 — Sarosh / Codex — UI-001/UI-003 transport assistance

- User authorized isolated help in unfinished lanes and frequent direct main commits.
  Reserved only `frontend/transport/**` in issue #29; no remote Vihaan branch or
  scaffold existed at each milestone check. No app, backend or shared source edits.
- Implemented standalone strict TS/ESM package consuming canonical generated types
  and JSON schemas. Session/CSRF, explicit approvals, idempotency, lists/logout,
  bounded JSON and SSE, Last-Event-ID resume, retries, abort and terminal-state
  verification. API contract mismatches found by integration were fixed.
- Executed `npm test --prefix frontend/transport`: 13 passed plus strict TS build.
  `npm run test:integration --prefix frontend/transport`: 4 passed (parent + 3
  subtests) against real FastAPI and isolated PostgreSQL; zero model calls/target
  executions. Ruff/format of Python harness passed. Initially mislabeled 4-pass
  status corrected explicitly: first unit run was 3 passed/1 failed, later fixed.
- Commits: `d4a0fce`, `3bc4e28`, `05ce162`, `a48a202`; pushed main per user direction.
- Next: Vihaan adopts file dependency and connects pages; Adam/Vihaan wire transport
  commands into frontend CI. Browser CORS/cookies/rendered behavior remain unverified.
- Sai's real-index consumer integration merged PR #31. Sarosh attempted its test
  but collection failed because local environment lacks installed `code_review`;
  no independent consumer-pass claim. Producer suite was rechecked separately.

### 2026-10-17T04:35Z — Sarosh recovered intelligence-memory continuation (PR #39)

- Recovered the Codex worktree `/tmp/hackatl-sarosh-memory` on
  `feat/sarosh/intelligence-memory`; no work was discarded. Fixed the CI failure
  in `PostgresMemoryStore.decisions` (partitioned `expectation_id` was not in the
  subquery select) and the new test's use of string agent names.
- Merged `origin/main` through `dd9f8a9`, preserving Sai's functional-remediation
  changes. Kept the reviewer only on Sai's paths (`_runner` shared-intelligence
  hook plus one test-double check) and added Alan's host adapter wiring as a small
  isolated hook.
- Implemented durable accepted-Verify -> verified-regression outbox, exact-snapshot
  host snapshot attachment, shared hosted intelligence service use, context/API
  surfacing, and all-five-check enforcement at the trusted host boundary.
- Executed on the merged tree: backend/shared/monitoring/reviewer **154 passed,
  5 skipped**; code-intelligence/shared **62 passed, 2 skipped**; code-review
  **48 passed, 1 skipped**; security-agents **344 passed, 11 skipped**; focused
  memory/host tests **7 passed**. Ruff/format changed paths, strict mypy for
  root/code-intelligence/code-review, contract freshness (92 files, 0 changed),
  and 223-document link check passed.
- Remaining gate: Srihith's contract-owner ACK of additive schema 0.6.0 on #38/PR
  #39. Live provider, RL package, browser/app e2e and full hardened deployment
  remain separate prerequisites; no such claim is made here.

## 2026-10-17 — standards integration (Srihith)

Continued from PR37 prompt on branch `feat/srihith/standards-alignment`. Read GitHub and adopted Alan PR49 canonical0.7 contracts/catalogs/scoring/lifecycle rather than a competing schema. Added real backend incident persistence/API/approval/evidence/audit gates, report standards, host atomic classification updates, producer integration, dual OWASP2025/2026 coverage and transport helpers. New migration0005. Contract generation fresh. Root193 passed/2 live skips; security/reviewer400 passed/11 optional skips; RL82 passed; transport16 unit+4 real-stack tests. No paid calls, deployment or infrastructure response. Review stacked PR before merge; coordinate frontend page mounting with Vihaan and other standards additions with Alan.

Final review follow-up: added strict regression artifact/hash proof, succeeded-Verify requirement, and demo incident labels; model CVSS interpretations are provisional assumptions rather than claimed verified metrics. Adopted Alan’s additional canonical tests/CI at80fb0e7. Expanded root suite304 passed/2 live skips; separate migration coexistence and incident suite5 passed after allocating revision0005_incidents (avoids PR39’s0005). Final standalone tests include standards126, security/reviewer400, RL82 and transport16+4.

### 2026-10-17 — Codex — isolated recovered catalog additions
- Recovered Claude dirty-worktree changes without discarding any work. Saved checkpoint 93c0225 and tested recovery on PR #35.
- Merged PR #69 after observing all checks pass. New PR #73 supplies a narrower host recovery/functional-Verify path, so unique catalog additions are isolated on feat/alan/recovered-catalogs; duplicate legacy runtime catalogs from #35 are excluded.
- New canonical modules: CVSS3.1 base scoring, OWASP API2023 project mappings, NIST800-115 headings and explicit source-hash refresh tool. No shared schema change. NVD terms retain their existing unverified label.
- Saved previous broad verification evidence in coordination/status/ALAN.md; rerun checks on this branch before merge.

## 2026-10-17 — frontend expectations / incidents / settings pages (Devin for Adam)

Branch `devin/1792226026-frontend-gaps` (merged `origin/main` at efc0e55). Frontend owner idle; these routes were specified in docs/14 with backend APIs already on main. Added transport `expectations`, `decideExpectation`, `expectationDecisions`, `updateProject` (PATCH; additive, no contract change) and pages `/projects/[id]/expectations`, `/incidents`, `/settings`. Executed: transport 27 passed; app ESLint, tsc, Vitest 29 passed, Next build; browser e2e via `tests/e2e/stack.py` 11 passed (3 new) against real FastAPI + PostgreSQL + worker with scripted (non-live) model transports. Not done: incident closure/lessons-learned/evidence-link UI, functional-remediation pause/resume (Alan #73), live provider, Docker sandbox proof.

### 2026-10-17 — Codex — functional verification safety and recovery consolidation
- Continued newer Alan PR #73 rather than landing duplicate legacy runtime standards from PR #35. Unique recovered canonical catalog code is PR #74 (189 offline tests). PR #69 merged.
- Tightened independent functional verification: empty test bundles, runner/collection errors, timeout, skipped tests and fabricated sandbox evidence cannot pass. Generated tests cannot replace existing files. Resume rechecks current scope and binds the active host/job.
- Adopted only PR #71's two-remediation browser fixture. Both security and functional fixes complete through the actual API/queue/worker/DB stack using scripted model replies.
- Commands/results: root pytest 324 passed/6 optional skips; security pytest 379 passed/11 optional skips; focused authorization/evidence guards36 passed; PostgreSQL subset11 passed; browser8 passed; Ruff/format/mypy pass.
- No live-model call, hardened Docker execution, deployment or always-on operation was performed.

## 2026-10-17 — frontend expectations / incidents / settings pages (Devin for Adam)

Branch `devin/1792226026-frontend-gaps` (merged `origin/main` at efc0e55). Frontend owner idle; these routes were specified in docs/14 with backend APIs already on main. Added transport `expectations`, `decideExpectation`, `expectationDecisions`, `updateProject` (PATCH; additive, no contract change) and pages `/projects/[id]/expectations`, `/incidents`, `/settings`. Executed: transport 27 passed; app ESLint, tsc, Vitest 29 passed, Next build; browser e2e via `tests/e2e/stack.py` 11 passed (3 new) against real FastAPI + PostgreSQL + worker with scripted (non-live) model transports. Not done: incident closure/lessons-learned/evidence-link UI, functional-remediation pause/resume (Alan #73), live provider, Docker sandbox proof.

## 2026-10-17 — incident closure UI (Devin for Adam)

Branch `devin/*-incident-closure` from main after #80. Added `components/incident-closure.tsx` (link finding / accepted verification, add + satisfy closure criteria with hash-pinned patch-diff or Fortify regression artifacts, lessons learned, communications, closure readiness) and pure helpers in `lib/incidents.ts`. Executed: tsc, ESLint, Vitest 32 passed, real-stack Playwright incl. new closure spec. Found (not fixed, other lanes): no producer sets `HardeningRecommendation.proposed_test_artifact_id`, so lessons learned → closure is unreachable for hosted assessments; see coordination/messages/20261017T093000Z-adam-to-alan-srihith-fortify-regression-artifact.md.

## 2026-10-17 — REG-001 complete: Devin closure evidence continuation

Branch `fix/srihith/fortify-closure-artifacts`, based on merged Devin PRs80/81 and Alan PR83. Real security and functional Verify now retain bounded, unchanged regression sources as redacted ArtifactStore archives and publish their actual canonical hardening artifact IDs. Incident eradication resolves remediation-plan approval to the same finding/project/assessment. Evidence selectors now accept options that arrive after linking a finding.

Executed: focused host/unified/verification/closure54 passed; root325 passed/7 skips (optional RL dependency, global tsc unavailable, opt-in live providers); security-agent396 passed/11 optional skips; frontend32 unit tests; real PostgreSQL/API/worker/Chrome browser12 passed, including complete two-person incident lifecycle through Closed and reload. ESLint, TypeScript, production build, Ruff/format, mypy and generated contracts110-file freshness passed. Browser runs use scripted transports; no paid provider calls, deployment or individual test-node execution claim.

### 2026-10-17 — recovered catalog integration refresh
Synced PR74 with current main after PR83/85/87, preserving both task histories and changelog entries. Corrected README: CVSS3.1 is an explicit reference calculator, while runtime severity retains CVSS4.0. Standards189 passed, manifest integrity and Ruff/format pass. No live data refresh or provider call.

### 2026-10-17 — QA-001: finish unexecuted Devin QA tools
Replaced invalid imports/nonexistent npm commands and skip-as-success summaries in PR94’s proposal. Scripts use the current interpreter, do not install dependencies, force live model evals off, resolve paths independently of cwd, and execute existing suites. Added runner semantics CI. Executed on the proposal base: helper10 passed; security wrapper backend/monitoring/reviewer/intelligence162 passed/7 optional skips and security-agent394 passed/11 optional skips; frontend wrapper transport/tsc/lint/build/Vitest32 and actual-stack Chrome12 passed. Preflight from /tmp and optional RL runner intentionally return2 (incomplete: no Docker, RL package, or persistent API on this machine). No paid calls or deployment. Readiness report now records evidence and points to current milestones rather than stale blockers.

### 2026-10-17T13:22Z — Codex resume handoff (PR #39 already merged)

PR #39 landed on `main` as `2ab5584` at 05:30 UTC; issue #38 is closed. The
recovered worktree was fast-forwarded to `origin/main` at `39186cb` and is clean.
No commits from the intelligence-memory lane remain unmerged. The full resume
brief for the next Codex session — original failure, fixes, implemented files,
commits, verification, next actions and warnings — is
`coordination/messages/20261017T132209Z-sarosh-to-codex-recovered-memory-handoff.md`.
Post-merge smoke checks: memory/contract suite **8 passed**; regression/host/
reviewer integration suite **11 passed** after `uv sync --frozen`.

### 2026-10-17 — Alan security Docker evidence reconciliation
- Continued from Claude's current main-based worktree; #73/#74 are merged and #35 is closed. Left the separate Devin M7 branch and untracked reports intact.
- Inspected successful security CI 39033028814 (`5b3ef50`): 6 real Docker tests passed; ordinary suite 451 passed/18 skipped. Corrected stale execution-unverified and integration-pending README claims.
- INFRA-005 still needs published image digests; CI's temporary image tag is not a published release. Live seven-role provider proof remains separate.

### 2026-10-17 — Sarosh / Codex — recovered context and ingestion scope cache

- Read recovered handoff PR #145 (`e5fee45`, later merged #145); confirmed #39
  merged and #38 closed. Synced current main and installed frozen workspace.
  Recovery memory smoke: **8 passed**. No recovered implementation was redone.
- Found/reproduced #148: same project/repo/SHA/parser-version cache ignored the
  operator's include prefix and limits; an invalid prefix could hit the cache
  before validation. A new regression test failed on the inspected main.
- Fixed in `fix/sarosh/intelligence-scope-cache`: normalized/validated prefix,
  profile-aware immutable versions for cache/snapshot/diff paths, provenance from
  the exact ref. Parser base 0.3.0; non-default profile digest fits existing DB
  column. Existing refs and exact-snapshot decisions stay unchanged.
- Executed: new producer cases **6 passed**; broad intelligence/shared/PostgreSQL
  checks **97 passed, 1 live opt-in skipped**; new real PostgreSQL profile case
  **1 passed**; backend M7/host/unified and reviewer consumer checks **11 passed**
  with scripted test transports. Ruff/format and strict producer mypy passed.
- No new live-provider, local Docker, browser or deployment proof claimed.
  Active security/frontend owner branches were left untouched.
### 2026-10-17 — QA-002 M7 runner isolation
- Continued unfinished Devin runner: uses selected Python, no uv PATH requirement, no shared devdb start/stop, forced live-provider opt-out, ignored report output with --output override. Removed hardcoded branch metadata.
- Executed M7 through isolated PostgreSQL fixture successfully; wrapper exits2 truthfully because browser is unexecuted. QA unit suite13 passed; Ruff/format and link checks passed.
- Primary Devin checkout and its existing untracked reports remain untouched. No live model or deployment.

### 2026-10-17T13:45Z — Sarosh ingestion profile fix merged

PR #150 merged as `1129434`; issue #148 closed. Product fix `5015d67` was tested on combined head `91cea8c`, including Sai typography main. All executable CI checks passed (backend417, producer33, reviewer60, security455, Docker security6/reviewer2, Vitest67/browser12; skips and exact commands recorded in the PR). Final consumer migration, remaining human/account gates and M5 owner reconciliation request: `coordination/messages/20261017T134500Z-sarosh-to-all-profile-fix-merged.md`. Existing primary checkout untracked paths and other owners worktrees were preserved.

### 2026-10-17T14:21Z — Ari logo integration awaiting Sai guidance

Owner explicitly asked to consult Sai UI agent and place official ant logo according to its answer. Original asset is pushed unchanged on `feat/sarosh/ari-logo` at `frontend/public/brand/ari-ant.png`. Issue #165 requests surfaces, wordmark, dark/light and intro treatment; Sai was also tagged on #164. Do not invent a reply or claim the logo is integrated: UI files are still unchanged. Isolated worktree `/tmp/hackatl-sarosh-ari-logo` has installed frontend/transport dependencies. Baseline `npm run test -- --run tests/unit/brand-ui.test.tsx tests/unit/brand.test.ts`6 passed, `npm run typecheck` passed. Vihaan #156 does not overlap proposed brand/intro files; recheck active ownership before editing.

### 2026-10-17T14:39Z — Actual Sai logo sign-off received

Sai responded in #165 and merged message #176. PR #169 now follows it: shared header/sign-in24px, intro96px with700ms fade/scale0.94, original-byte app/icon.png; Ari AI text and header/intro contracts preserved; provisional landing mark removed. This supersedes the earlier pending-reply handoff. Source-byte checks/unit71/tsc/lint and provisional browser2 pass; rerun full browser suite on combined main before merge. Invalid dev-login HTMLpattern discovered and corrected equivalently; mobile story overflow reported to #156 without touching that owner work.
### 2026-10-17 — Codex primary application sprint — API-RETRY-001
- Base37b0920c37d0460ecf988126693e79761478a888; dedicated worktree /private/tmp/omen-codex-integration, branch fix/alan/demo-api-reliability. Existing Claude/Devin checkouts untouched. Reviewed125/133/142/143/146/147 (all merged); active156/161 frontend changes avoided. No attributable active Grok branch found.
- Confirmed before fix: two same-key concurrent API requests created distinct assessment/job IDs; same body/key on another project URL returned the first project's acceptance.
- Added transaction advisory lock per user/key; project-bound request hash; four PostgreSQL/API regressions cover replay, conflicting concurrent body, cross-project key reuse, rollback after scope denial. No shared schema change.
- Executed with existing Python3.12 interpreter and local checkout packages on PYTHONPATH: pytest backend/tests/test_assessment_retries.py backend/tests/test_api_core.py -q (16 passed); pytest -q (420 passed/9 skips); pytest backend/tests -q (79 passed/5 skips). Logs /tmp/omen-{retry-before,retry-after,root-tests,backend-tests}.log. Ruff/format clean, mypy38 sources, shared.export112 files current, shared.examples current, links320 files clean.
- Root test includes the existing API/worker M7 scripted chain. Not run here: browser, frontend build, Docker or live provider. Optional RL deps and tsc absent; live/Docker opt-ins skipped. Prior cached retry keys use the old hash and return409 after upgrade; new actions need fresh keys.
- Next: reviewer inspect retry PR; do not auto-merge under this sprint instruction. Claude owns Probe/Docker/RoE/SEC-005/INFRA-005. Existing issue155 (stored patch hash mismatch) and the latest inconclusive live Probe/M3 run remain demo blockers for those owners.

### 2026-10-17 — Codex DEMO-FONTS-001
- Isolated /private/tmp/omen-offline-fonts from d1d324d. Original webpack build fails fetching all three fonts with HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=http://127.0.0.1:9; font tests2 failed before change.
- Vendored115,760 bytes of unmodified WOFF2 from integrity-checked Fontsource5.3.0 tarballs, included all three OFL licenses and asset hashes; next/font/local preserves family/weight variables. No extra runtime dependency.
- Same unavailable proxies: default npm run build passes. Frontend69 unit tests (including2 local-font checks), typecheck/lint pass. No UI redesign; other layout/branding hunks preserved. PR for review only.
- 2026-10-17 Adam: merged origin/main after #187 (conflict recovery); kept both handoff/task/status entries.

### 2026-10-17 — Codex DEMO-RECOVERY-001
- Isolated /private/tmp/omen-final-recovery, branch fix/alan/explicit-conflict-recovery from d1d324d. Two component tests failed before change;3 pass afterward (same network retry, explicit conflict recovery, project isolation). Frontend70 tests, typecheck/lint/build pass.
- Full Chrome13 passed, including actual server409 seeded with a conflicting key, unchanged retry retaining key, preparation sending no request, and a fresh submission creating one additional assessment/job. Other journey tests use scripted agents and real API/DB/worker. The recovery test uses an unbound owned fixture so it cannot consume scripted agent turns.
- Preserved active #156 layout/class hunks and #184 primitives; no API/shared/security changes. PR for review; no own merges. Separate font PR removes external build downloads.

### 2026-10-17 — Codex API-JOB-001 real demo verification
- Separate worktree /private/tmp/omen-demo-smoke, branch test/alan/demo-recovery-smoke, initial base30e1234b508c82a37f335da341daeda8f207e6bc; synchronized056f65b. PR166 was merged by Srihith; no own PR automatically merged.
- Before fix: real JS client receives job_id, then owner GET/jobs returns404. Fix resolves project from the actual assessment. New backend/member/outsider tests and real HTTP retry smoke pass.
- Executed: pytest -q422 passed/8 optional skips; pytest backend/tests -q80 passed/5 skips; targeted API13 passed; production Next webpack build, Chrome14 passed; transport HTTP6 passed, unit27 passed; frontend67 unit tests, TS/ESLint clean; Ruff/format, mypy38, generated contracts112 files current.
- Browser exercises real API/PostgreSQL/worker/UI, scoped scripted reviewer/security/repair/Verify, persisted evidence and reload, incident closure. New cases inject transient API loss and verify Retry/reload; missing assessment uses real404. Node harness uses real HTTP/DB (no worker/models), intentionally loses one accepted response, checks original IDs, conflicts and fresh key, verifies redacted artifact bytes and rejects injected corruption. Temporary fixture DBs/artifacts cleaned.
- Initial Turbopack build failed fetching Inter from Google; webpack build succeeded twice and fresh post-sync Chrome14 passed. No live provider or Docker execution claimed; optional RL deps/live/Docker tests skipped.
- Ownership/handoff: no product frontend, Patch/shared contracts, RoE or Docker internals changed. Frontend owner should add explicit new-action recovery for a persistent409 (unchanged fields retain key); keep same-key network retries. Issue155 remains the raw-vs-stored Patch hash contract assignment. PR for review only. Exact repeatable smoke commands in frontend/transport/README.md and PR; logs /tmp/omen-{browser-final,client-before,client-evidence,demo-root,demo-backend}.log.
### 2026-10-17T14:42Z — Ari logo verification complete, CI next

#169 follows actual Sai sign-off #165/#176. Unit71, tsc/lint/build passed; full real-stack Chrome/PostgreSQL/API/worker browser14 passed47.9s; focused screenshot2 passed13.6s. Both PNG copies match original source hash. Exact commands in PR; final screenshots `/tmp/ari-logo-proof-final`. Await CI green then normal PR merge. No other owner product code was edited except preserving incoming main through merge; shared header/intro contracts intact.

### 2026-10-17T14:52Z — Ari logo merged, #165 closed

PR #169 merged as `b38b6ff` after all executable checks passed on `4b48388` (unit71, browser14, build/types/lint/docs/gitleaks). Actual Sai sign-off #165/#176 applied exactly; source PNG copies remain byte-identical. Initial CI browser job failed in Next font resolution before tests; unchanged-head retry passed. Local full browser14 and final screenshot2 passed. Final immutable receipt: `coordination/messages/20261017T145240Z-sarosh-to-sai-ari-logo-merged.md`. Sai can resume reserved UI files. Existing mobile story overflow remains with #156; no deployment/live-provider proof claimed.

### 2026-10-17T15:23Z — Maintenance cycle1 context citation privacy

Issue #190 reproduced with a fake bearer-shaped canary: chunk text redacted but raw human reason remained in citation metadata and public expectation source notes. Redact free-form note/provenance in copied output references; exact identities, stored history/index remain untouched. Code-intelligence only product edits; no shared DTO or index-version change. Focused10, broader producer/shared/PostgreSQL107 passed/1 opt-in skip, hosted consumer11 passed67.49s; Ruff/format/mypy12 modules passed. Worktree /tmp/hackatl-sarosh-maintenance, feat/sarosh/intelligence-maintenance. Ten-minute message loop remains active; next scheduled read15:24:40Z.

### 2026-10-17T15:35Z — Maintenance cycle2/3 relative-import grounding

Privacy #196 merged4119eff; #190 closed. Issue199 synthetic Git/AST repro showed an impossible relative import grounded as exact to unrelated outside.helper. Fix preserves unresolved spelling and diagnostic; parser0.3.1 ensures cached0.3.0 facts are not silently reused. Active SHAs need normal reindex; old facts/decisions remain immutable. Focused15, broad123/1 opt-in skip, hosted consumer11 passed67.42s; strict mypy12/Ruff/format passed. Worktree /tmp/hackatl-sarosh-import-grounding; branch fix/sarosh/relative-import-grounding. No target code executed by index tests, live provider or UI-owner edit. Loop check15:34:42 read Sai Lab redesign and preserved ownership; next15:44:40.

### 2026-10-17T15:53Z — Takeover: relative-import grounding (PR #201)

Took over the prior Codex session's active worktree `/private/tmp/hackatl-sarosh-import-grounding`
on `fix/sarosh/relative-import-grounding`. Merged current `main` (`a696361`) cleanly.
Re-executed focused relative-import/privacy **25 passed**, broader producer/shared/PostgreSQL
**122 passed, 2 skipped**, hosted reviewer/security/M7 consumer **11 passed**; Ruff/format,
strict producer mypy 12 modules, 345-document link check and `git diff --check` passed.
Polished changelog/README/task wording. Parser 0.3.1 keeps beyond-package relative imports
unresolved with a diagnostic; active SHAs need normal reindex, historical refs/decisions
remain immutable. PR #201 is the delivery vehicle; no target code executed and no live
provider/UI-owner edit.

### 2026-10-17 — Vihaan backend review-cycle fixes

Branch `codex/backend-artifact-snapshot-fixes` fixes same-process artifact temp-file
collisions and ready snapshot reuse across parser/profile changes. Adds public
`current_index_version(project_id, repo_id)` on IntelligenceService; no DTO/schema
change. Existing history stays readable; unavailable version resolution returns
503 on ready refresh. Initial/failed requests retain asynchronous enqueueing.
Local intelligence suite: 53 passed, 1 live skip before the additional version
registration test; strict producer mypy passed (12 modules), Ruff/format passed.
Real PostgreSQL tests cannot initialize locally: sandbox denies SysV shared memory
and pgserver process enumeration. CI must verify the new backend regressions.

## 2026-10-17T18:45:19+00:00 — Sarosh: ARI frontend polish (local)

Branch: `feat/sarosh/ari-frontend-polish` in `/tmp/ari-website-polish`, based on
`e715ac7` from `adamissac/ari-ai` (owner corrected the original slide-deck URL).
Updated header/hero/brand/pricing copy/metadata, one local font plus license/hash,
and related unit/browser checks. Kept ant assets and existing body/display/mono fonts.

Executed: `npm run lint --prefix frontend`; `npm run typecheck --prefix frontend`;
`NEXT_PUBLIC_DEMO_ONLY=1 npm run build --prefix frontend` (all passed);
`npm test --prefix frontend -- --maxWorkers=2` (49 files, 211 tests passed);
`E2E_BASE_URL=http://127.0.0.1:3015 E2E_DEMO_ONLY=1 E2E_BROWSER_CHANNEL=chrome ARI_LANDING_SCREENSHOTS=/tmp/ari-polish-screens npm run e2e --prefix frontend -- tests/e2e/landing.spec.ts --reporter=list` (5 passed).
Default parallel unit rerun hit one existing logo-file test's 5s timeout under concurrent
build load; rerun with 2 workers passed without changing any timeouts.
Rendered Chrome inspection covered desktop/mobile hero and scrolled light/dark content;
no browser warnings/errors observed. Screenshots/logs are outside Git under `/tmp/ari-polish-*`.
The full authenticated backend e2e suite was not run. No provider calls, API changes,
push, merge or deployment. Next action: review local diff; publish via reviewed branch
only when authorized (Git-connected pushes create Vercel previews).

## 2026-10-17T19:01:17+00:00 — Sarosh: transparent navigation and warm headline refinement

Local branch `feat/sarosh/ari-frontend-polish` in `/tmp/ari-website-polish`; integrated
current main (`f81ab5a`) before changes. Removed logo/link cards, borders and shadows;
ordinary links have subtle translucent hover fills. Pricing alone retains a solid accent.
`HeaderContrast` samples the painted surface beneath each control, adapting white/ink
text and the ant over hero, light chapters, later dark chapters and light cards.
Uses passive scroll/resize listeners and coalesced animation frames; attributes change
only when the tone changes. No dependencies or geometry/link changes.

Headline content, structure, CTAs, workflow pills, spacing and background are untouched.
Only its text styling gains a warm off-white core and three diffuse ember shadows.
The dimmest core exceeds 4.7:1 against the solid brand color (unit assertion);
no added filter or animation.

Executed: transport build; `npm run lint --prefix frontend`; `npm run typecheck --prefix frontend`;
`npm test --prefix frontend -- --maxWorkers=2` (50 files, 215 passed);
`NEXT_PUBLIC_DEMO_ONLY=1 npm run build --prefix frontend` (passed);
`E2E_BASE_URL=http://127.0.0.1:3015 E2E_DEMO_ONLY=1 E2E_BROWSER_CHANNEL=chrome ARI_LANDING_SCREENSHOTS=/tmp/ari-nav-glow-screens npm run e2e --prefix frontend -- tests/e2e/landing.spec.ts --reporter=list`
(5 passed, 25.4s; 1440/1024/390/320px plus reduced motion). Verified transparent
controls, dark/light/later-dark contrast, hover, 44px targets, routes, no overflow, warm
headline styling and retained content. Corrected a browser-test race that interrupted
the smooth anchor animation before checking the next scroll position.
Rendered desktop/mobile and scrolled light views inspected; no browser warnings/errors
observed. Full authenticated backend e2e not rerun. No push, merge to main or deployment.
Screenshots and logs remain external to Git at `/tmp/ari-nav-glow-*` and `/tmp/ari-nav-*`.
Next action: review local commit and authorize publishing if desired.

## 2026-10-17T19:10:13+00:00 — Sarosh: homepage navigation scroll reveal (local)

Branch `feat/sarosh/ari-frontend-polish`, `/tmp/ari-website-polish`. Integrated
main through `0f645c2`, preserving Sai's refreshed README and the newer local ARI
branding/font details during its sole merge conflict.

`SiteNavigation` reveals Projects/Defense Lab/Demo/Pricing at `scrollY >= 100`,
independent of direction, and hides them below it. Group geometry remains reserved;
logo/account remain visible and Sign In's x/y stay unchanged. Hidden group uses
`inert`, `aria-hidden`, visibility and pointer blocking. Transition: 250ms fade +8px
horizontal offset, removed for reduced motion. Other routes always show all links.
The checkout had no separate mobile menu, so added a compact phone Menu disclosure
with Escape/focus restoration; crossing the threshold closes it. No hero/content,
headline, font, background, workflow, CTA, backend or API changes.

Executed: `npm run lint --prefix frontend`; `npm run typecheck --prefix frontend`;
`npm test --prefix frontend -- --maxWorkers=2` (51 files, 219 passed);
`NEXT_PUBLIC_DEMO_ONLY=1 npm run build --prefix frontend` (passed);
`E2E_BASE_URL=http://127.0.0.1:3015 E2E_DEMO_ONLY=1 E2E_BROWSER_CHANNEL=chrome ARI_LANDING_SCREENSHOTS=/tmp/ari-reveal-screens npm run e2e --prefix frontend -- tests/e2e/landing.spec.ts --reporter=list`
(5 passed, 36.6s, 1440/1024/390/320px and reduced motion). Browser assertions prove
0/99 hidden, 100 shown, decreasing above threshold shown, returning to zero hidden,
hidden-focus rejection, phone menu/Escape, unchanged account coordinates, no overflow
and full navigation on pricing. Desktop/mobile top and revealed screenshots inspected.
Existing brand e2e scroll checkpoint updated from 80px to 120px; its full backend
suite was not rerun. Screenshots external to Git: `/tmp/ari-reveal-screens`.
No push or deployment. Next action: review local commit; publishing remains pending.

## 2026-10-17T19:14:09+00:00 — Owner-authorized publication to main

The owner explicitly requested pushing the accumulated frontend changes to main.
Product commits: `391831a`, `ed37e30`, `b2961f9`; current main base: `0f645c2`.
Scope: ARI branding, local wordmark font, centered warm headline, transparent
contrast-aware navigation and homepage 100px reveal with phone Menu access.
Final unchanged product tree passed lint, type-check, demo-only production build,
219 unit tests (51 files, two workers) and 5 Chromium browser tests (36.6s), covering
1440/1024/390/320px, reduced motion, top/reveal/return, inert focus and anchored account.
No backend/API changes in the publication diff. Earlier local-only notes describe
the state before this authorization. Publishing via a normal fast-forward push;
next action is to confirm GitHub main matches the release commit and inspect CI.
The Git-connected Vercel deployment is triggered by that push; no project settings changed.

## 2026-10-17T19:50:00+00:00 — Adam: GitHub onboarding (org → repo) and labelled simulated attacks

Branch `feat/adam/github-onboarding` (merged as PR #239).
- Backend: GitHub sign-in now lands on `/connect`; returning from installing the App
  (`setup_action=install|update`, no OAuth state) restarts sign-in instead of a 403, which
  refreshes the repository list. `GITHUB_APP_PRIVATE_KEY_PATH` also accepts pasted PEM
  contents (now SecretStr), because Railway has the key under that name.
- Frontend: `/connect` lists organizations (owners of App-visible repositories);
  `/connect/[org]` lists repositories, and Connect finds-or-creates a project named after
  the organization, connects with the installation id and opens the project. Projects
  page links to it. Demo-only builds redirect `/connect*` to `/demo`.
- Simulated attack: `/projects/[id]/simulate?repo=` replays the demo's seven-agent run and
  Defense Lab for a real connected repository, with a visible "Simulated demo" marker and a
  note that nothing in the repository was read or attacked (ADR 0013). Live attacks remain
  Python-ASGI-only. `CommitAnalysis` moved to `components/demo/commit-analysis.tsx`.
- Railway: all seven secrets are present but **staged, not deployed**; `GITHUB_APP_SLUG`
  is not set (install link hidden). Vercel still has `NEXT_PUBLIC_DEMO_ONLY=1`; unset it
  once GitHub sign-in works against Railway.
Executed: backend key/access tests (9 passed), frontend lint, typecheck, 224 unit tests,
production build. E2E result recorded in the PR.

## 2026-10-17 — Srihith: owner-requested product readiness audit

Published the complete feature inventory, evidence boundaries and prioritized checklist as [issue #244](https://github.com/adamissac/ari-ai/issues/244), with dated report in [PRODUCT_READINESS_AUDIT_2026-10-17.md](PRODUCT_READINESS_AUDIT_2026-10-17.md) and immutable all-team message `20261017T203234Z-srihith-to-all-product-readiness-audit`. No application changes. Primary snapshot `882daa2`, publication base `b44d372`; accounts for resolved frontend #241, onboarding #239 (merged after the audit draft) and Fortify/M3 #243. Fresh sweep 1035 passed / 35 skipped / one audit Git-metadata failure; affected owned-target tests 2 passed after restoring metadata. Full audit discloses local opt-in skips, CI evidence, public readiness gaps and raw RL recomputation. Next: owners add acceptance evidence on issue #244; no feature is completed by this documentation change.


## 2026-10-17T23:35Z — Sarosh: UI provenance follow-ups

PR [#282](https://github.com/adamissac/ari-ai/pull/282), `feat/sarosh/demo-provenance-labels`: dedicated real-commit/illustrative-analysis marker (#278), truthful synthetic drill/optional-Claude copy (#273 UI only), saved-live-evidence exception to sample-account copy plus historical missing red-to-green limitation and current runbook (#262). Preserve access-code gate, account framing and Sai's Defense Lab changes. Initial checks passed lint/typecheck/normal production build, 246 unit tests and four demo e2e tests; mobile visual inspection caught assessment-ID overflow, now wraps with a phone assertion. Final integrated checks pending. No live provider/backend run or raw historical artifact audit. Backend drill baseline/spend/rate-limit concerns stay on #273.

Final local verification after integrating main `b2bb5ec`: lint, typecheck, normal production build, 248 unit tests (61 files), four demo e2e tests and Markdown link checks passed. Chrome visual inspection at desktop and 375px confirmed the caveat and no document overflow after wrapping. Unit tests mock real-commit/drill dependencies; browser tests use a local test-only access code and unavailable backend, with no live provider call. See PR #282 for final CI/merge state.

## 2026-10-18T00:45Z — Srihith (cloud session): night surface on every page but the landing

Branch `claude/wizardly-mccarthy-hni97r`, pushed to `main` at the user's request. Frontend
only; no contract, backend or landing change.
- **Did:** every page without a full-screen hero stage (`.hero-full`) now carries the landing
  hero's ink + ember look. This covers the console, the Defense Lab, pricing, the demo account
  and connect.
  - Tokens flip under `:root:not(:has(.hero-full))` (globals.css, "Night surface").
  - `components/ambient.tsx` adds fixed drifting ember light, a grid and grain, with scroll
    parallax and a pointer glow on panels.
  - Motion: page titles, ink bands and project headers blur-rise in, and panels rise on
    first scroll. Primary buttons, tabs and link cards glow; `Loading` shows an ember pulse.
  - The footer gets the wordmark and tagline. Pricing gets a lit recommended plan and
    reveals.
  - `--color-night` replaces `--color-ink` wherever ink was a background: `.hero-ink`,
    `.lab-now`, `.net-map`, `.monitor-head`, and others.
- **Verified:**
  - `npm run lint`, `typecheck`, and `npm test`: 253 passed. Night contrast is asserted in
    `contrast.test.ts`.
  - `npm run build` succeeds.
  - `tests/e2e/stack.py`: 30 passed (4.8 min) against the real API, Postgres and worker. That
    run predates the `.monitor-head` one-token fix and the latest main merge (footer under
    sign-in, #289).
  - Visual check with Playwright at 1440 and 390 px: /lab, /pricing, /access, /demo/*,
    commit view, and console journey evidence. No horizontal overflow; no console errors.
- **Not done / open:**
  - No Lighthouse or axe run.
  - The monitoring page after the `.monitor-head` fix was not re-screenshotted.
- **Warnings:**
  - On night pages `--color-ink` is the light text colour. Use `--color-night` for any
    surface that must stay dark.
  - Playwright 1.64 wants Chromium 1248; the cloud image has 1194. Run e2e with
    `PLAYWRIGHT_BROWSERS_PATH` pointing at a folder whose `chromium*-1248` paths symlink
    to `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`.

### 2026-10-17 — Pricing navigation consistency

Removed Pricing CTA class and color overrides on current main. Pricing now shares navigation colors, hover styling, and HeaderContrast handling. git diff --check passed; frontend dependencies absent, so runtime checks were not run.

## 2026-10-18T01:20Z — Srihith (cloud session): replay order, commit view, page transition, calmer surfaces, Defense Lab

Branch `claude/wizardly-mccarthy-hni97r`, pushed to `main` at the user's request. Frontend only. The landing page is untouched: it is pixel-identical to `089fc77` at 1440 and 390 px, motion on and off. The only differences are timed animations and #290's nav link.

- **Did:**
  - **Demo replay** (`lib/demo/agent-map.ts`):
    - The red team (Scout, Probe) explores. The confirmed part turns red at Probe's verdict.
    - Trace and the blue team (Diagnose, Patch, Fortify) plus Verify work only on that part. Before, they landed on parts already marked clear.
    - Clean commits stop after the red team.
  - **Commit view:** opens on Overview; the code review has no dropdowns; the agent map is rebuilt calmer.
  - **Page transition:** a full-screen intro-style veil between parts of the site (`components/route-transition.tsx`).
  - **Night surfaces:** white accents instead of orange boxes.
  - **Defense Lab:** warm-ink map instead of teal. Node markers sit inside cards, so stacked nodes no longer overlap. Calmer player and story panel.
  - **Agent relay:** hairline tiles with a status dot.
  - **Landing test:** the stale Pricing assertion is aligned with #290 (main fixed the same line in #291).
- **Verified:**
  - Lint, typecheck and unit tests: 257 passed, including new tests for replay order and the transition rule.
  - Production build passes.
  - `tests/e2e/stack.py`: 27 passed and 4 failed, all at the stale `landing.spec.ts:73` assertion; after the fix the landing spec passes 5/5.
  - Playwright screenshots of the commit view, Lab, pricing and demo at 1440, 1024, 820 and 390 px. No horizontal overflow.
  - The transition timeline was checked in a real browser.
- **Open:**
  - No axe or Lighthouse run.
  - Below 1000 px the Lab map still swipes sideways inside its own frame (canvas is 700 px wide).
- **Warning:** the transition and the intro both skip under `navigator.webdriver`. Override it to see them in Playwright.

## 2026-10-18T01:55Z — Srihith (cloud session): Lab network cards, agent map colours, white sections, commit background

Pushed to `main` at the user's request. Frontend only. The landing is still untouched: pixel-identical below the header; the header differs only by #290's nav link.

- **Did:**
  - **Defense Lab map:** service cards (pattern study of Omen's network view, own implementation). Each card has a name, a status dot with plain words and a signal bar. Colours:
    - red: attacker present (ground truth);
    - green: attacker cut off;
    - yellow: false alarm;
    - amber: suspicious (the defender's view);
    - blue: defender action;
    - dashed: cut off;
    - red dashed: the attack path.
  - **Lab layout:** "who is where" is now tags on the cards, and the separate location strip and icon legend are gone. The header is open, not a box.
  - **Agent map:** a false-alarm (yellow) state, an on-node tag for the agent working there, and a colour legend.
  - **Commit page:** it no longer paints its own band under the header (that was the seam).
  - **White sections:** paper bands (`.paper`) in the Lab (intro and scoreboard) and on pricing (FAQ).
- **Verified:**
  - Lint, typecheck and unit tests: 258 passed. New tests cover the Lab colour rules and the false-alarm state.
  - Landing pixel parity re-checked.
  - Screenshots of the map at contained and false-alarm steps.
  - E2e: see the 02:30Z entry below for the current run.
- **Notes for Sai:** `node-graph.tsx` cards are `CW=196, CH=88`. `nodeTone()` holds the colour rule. Tags stack when both the attacker and the defender are on one node. The `Legend` in `lab-replay.tsx` was removed; the map header has the legend now.

## 2026-10-18T02:30Z — Srihith (cloud session): false alarms, Lab tracker, replay bars, hero glow, FAQ

Pushed to `main` at the user's request. Frontend only, no contract or backend change. Merged with #293 (Sai's hero ant colony).

- **Did:**
  - **Demo replay:** authz now has two confirmed issues (red) and one false alarm (yellow, "False alarm": suspected, checked, nothing there). Injection has one false alarm. The blue team never acts on false-alarm or checked parts.
  - **Agent-map replay bar:** it spans the agents' steps only. Before, it began at about 12% (the read/review ticks) and snapped back there when dragged to the start.
  - **Defense Lab:**
    - A tracker line under the map legend: where the attacker is (truth overlay only) and the defender's latest move with its step.
    - The blue defender tag stays on the last service it acted on, faded once that move is in the past.
    - A "Slowed traffic" legend entry, and quieter notes on healthy cards.
    - Scrubber marks and the hidden input share one geometry, so step 0 is at the far left and the last step at the far right. The time label has a fixed width.
  - **Lab scoreboard:** a plain summary, with the technical detail under "How these scores work".
  - **Landing:** no glow on "FIND THE FLAW. PROVE THE FIX." (the owner asked). The spotlight repaints only on pointer move. `HeaderContrast` skips the hit-test over the home hero. Momentum scroll and all animations are unchanged.
  - **Pricing FAQ:** a titled left column and soft rounded open rows.
- **Verified:**
  - `npm run lint` and `npm run typecheck` are clean. `npm test`: 64 files, 261 passed. `npm run build` OK.
  - Hero scroll on the production build with the colony (headless Chromium, 3 runs, 934 frames): mean frame 16.8 ms, p95 16.7 ms, 0 frames over 50 ms.
  - Before the glow and spotlight change: mean 38.2 ms, p95 116.6 ms, 246 frames over 50 ms.
  - Screenshots of both replay bars at the start and end, and of the Lab tracker at 1440 and 390 px (no horizontal scroll).
  - E2e, `tests/e2e/stack.py` (real API, Postgres, worker, `next start`, Playwright): 31 passed in 4.3 min.
  - Warning: `E2E_SKIP_BUILD=1` needs a `.next` built with `NEXT_PUBLIC_DEV_LOGIN=1`. A plain `npm run build` drops the dev sign-in form, and every signed-in test then times out at 240 s.


Homepage invitation and evaluation picker polish: responsive action cards, explicit simulation description, aligned native select. ESLint and TypeScript pass after rebuilding transport.


Sign-in refinement: removed promotional side content and centered the shared AuthStage card. Verified centers at 720px/195px in 1440px/390px viewports, no horizontal overflow, and reduced-motion animation disabled. ESLint and TypeScript pass.


Replaced game-like UI copy with evaluation terminology and synthetic/illustrative labels. Kept explicit isolated-test-network caveats and unchanged simulation contract fields. Lint, TypeScript and 13 focused lab tests pass.


### 2026-10-19 — live provider suite with real Claude (Adam's key)
- `RUN_LIVE_LLM_EVAL=1 pytest -m live security-agents/tests/test_live_provider.py`: Scout, role separation and **seven roles end to end** passed live (`asm_01M594BBKZGTTVTY770XYT4HF7`, all seven agents `provider_mode=live`, Probe and Verify executed `checks.run_template`, about $1.27 total).
- The isolated Probe test failed: its `ToolContext` had no rules of engagement, so `checks.list_templates` withheld parameter values (`MISSING_ROE`) and live Probe refused to guess, reporting inconclusive. Correct agent behaviour, wrong test fixture. The test now issues RoE with `build_roe` like the orchestrator.
- After the fix, live Probe ran `access_control.ownership@1` (route `payment_detail`, alice vs bob) in sandbox `sbx_01M594NN5VE87WXM4E7EEWF5MG`: `matches_expectation=false`, outcome `confirmed` from the executed check. 1 passed in 15 s.
- Not exercised: a production GitHub sign-in → repo connect → push → assessment on Railway (needs a signed-in user session and an authorized scope).
Removed the homepage Your next move section at owner request, including both action cards, unused Reveal import, closing-link labels and dedicated CSS.

### 2026-10-19 — Projects nav simplified to the connect drill-down (Adam)
- `frontend/app/(console)/projects/page.tsx` is now a redirect to `/connect`: the Projects
  section is organization → repositories → commits → per-commit code review and the real
  seven-agent assessment. The standalone project list, the "Connect from GitHub" strip and
  the create-project form are gone (`lib/create-project.ts` deleted with its unit test).
  A project is still created implicitly on first connect via `lib/github-connect.ts`;
  the deep `/projects/[projectId]` views (scopes, assessments, findings, monitoring…) are
  unchanged and still reachable through the assessment/scope links on the commit pages.
- `showsRecordsFooter` now covers `/connect` too — those pages only render saved records.
- E2e: `journey.spec.ts` seeds its project through `POST /projects` instead of the removed
  form; `idempotent-retry.spec.ts` exercises the same lost-response replay through a
  browser-side `POST /api/v1/projects` with a fixed `Idempotency-Key`.
- Verified: `npm run lint`, `npm run typecheck`, `npm test` (67 files, 274 tests) and
  `npm run build` all clean after a fresh `npm ci` + `frontend/transport` build. E2e not run
  here (needs the full stack harness).

## 2026-10-19T05:27Z — Adam — refresh GitHub repository picker from personal installations

- **Did:**
  - On `fix/GH-repo-list-refresh`, the repository-list API refreshes only a trusted personal GitHub App installation before reading the snapshot. It replaces rows for that installation only; organization-installation repositories remain from the sign-in snapshot. App configuration and HTTP failures fall back to stored rows.
  - Added App-JWT personal-installation lookup and paginated installation-repository listing (100 per page, at most 10 pages).
  - Added the GitHub sign-in refresh link on the connect page and repo picker. `useResource` fetches on mount and has no cross-navigation cache.
  - Added backend regression coverage for additions, removals, rejected installations/identities, failures, preserving other installations, and dev-login skipping; added frontend link tests on both surfaces.
  - Dev-login sessions are gated when the dev login setting is enabled and the stored GitHub ID is absent. The database stores the supplied dev-login name in `github_login`, so this avoids treating that unverified name as a real GitHub identity; case-insensitive login matching remains available when dev login is disabled.
- **Verified:**
  - `uv run ruff check backend` passed; `uv run ruff format --check backend`: 92 files already formatted.
  - `uv run pytest -q backend/tests -k "github or repo or auth"`: 39 passed, 106 deselected, one SQLAlchemy deprecation warning.
  - Full touched backend tests: 24 passed.
  - Frontend lint and typecheck passed; Vitest: 69 files, 277 tests passed. Vitest printed the existing jsdom canvas warning.
  - `uv run mypy` was not run: root configuration checks `shared/shared`, `monitoring/monitoring`, and `standards/standards`, not backend.
- **Not exercised:** live GitHub App requests or a visual browser check. CI was intentionally not watched after push per the task scope.



### 2026-10-19 — Defense Lab page simplified for demo visitors (Adam, `feat/adam/simplify-defense-lab`)
- `frontend/app/lab/page.tsx` rebuilt around "just the numbers": intro is one lede line plus the controlled-test-environment caveat; the three headline metrics lead; scoreboard shows one compact card per headline metric (the fourth metric, false-disruption rate, was dropped from the page); the "How these scores work" block, per-scenario metric explorer and checkpoint hash list are gone. Provenance (source path, env, run, seeds) remains under a collapsed "Where these numbers come from" footer.
- `frontend/components/lab-replay.tsx`: story panel no longer shows the "Cost" row or the "Technical details for this step" disclosure (raw action, step reward, cumulative score, reward-component bars). Map, service inspector, key moments, scrubber and the labelled ground-truth overlay are unchanged.
- `frontend/tests/e2e/honesty.spec.ts`: dropped the metric-chart/per-policy-CI assertions that exercised the removed explorer; honesty labelling, scoreboard, replay and provenance checks remain.
- Executed: `npm run lint`, `npm run typecheck`, `npm test` (68 files, 275 passed), `npm run build`, and a manual look at `/lab` on `next dev`. E2e stack not run.
- Bundle untouched: `lib/lab-plain.ts`, `lib/lab.ts` and the bundle route still ship every metric; only the page's presentation changed.

### 2026-10-19 — Adam (agent) — simplify connect drill-down and commit pages
- Did: Reduced `/connect` to organization → repository → commit selection; simplified the commit header and review layout; replaced Overview with exactly two tabs, Code review and 7 agents; placed real-run controls in the agents tab, with a compact standalone state while the quick read loads or errors.
- Commands run from `frontend/` and results:
  - `npm run lint && npm run typecheck && npx vitest run tests/unit/live-commit-analysis.test.tsx tests/unit/real-commit-provenance.test.tsx` — lint and typecheck passed; 2 files, 3 tests passed.
  - `npm test` — 68 files, 277 tests passed.
  - `npm run build` — production build passed on Next.js 16.4.2.
  - `../.venv/bin/python tests/e2e/stack.py tests/e2e/demo.spec.ts tests/e2e/brand.spec.ts tests/e2e/journey.spec.ts` — 9 passed in 44.7s. The output included `ResponseAborted` notices and one scripted-plan fallback; all selected tests passed.
- No separate manual visual check was performed. Commit and push were pending when this entry was written.

## 2026-10-19 — Defense Lab visual polish (`feat/adam/polish-defense-lab`)
- `frontend/app/lab/page.tsx`: lede + caveat moved into the hero (keeps `lab-intro`/`lab-caveat` testids); new `.lab-chips` strip summarizes the eval (held-out runs, defenders, scenarios, seeds); scoreboard section is now `.lab-console` instead of `.paper`.
- `frontend/app/globals.css`: added `.lab-caveat-dark`, `.lab-chips`/`.lab-chip`, and `.lab-console` (always-dark panel: night surface, brand radial glow, faint grid overlay) plus console-scoped overrides for headline/score elements (white numerals, glassy cards, AI row in ember→brand gradient, `.text-*` utility remaps).
- Inspired by kairo.security's dark "mission control" look (mono stat chips, dark evidence surface, severity/ember tones).
- Executed: `npm run lint`, `npm run typecheck`, `npm test` (276 passed), `npm run build`; visual check via Playwright screenshots at 1440px and 390px; honesty.spec.ts lab tests passed against `next dev` (the three auth/backend tests can't run without the real API).

### 2026-10-19 — Specialists: corpus-2 + real-corpus retraining (Adam, `feat/adam/specialists-real-corpus`)
- `specialists/data/corpus_variants.py` registers ~16 extra corpus families (5-6 per CWE) with varied (CauseClass, strategy) labels so the by-CWE lookup baseline stops saturating Diagnose/Patch. `GENERATOR_VERSION` is now corpus-2.
- `specialists/data/realdata.py` + `fetch.py`: optional real corpora in git-ignored `specialists/data/cache/` — DiverseVul (HF `bstee615/diversevul` parquets, split by commit id, out-of-space CWEs labelled `vuln_other` never safe), NVD CVE descriptions (Trace name-field rows, CWE-aligned splits), ATT&CK STIX technique descriptions (Fortify). Missing cache → builders fall back to bundled data.
- `datasets.py` merges those rows into scout/trace/fortify; `DATASET_VERSION` bumped; manifest `version` bumped to 1.1.0 (test updated).
- Retrained v1.1.0 checkpoints committed with eval: **5/7 promoted** — Scout 0.374 vs 0.099, Probe 0.631 vs 0.606, Diagnose 0.601 vs 0.525 (newly promoted), Verify 0.971 vs 0.864. Patch (0.654 vs 0.750), Trace (n=27), Fortify (n=70) stay on baselines; CI rules kept. 44 specialist tests pass; README/results tables updated with honest numbers and the corpus-2/non-comparability caveat.

### 2026-10-19 — 24/7 defense control (Adam, `feat/adam/defense-switch`)
- Added an owner-only switch on the Monitoring page and a session-aware project panel in the Defense Lab; both preserve the full settings object, and the existing offline bundle view remains independent.
- Updated Settings copy and added focused switch/sessionless-Lab tests.
- Verification: frontend lint and typecheck passed; focused tests passed (2 files, 4 tests); full frontend suite passed (70 files, 282 tests).
- No visual browser check was run.

### 2026-10-19 — 24/7 defense switch contrast (Adam, `feat/adam/defense-switch`)
- Made `DefenseSwitch` light by default for theme-token panels and retained its existing white styling behind `tone="dark"` for the Monitoring header.
- Added light/default and dark-tone class assertions.



## 2026-10-19 — Defense Lab redesign v2 (`feat/adam/lab-redesign-v2`)
- Adam feedback: the all-dark-orange console still looked "vibe coded"; wants contrast/white sections and a fresher node map (kairo.security reference).
- `frontend/app/lab/page.tsx`: scoreboard back on `.paper` (white section, which the night theme already styles light); eyebrows numbered "01 — The numbers" / "02 — Watch it in action".
- `frontend/app/globals.css`: removed the `.lab-console` block entirely; kept the score-card stacked-row fix (now scoped to `.lab-score-card`, surface-agnostic); added a light-node variant for the lab map — `.lab-replay-card`-scoped `network-card`/label/status/bar overrides (warm-white cards, ink labels, tinted tone fills) under the `:root:not(:has(.hero-full))` prefix.
- Executed: lint, typecheck, `npm run build`; Playwright screenshots at 1440px + 390px.

2026-10-19 Adam: finishing #352 against the merged Defense Lab redesign. Preserved both handoffs during the main merge; added a real-stack browser assertion for turning monitoring off/on and verifying persistence after reload. Production build passes; final lint, typecheck, unit and browser results are recorded in the PR.


2026-10-20T01:10:10.487930+00:00 Adam: landing redesign on feat/adam/landing-redesign. Replaced scroll-swapped reading sections with manual process tabs and walkthrough buttons, bounded home wheel momentum, and added the requested cinematic footer. Captured and inspected Ari desktop/mobile previews. Kairo public source and owner-supplied screenshots informed the design; connected-browser tools were unavailable, so no live Kairo capture is claimed. Validation results in PR.

Owner correction: restore the original landing hero, scroll-driven story animations and original wheel easing; remove the reading-distance cap and manual controls. Keep the new Ari-colored footer. Branch `fix/adam/restore-landing`; validation in PR.

Footer correction: use a conventional aligned footer with Ari branding/tagline on the left, two navigation columns on the right, and a separated copyright/back-to-top row. Stack branding above the link columns on mobile. Preserve Ari colors and restored hero/scroll animations. Validation recorded in PR.

Refine the landing headline with tighter two-line rhythm, a stronger cream first line and a lighter warm-ivory second line. Preserve wording, centered layout, pointer spotlight and scroll animations. Branch `fix/adam/hero-headline`; validation in PR.

Add staggered proof-card/footer entrances and subtle button, workflow-chip, process-evidence and footer-link motion. Reduce hero ants from 10–34 to 6–16. Honor reduced motion; preserve existing scroll traversal. Branch `feat/adam/landing-motion`; validation in PR.

Simplify project navigation to Code review and 7 agents. Move settings, scopes, approvals and other records into More; lead with run/replay and starting a check; disclose findings, repositories and progress on demand. Keep all routes and approval requirements. Branch `feat/adam/project-simplify`; validation in PR.

Keep organization/repository/commit navigation scoped to the selected repository and SHA. Real commit runs stay inline in a two-tab reviewer/seven-agent workspace with demo-style agent rail and recorded replay; no redirect to shared project history. Resolve the repository’s actual project, search older run pages, reject mismatched results and retain scoped remediation approvals. Optional narrated quick reads are explicitly requested and separate from executed records. Branch `fix/adam/commit-workspace`; validation in PR.

Review follow-up: retain accepted starts while history refreshes to prevent duplicate paid runs; lift newly connected repository IDs into the commit workspace; correct replay indexing; bound project lookup concurrency and tolerate unrelated project errors. Added regression coverage for these cases.


## Automatic commit assessments — 2026-10-21

IMPLEMENTED in [PR #377](https://github.com/adamissac/ari-ai/pull/377), pending merge/deployment verification: signed pushes fan out into durable per-commit jobs; GitHub API author allowlist, exact SHA, duplicate protection, no docs-only skip/coalescing loss, scope-approval resume. Railway defaults to `adamissac`; configurable for other accounts. Full runs use the normal project assessment budget (operator-capped), not the deliberately cheaper legacy monitoring budget. Shared contracts unchanged. Full-chain fixture tests use scripted model responses, not live provider evidence. Validation: 181 local backend/monitoring/security tests passed (7 optional/dependency skips); 12 final focused scheduling tests passed. Full-chain fixture covers reviewer and all seven roles with required repair approvals. All GitHub checks passed on cbc0782; checks rerun after integrating newer main documentation/RL changes. Production readiness is reachable; authenticated production worker/run verification remains outstanding.
### 2026-10-20 — Alan / Codex — continue Claude's RL v8 review

Isolated `/private/tmp/rl-v8-continuation`, branch `feat/alan/rl-v8-continuation`,
base `45f71eca`. Read Alan's v8 evidence and PR #370's unresolved reviews. V8's
historical pass compares to a fresh simulator baseline, not incumbent v6; retained
results show a tie versus v6 overall and a per-scenario regression. No holdout rerun,
training or promotion. Original dirty checkout and Claude worktrees preserved.

Implemented current-incumbent digest binding at promotion, report hash pinning,
nonempty passing-check validation, `online --registry PATH`, and reference-name alias
validation. Old unpinned registry entries require explicit re-registration. Historical
packets are preserved. Authenticated approval, signed/schema-complete reports and
concurrent registry updates remain outside this local hardening; issue #317 remains open.
Next experiment: preregister validation coverage of quiet traffic with a fresh holdout,
using the actual incumbent as comparator. Do not promote v8 from its historical packet.

Validation results are recorded in the continuation PR and coordination message.


Final verification for PR #375 (`f9962eb`): from `rl-defense/`, run the existing
v8 worktree's `.venv/bin/python -m pytest -q` with
`PYTHONPATH=.:../shared:../standards:../examples/payments-lite/target` pointing at
this checkout. **228 passed in 240.68s**, log `/private/tmp/rl-v8-final.log`.
Focused runs: 35 passed; final retained-evidence/pipeline checks 15 passed.
The first broad run collected an obsolete pre-edit test (226 pass/1 fail); the
clean committed-tree rerun above supersedes it. Ruff check and Python format
checks pass. PR: https://github.com/adamissac/ari-ai/pull/375 (review, no merge).

### 2026-10-20 — V9 preregistration and execution

Owner said "Do it" after the proposed next experiment. Defined quiet-validation extension,
fresh disjoint holdout, five seeds0–4×100 evaluation episodes, actual registered v6 as
baseline. Pipeline adds opt-in random-valid baseline/raw trajectory retention; aggregation
uses five seed means and refuses incomplete/mismatched cohorts. ADR-0018 proposes lab
protocol extension; no simulator TTC is invented. Pre-run env/splits/online smoke31 passed.
Source/config/preregistration must be pushed before launch; see results/live-retrain-v9.

### 2026-10-20 — V9 evidence tooling prepared during the fixed CPU run

Separate reporting worktree `/private/tmp/rl-v9-reporting`, branch
`feat/alan/v9-reporting`, based on29aa5f8. The actual training checkout stays on29aa5f8;
learning modules/configs are unchanged. Added portable finalization/read-only hash
verification, secondary threshold checks, isolated scientific plotting helpers and
comparability guards for budgets/source/reference hashes. Targeted20 passed; earlier
reporting/pipeline22 passed; lint/format clean. Fixture tests execute no held-out v9
policy. Merge reporting into the experiment only after all five runs finish, then
report/plot/finalize saved artifacts per POSTPROCESSING.md. No promotion.

### 2026-10-21 — V9 completed, no promotion

All five frozen-source (`29aa5f8`) runs completed in 3h 7m 12s with no execution errors.
16,500 held-out episodes retained across 11 policies. Primary hypothesis unsupported:
return gain vs actual v6 −0.0458, 95% training-seed CI [−0.1758, +0.0843]. Both quiet
scenario means exceed v8, but mixed rotation regresses and all five gates reject.
Compromise fraction improves while availability and quiet disruption worsen.
Selected online steps: 105k,150k,75k,120k,0; seed4 selects demo-pretrained weights.
Exact checkpoints, compressed full experience/trajectories, all metrics, worst paired
failure, train-only mapping/behavior checks, runtime/registry snapshots and figure are
retained in `rl-defense/results/live-retrain-v9`. Reporting integrates only after jobs
finish; no held-out rerun/tuning. Registry v6 unchanged. PR #376 awaits review, stacked
on incumbent binding PR #375. Next experiments need fresh preregistration/holdout and
should address mixed rotation/persistent attacks; this holdout is now exposed.

V9 final validation: core frozen-source RL suite 237 passed; reporting/portability suite
20 passed in1.91s; Ruff and diff checks pass; 76 stored artifacts plus all five compressed
evidence indices verified, checkpoint source/optimizer lineage verified, registry unchanged.

### 2026-10-21 — V9 pre-merge review corrections

Addressed five PR #376 review findings: require source/teacher-budget/expert/rule
cohort provenance and complete holdout scenario sets; index trajectories at initial
export; pin fixed v7/v8 bytes in launcher/reporter. Historical reports remain unchanged;
explicit saved-command budgets reproduce the original summary. Focused31 tests pass.
No retraining, holdout replay or promotion. Owner authorized merging #375/#376 and
subsequent v10 failure analysis, regressions and preregistration.

### 2026-10-20 — interface polish

Polish the shared ARI interface using Kairo’s restrained grids and technical typography in ARI’s warm charcoal/ember palette. Add consistent workspace headers, numbered organization cards, clearer repository rows, quieter panels and a precise replay frame. Preserve the original hero and scroll choreography; keep Projects highlighted throughout the organization/repository/commit flow. Branch `feat/adam/ui-polish`; validation and screenshots recorded in PR.

### 2026-10-21 — V10 failure analysis, regression and preregistration

Owner authorized all three follow-ups and merging #375/#376. Branch
`feat/alan/v10-demo-balance`: saved-data analysis confirms ~87% noop teacher rows;
seed0 chooses noop with higher Q than valid block actions in20 of23 compromised
windows (current masks reconstructed from preceding transitions). No causal training
mechanism or serving bug is established. Implemented opt-in action-balanced demo
sampling with unchanged default/agent replay. Added sampler, tiny real training and
paired-arm/split regression tests. Fresh val_v10 and holdout_v10 plus controlled
two-arm/five-seed preregistration/runner are prepared. No v10 training or holdout run.
V6 stays deployed; a future study needs ~6h CPU with five workers.

Additional #376 review: expose explicit legacy teacher budget on aggregation CLI,
require a clean concrete source commit before launch and validate actual pooled/scenario
episode counts. Historical CLI re-aggregation and final evidence verification are
executed before publishing. No held-out replay or learning changes.

V10 review follow-up (#378): normalize only configured OnlineConfig settings across
arms (not treatment/selected outputs), fingerprint tracked source/config bytes before
and after every job, and complete both-arm train-only checks/worst-trace extraction
and stored evidence verification before reporting success. Synthetic postprocessing
and uncommitted-source-edit regressions added; integrated58 tests passed. No v10
training/evaluation has run. #375 merged at64c38b7; #376 awaits final CI.

#376 merged atce723f8 with267 RL tests passing; four new review findings arrived
during merge and are addressed in #378: shared tracked-source fingerprint per job,
source SHA in aggregate output, complete/consistent seed gates, and transactional
finalization with validation-before-write and I/O rollback tests. Historical numeric
results/weights remain unchanged. #378 still contains the unexecuted v10 study.

### 2026-10-21 — Authorized RL continuation complete

Merged #375 (`64c38b7`), #376 (`ce723f8`) and #378 (`fdc8e45`) after their required
checks passed. #378's final RL suite:282 passed in829.86s plus successful20k-step
training/checkpoint-reload smoke; backend, RL integration, frontend unit/browser,
links and secret scanning all passed. Local focused follow-up64 passed;78 stored
v9 artifacts and all five compressed indices verify. All reviewed findings, including
the four late #376 findings corrected in #378, are addressed and resolved.

Implemented saved-v9 failure analysis, opt-in balanced demonstration action sampling
with default/agent replay unchanged, regression tests and fresh controlled v10
preregistration/runner. V10 full study has NOT launched; its effectiveness remains an
untested hypothesis. Original v9 reports, checkpoints and numeric results are preserved.
The actual v6 registry remains unchanged (digest
`c345593354856c50559ba16611853a0e4936a7efc11fa5704c224d3189271408`).

Next technical action is the preregistered v10 uniform-versus-balanced comparison:
five fixed seeds/arm, about6h CPU with five workers and33,000 held-out episodes.
Use committed/pushed clean source and the real existing v6 registry with
`results/live-retrain-v10/run_study.py --registry PATH --workers 5` from `rl-defense`
and its documented workspace PYTHONPATH. Source-byte/reference/registry guards,
complete primary/secondary reporting and failure evidence are implemented; no
automatic model choice, approval or promotion. This requested task ends at preparation.


### Automatic/manual mode and Haiku-only follow-up (#377 / #380)

Implemented per-repository persisted ON/OFF, owner-only API, migration 0009 and proposed additive schema 0.15.0; manual Run shown when OFF. Added signed-sender gate and per-project start quotas that defer every excess commit durably. Production/staging gateway, Railway startup and quick-read enforce Haiku 5.5. Historical evidence labels preserved. Contract review requested from Alan; full test/CI and deployment verification remain pending.
### 2026-10-21 — V10 execution authorized

IN_PROGRESS on `feat/alan/live-retrain-v10`: owner requested continuation after
merged preregistration. Launch the unchanged two-arm/five-seed study with five CPU
workers, fresh validation/holdout, actual read-only v6 registry and all ten runs
retained. Expected compute roughly six hours. Freeze launch commit and tracked
source until parent runner exits; no automatic retries, policy selection or promotion.
Prior source validation: merged #378 CI,282 RL tests and20k-step reload smoke passed.
This entry records authorization/preparation, not a successful experiment.

### 2026-10-21 — RL-V10-001 completed, no promotion

Completed uniform/balanced seeds0–4 from frozen source `a98899d` in5h58m17s
(before final diagnostics), no execution errors;33,000 held-out episodes retained.
Balanced-minus-uniform return +0.05919 (95% training-seed CI +0.03335 to +0.08713;
Holm p0.0895); balanced-minus-v6 +0.29080 (CI +0.27056 to +0.31045). Primary
hypothesis unsupported: all ten candidates reject; every balanced seed has a scenario
regression, four fail availability, three fail static-rules comparison. Compromise
improves but availability/quiet disruption worsen. Train-only diagnostics pass for
all ten; all saved evidence verifies and checkpoints reload. Worst saved failure:
uniform seed2 persistent43203,84/84 compromised vs v6 0/84. No holdout reruns,
retries or promotion. Real registry digest remains c3455933…; v6 stays deployed.
Results/provenance: `rl-defense/results/live-retrain-v10/PROVENANCE.md`.
Future experiments need fresh preregistration/holdout; no follow-up run launched.


### 2026-10-21 — automatic commit review fixes (#377)

Finalized deferred requests as skipped when repository/rollout policy disables them; added regression coverage. Updated operator docs and ADR-0015 to the explicit Haiku-only production policy. Latest focused scheduling/migration verification: 26 passed; Ruff passed. Prior frontend verification: 305 tests, TypeScript, lint, production build and two real-stack browser checks passed. Integrating latest main; final CI and Alan contract approval remain required. No authenticated live-provider run has been verified.

### 2026-10-21 — RL-DIAG-001: targeted v10 diagnosis (Alan, owner authorized)

IN_PROGRESS `feat/alan/rl-targeted-diagnosis`. All ten selected checkpoints already
miss85% validation availability; rotation exposes principal-block collateral cost,
not token-rotation bypass. Added saved-data/input-pinned diagnostics and opt-in
`--require-validation-availability` rejection before any holdout policy execution.
Targeted gateway regressions preserve own/delegated access assertions and prove the
current broad-control tradeoff. No new training/holdout replay or promotion. V6 stays
deployed. No v11 launch: process guard is not proof of a better learned policy.

Focused verification: 22 tests passed in 22.51s, including the unchanged complete v10
evidence bundle. Ruff lint/format and diff checks pass. No new policy execution or
training performed by this diagnosis. Full CI and review pending.

PR #383 review follow-up: refuse stale output/evidence directories before training,
bind validation diagnostics to checkpoint/seed/source/config/manifest provenance, and
atomically export verified/restorable rejection evidence before exiting. No synthetic
promotion report; original run files remain intact. Added stale-directory, durable
restore, corrupt-input rollback and complete rejection-path regression tests.

### 2026-10-21 — RL-DIAG-001 complete; pause further training

Merged PR #383 at `79a21e8` (final source `d9ad43b`). Diagnosed exposed v10 data
without policy replay: all ten selected models already miss validation availability;
principal-wide long controls impose documented own/delegated-traffic costs during
rotation. Added opt-in validation screening, candidate/source/config provenance, empty
output preflight and atomic verified/restorable rejection export. All three automated
review findings are fixed and resolved. Existing trainer defaults, serving and v10
evidence are preserved.

Final CI: **310 RL tests passed in 389.33s**, plus successful 20k-step gradient/reload
smoke, full backend suite, backend-to-RL integration, frontend unit/build and browser
checks, link checks and secret scan. Local focused verification: **28 passed in 22.73s**.
No full research training, fresh/exposed holdout replay, registration or promotion.
Registry SHA-256 remains `c345593354856c50559ba16611853a0e4936a7efc11fa5704c224d3189271408`;
v6 remains deployed. The earlier v10 study monitor remains paused.

Owner requested at most one justified follow-up experiment. **No v11 launched**: the
process guard is proven, but no learned-policy improvement is established. Next research
requires a specific objective/control or validation-selection intervention with
train/validation proof, then at most one preregistered fresh-holdout experiment. If it
fails, reconsider the approach before spending more CPU. Diagnosis and preserved paired
traces: `rl-defense/results/v10-followup-diagnosis/README.md` and `diagnostics.json`.

### 2026-10-21 — RL-DURATION-001 (Alan, owner authorized)

IN_PROGRESS `feat/alan/rl-duration-study`. Bounded train/validation-only comparison of
short/long controls and one fixed existing outsider-duration rule, with actual v6 and
fixed tuned-rules references. Reuse existing implementation; no new control/action,
serving fallback, learned policy or holdout execution. Plan frozen before execution in
`rl-defense/results/duration-screen/PLAN.md`. Only prepare one possible v11 study if
validation improves availability without worse compromise under the fixed screen.

### 2026-10-21 — RL-DURATION-001 screen complete; v11 not justified

Source `d985e69`, fixed/pushed plan before execution; all five heuristic/reference
policies completed 280 train and240 validation episodes,15,600 windows, no errors or
retries. Duration-aware vs actual v6 on validation: availability +1.2373 percentage
points,95% CI [−0.0313,+2.5340]; compromise −4.1464 points, CI [−7.4197,−1.1465].
Availability CI includes zero, failing the precommitted strict screen. Other validation
conditions pass, but training compromise also worsens vs v6 (16.3863% vs15.2552%).
Do not relax the test, add samples/candidates or prepare/launch v11. Reconsider the
objective/control design first. This is a deterministic heuristic, not trained RL.

All520 saved metrics exactly reproduce from complete raw trajectories; initial35
artifact pins and frozen source/actual registry verify. Expanded evidence additionally
pins post-run report/verifier. 13 focused tests passed in0.97s; Ruff lint/format and
diff checks pass. No holdout replay, new checkpoint, registration, serving change or
promotion. V6 registry unchanged. Results in `rl-defense/results/duration-screen`.
PR #386 holds the complete source/results; final review and CI pending.

### Duration screen review correction — historical interpretation

PR #386's two review findings are addressed locally: both commands now reproduce
training and training-by-scenario aggregates, not only validation; raw verification
uses hash-pinned historical metric/schema/decision definitions from frozen source
`d985e69`, independently of current product imports. Exact prior execution/verifier
source and the earlier index are retained for audit; original measurements, plan,
traces, registry and failed screen decision are unchanged. Recorded Python/NumPy/
Pydantic versions are required. 17 focused tests pass in3.91s, including corrupt/re-pinned
training summaries and incompatible current-product imports. No policy replay or new
candidate/training. Shell permission approval timed out twice; GitHub connector is
used to publish the authorized changes. The temp worktree Git metadata may remain
at its earlier head; modified local files are preserved and match the remote fix.

### 2026-10-21 — RL-DURATION-001 complete; no v11

Merged PR #386 at `9926d14` (final review-fix commit `1e42f3e`). The one fixed
heuristic duration comparison completed all520 train/validation episodes and15,600
windows from source `d985e69`, without errors, retries, holdout execution or model
training. Validation availability +1.2373 percentage points versus v6 had95% CI
[−0.0313,+2.5340], failing the precommitted strictly-positive lower bound. Compromise
improved on validation but worsened on training. Do not relax the condition, add
samples/candidates, or prepare/launch v11 from this screen. Reconsider objective/control
design before further research. V6 and its original registry remain unchanged.

Both review findings are fixed and resolved: complete training/validation aggregate
reproduction, plus hash-pinned historical metric/schema/decision semantics and recorded
Python/NumPy/Pydantic versions. Original execution/verifier sources and prior index are
retained; all original measurements, raw traces, plan and decision are unchanged.
Final CI: **327 RL tests passed in394.40s**, plus successful20k-step gradient/reload
smoke, frontend unit/build and browser tests, links and secret scanning. Local focused
verification17 passed (latest2.32s); all520 raw episode metrics reproduce. Full result:
`rl-defense/results/duration-screen/PROVENANCE.md`; historical verification details:
`rl-defense/results/duration-screen/VERIFICATION.md`.

Shell permission review timed out; changes were committed/pushed/merged through the
GitHub connector. The temporary checkout `/private/tmp/rl-v10` still has earlier local
Git metadata and preserved files corresponding to remote fix `1e42f3e`; reconcile it
with the remote before any cleanup or new work. The user's original dirty checkout
was not overwritten. No study heartbeat was re-enabled or extra experiment scheduled.

### 2026-10-22 — RL-CMDP-001 Phase 1 foundation

Owner supplied explicit Phase 1 scope; no training/Phase 2 or serving changes.
Dedicated independent checkout `/private/tmp/rl-cmdp-phase1`, branch
`feat/alan/constrained-rl-phase1` from main `198eb3b`. Primary checkout has unrelated
Devin UI edits/QA outputs and is untouched. Existing duration evidence is preserved.
New internal research adapter reuses shared EvidenceRef; no public contract change.
Initial18 tests passed; extended20 tests passed in9.53s. Freeze/publish fixed bounded
baseline plan before execution. Shell network is unavailable; GitHub connector
publishes equivalent source trees. Record both local and published commit identities.

### RL-CMDP-001 — completed Phase1 measurements and decision

Published frozen execution source `9d6460487275d25cc31194d7b603196d436c4485`, exact tree
`22c2f7f50c36e5fe5b7eb1d26c41b1b314438e4a` (local equivalent `43b7110`).140owned-lab
baseline episodes /4,200scored windows completed; raw request→outcome→reward/cost→all
pooled/per-scenario summary verification passes. All policies share an explicit
capability shield; v6_shielded is not the original policy selection semantics.
Cost-aware<=2short actions:99.504% benign availability but93.156% attack compromise.
This is an unsuccessful defense, not evidence that a constrained neural policy works.

Read-only legacy adaptation:280duration-screen train episodes /8,400transitions;
all aggregate costs measurable,280initial current masks unknown, all propensities and
delegated-denial attributions unknown. Preserve every historical artifact unchanged;
do not pool overlapping train seeds. Full audit/CMDP/action matrix/readiness/four-paper
architecture comparison is in docs/research/constrained-rl-phase1; ADR0019 raises the
stale ADR0008/0014 vs implemented-lab discrepancy without silently ratifying it.

**No-go now for constrained DDQN or discrete CQL.** Conditional preference: cost-aware
DDQN after feasible controls, reviewed data/state/support and precommitted cost budgets.
No Phase2 code, training study, holdout run, new action, promotion or serving change.
Actual v6 registry before/after SHA256 stays c345593354856c50559ba16611853a0e4936a7efc11fa5704c224d3189271408.

Validation so far:20focused tests passed in9.53s, Ruff checks/format all87RL source/test
files pass, links518files/0problems. First full compatibility command had346passes and
one subprocess import failure because relative PYTHONPATH did not survive cwd change;
rerun uses absolute paths. Final full suite and PR review/CI pending at this entry.

Phase1 compatibility rerun: **347passed in256.62s**, absolute PYTHONPATH. Full87-file
RL lint/format and links518files/0problems pass. PR #388 published (head `ed74a11c`).
CI secret scan found three false positives: SHA256 entries for api.py/owasp_api.py in
the pinned code manifest. Added a path+exact-line+exact-value AND allowlist, not a file
exclusion; local gitleaks8.30.1 history scan passes. A temporary Git fixture proves the
manifest hashes pass while a synthetic credential-shaped api_key in that **same file**
still fails. New regression checks the exception's scope and historical pins. Original
execution evidence/source semantics remain unchanged; no result recollection/rewrite.

### RL-CMDP-001 — Phase1 review corrections; no merge/deployment

PR388 remains deliberately on the dedicated research branch: latest owner Phase1
request forbids production deployments; root vercel.json enables main deployments.
This takes precedence over earlier general merge permission. No Phase2 work.

Automated review's four P2s are addressed: resolve actual published commit/tree;
portable always-present champion fixture plus canonical-registry absence/byte checks;
verify every baseline action against actual pinned policy inputs on saved observations;
reject unknown index/config schema versions.26focused tests pass in10.31s. Stronger
verifier reproduces all140episodes /4,200transitions from the original artifacts,
without new lab episodes or altering a measurement. No hypothesis or decision changed.
The exact original evaluator and cryptographically verified original Git commit object
are retained; collection/adaptation ASTs and all original reward/cost/policy/environment
source hashes must remain identical. New verification source is separately identified.

Main source before fixes `184a61a`: all five workflows green (RL, backend incl integration,
frontend/browser, docs, secrets). New fix CI will run on publication. Full local suite
before added regressions347passed; shared60passed/1tsc skip then omitted test1passed
separately with installed tsc. Full commands/limitations: Phase1 VERIFICATION.md.
Research/checkpoint/registry guard unchanged. Stop here after review/CI; leave PR open.

Historical compatibility exception additionally checks exact separately recorded
post-review evaluator/proof hashes, so a future orchestration/import edit cannot
silently reinterpret this run. Added drift regression;27focused tests pass in9.91s.
Original scientific artifacts, execution/report indices, checkpoint and registry remain
unchanged. Source tree/content published through connector; local branch metadata is
an equivalent independent commit chain. Primary checkout and old experiment worktrees
remain untouched. No merge: Phase1's deployment restriction governs this research PR.

### 2026-10-23 — Alan / Codex — RL-CMDP-002 preregistration
- Owner authorized deterministic feasibility and explicitly selected zero successful unauthorized reads and zero valid owner/delegated denials in every case.
- Added an isolated exact-resource guard, independent negative/positive tests and a fixed 18-case / three-policy / 54-request HTTP protocol. No training, holdout or serving integration.
- Before comparison: 21 guard tests passed; fixed-policy harness test added. Comparison must execute once from committed protocol/source and retain all outcomes; no automatic retry or tuning.
- Parallel PR #393 owns operational controls; this research fixture does not duplicate or import that lifecycle.

### 2026-10-23 — Alan / Codex — RL-CMDP-002 result
- Preregistered protocol/source committed and pushed before the single 54-request comparison. Exact-resource guard passed zero unauthorized / zero legitimate-denial owner limits; six unknown cases all abstained without forwarding.
- Unguarded allowed seven unauthorized reads; blanket denial denied two owner and three delegate reads. All rows and source/protocol/runtime identities retained in `rl-defense/results/deterministic-feasibility-001/`. No retries, training, historical replay or promotion.
- Focused guard/harness tests: 22 passed. Lint and 533 Markdown files checked. This is one authored synthetic HTTP resource, not real-application context integration or production evidence.
- Next: independently verify application-context extraction, freshness, request binding and revocation races. RL remains no-go.

### 2026-10-23 — Alan / Codex — RL-CMDP-002 review correction
- Added a post-response compute-limit check and a regression that overruns on request 54; retains all 54 partial rows in FAILURE.json without retry or successful result.
- Original comparison evidence remains byte-identical. Its runner lacked the final post-response clock check and elapsed timing record; strict budget compliance is therefore not certifiable from those original artifacts. Numerical finite-case results are retained, not replaced.
- Recorded execution commit is a direct ancestor of this branch; merge-commit method preserves it durably.

### 2026-10-23 — Alan / Codex — RL-CMDP-003
- Added an opt-in owned payments-lite read wrapper reusing the research guard. Private store supplies identity/ownership; trusted fixture updates share a request lock. No HTTP context or writes can authorize access.
- Eight real-app integration tests pass: before/after BOLA, owner/delegated reads, resource/path/header binding, token/owner/grant updates and expiry/rollback races with buffered bodies.
- No earlier study rerun, evidence rewrite, serving hook, training or promotion. Single-process private-store assumptions are explicit; full feasibility remains inconclusive.

### 2026-10-23 — Alan / Codex — RL-CMDP-004
- Reproduced a response-binding gap: authorized Alice pay_a1 request could release a downstream Bob pay_b1 response with HTTP 200.
- Added strict existing PaymentOut schema/identity checks, duplicate-key and framing rejection, bounded message capture, fresh output headers and a post-validation freshness check. Customer key/record identity contradictions abstain.
- Twenty real-app/controlled-downstream integration tests passed; lint passed. Private-store and single-worker assumptions remain. No prior comparison rerun, source evidence rewrite, shared contract, serving hook, training or promotion.

### 2026-10-23 — Alan / Codex — RL-CMDP-005
- Added read-only original-fixture evidence audit: fixed trust anchor, execution Git object/protocol, 54 ordered units and independently recomputed summaries. Canonical EvidenceRef; current auditor identity is separate from unrecorded original interpreter/timing.
- Eighteen focused tests passed. Supported CLI runs from private read-only captured-commit source. Readiness remains inconclusive; training/promotion flags false.
- No collector/app execution, comparison rerun, modified evidence, new fact authority or serving hook. Real payment authorization producer/transaction semantics still need a reviewed contract proposal.

## 2026-10-23 — RL-CMDP-006 authorization provider

Branch `feat/alan/authorization-context-provider`. User designated payments-lite
trusted records and existing lab delegation records as research authority; no
external customer system exists. Documented version 0.1.0 interface first, then
extracted trusted lookup behind internal AuthorizationContextProvider and reused
the deterministic kernel. Owner/delegated/unauthorized/unknown classifications;
fixture clock domain and serialized revision metadata; no retained credentials.
The wrapper still holds its lock through release and checks freshness after
downstream execution/serialization. No external writer guarantees are claimed.

57 focused provider/kernel/owned-app tests pass (1.65s). Existing historical
evidence is untouched. No RL observation wiring/training or ControlGrant changes.
Future causal input requirements in docs/research/authorization-context/README.md:
completed observed windows, context available before cutoff, unknown coverage,
no hidden labels, future requests or later permission backfill. Cross-system
publication remains proposed and requires shared models/codegen/consumer review.
Review and CI pending.

## 2026-10-23 — RL-CMDP-007 adaptive-defense research readiness

Owner supplied the next-phase scope after #414. Branch
`research/rl-adaptive-defense-readiness`, audited base `3d540d90`; concurrent main
#421 inspected before publication. New docs/research/adaptive-defense-readiness
covers all requested formulation, feature-level coverage, actions/costs/baselines,
blocked preregistration and formal NO-GO. Implemented pure prefix-only provider
snapshot observation adapter (32 values +32 known bits +5 feasibility bits), noop-only
hard mask/rejection, separate descriptive cost outputs with unmeasurable reward null,
and hash-anchored fixed train-only inventory. No trainable environment: measured
residual benefit/dynamics unsupported after deterministic authorization. No new
training, experiment collection, checkpoint, promotion, customer control adapter or
historical artifact changes. Do not launch a study from the draft preregistration.

54 focused adapter/provider/owned-app tests passed in1.35s; links572 files/0 problems.
Read-only inventory: Phase1 140 episodes/4200 transitions, all current masks but zero
recorded behavior probabilities; duration train280 episodes/8400 transitions with
280 missing initial current masks. No retrospective candidate facts reconstructed.
Review/full CI and broader regression/type checks pending. Next owner decision is
whether to fund a separately scoped residual-task instrumentation/efficacy design,
then approve numerical constraints/protocol before any collection or training.

RL-CMDP-007 publication update: PR #422, tested code6c833795. Late arrivals enter
their knowledge-arrival window without rewriting earlier observations. Local166
research regressions passed in19.80s;91 shared contract tests including TypeScript
passed in0.96s; two new modules pass scoped strict mypy. NO-GO/Option C unchanged.
Exact commands and the pre-commit provenance rejection are retained in
docs/research/adaptive-defense-readiness/VERIFICATION.md. Final CI/review/merge state
is recorded on PR422; no new study may start without separate owner approval.

## Resource-exhaustion research foundation — RL-CMDP-008

Explicit owner attachment authorizes finite-worker/queue research implementation,
baselines and bounded unit instrumentation; no formal comparison before sample,
compute and safety approval, and no model training. Branch
research/rl-resource-exhaustion-feasibility from7c127a2b. Core service/Gym model,
causal observations, route/source quotas and deterministic baseline rules implemented.
23 core instrumentation tests pass. Stage2 infrastructure, docs and final checks
remain in progress. No empirical study or customer enforcement was run/modified.

RL-CMDP-008 Stage2 preparation implemented: versioned experience/config/profile/
cutoff schema, separate reward/cost arrays, bounded unit-only replay, fixed unit
receipt writer/verifier with raw work/control/cost accounting, failure retention,
and deadline-bounded settlement. No optimizer, checkpoint writer or study runner.
Stochastic private phases/tie ordering avoid clock or label-priority shortcuts.
Proposed study now600 episodes/18,000 decisions plus bounded settlement, with
unapproved safety/sample/compute values. Training NO-GO pending approved formal
feasibility, safe baseline headroom, terminal-cost collector/learner review and
explicit training authorization. No historical evidence or customer control edits.

Resource evidence closure: anchored four-window unit receipt committed from source
1b5516c808602c91152c18b7460fe9aaa9cd8404. Local checks: 46 resource tests,
213 broader selected research tests, 91 shared tests, scoped strict mypy (7 files),
Ruff/format and 580 Markdown link checks passed. Formal feasibility and training
remain unapproved; PR review/CI required before merge. See resource VERIFICATION.md.

## Resource feasibility001 — owner-approved execution preparation

Owner approved the previously proposed600 episodes/18,000 decisions, safety limits,
2000 paired whole-seed bootstrap draws and one-process512MiB/600-second budget.
Acceptance failures continue and remain reported/ineligible; integrity failures stop.
Persistent-static remains240 active ticks, explicitly ineligible at64-tick cap.
Environment/generator/baseline source unchanged. New isolated study collector pins
source/config/runtime, retains gzip raw episode traces and stops without retries.
50 resource/study unit tests pass; strict mypy and Ruff pass for the runner.
No formal outcome has been observed at this preregistration commit.

Feasibility001 completed once atbed44ff27897225f3194c6afd118eee35f7516d2:
600 episodes/18,000 decisions,9.178132s,79,462,400-byte peak RSS. All30 family/policy
results retained. No eligible policy in any attack family; persistent-static
240 active ticks explicitly violates64. Training NO-GO, no further execution.
Sealed index931fbd6737f963a1fe661ab029d08b7f4a26fe09e42805c291c9d1a1a2a0246c;
read-only600-episode audit passed. Report/evidence under resource-exhaustion/feasibility-001.

PR433 first RL CI:544 passed,1 fault-injection fixture failed because prior torch
tests raised process-lifetime peak RSS above the study limit. Test fixture now
isolates RSS for the invalid-action fault and separately tests memory-limit
rejection. No collector/protocol/evidence changes, no formal study rerun.

## RL-CMDP-010 read-only research/product assessment

Owner requested mathematical capacity feasibility, oracle limitations, control
expressiveness and product-thesis decision after433. New stdlib-only analytical
tool reads sealed archived requests, computes necessary interval-completion bounds
and relaxed offered-work actuation coverage. It does not import the service/env,
generate/replay workloads, execute a policy, train or modify prior artifacts.
Methodology frozen before calculation; exact existing-action optimum remains
uncomputed unless a rigorous bound resolves it. Do not equate rule failure with
fundamental infeasibility or optimistic upper bounds with achievable performance.

Bounds assessment completed on120 existing workloads (source0074562,0.7621s,
recorded peak RSS30,900,224 bytes). No completion upper bound below90%, no feasible
oracle/schedule certificate. Existing-action optimum unresolved. Primary product
recommendation C:deprioritize RL; non-RL assurance evidence packet proposed only.
Research, training permission and pilot readiness remain separate. See
docs/research/rl-product-thesis/README.md; all original study bytes unchanged.

## Phase7B / RL-CMDP-011 research-only first responder

Owner confirms Grok's proposed feat/live-defense-first-responder may not be pushed;
continue independently, use existing shared contracts, do not change production
schemas/incidents/agent APIs/workers. Fixture-only adapter and strict proposed
record models reuse canonical SnapshotRef/EvidenceRef/AgentRun/AgentStepResult/
AuthorizedScope. No dispatcher/environment/reward/collector/trainer. Default masks
allow only local abstain/monitor; hypothetical fixture agreement cannot authorize
execution.15 tests pass; source inventory and full research docs pending. See
first-responder INTEGRATION_PROPOSAL and immutable message to Grok/shared owner.


### Phase 7B specification and invariant verification

Research report: docs/research/first-responder/README.md. CONDITIONAL GO for
research; no learning yet. Grok's actual contract remains pending exact reference.
Seven-agent source inventory pins 71118928866d6974ba0ec9532420a9fc71187318.
23 fixture/provenance tests pass; scoped Ruff and strict adapter mypy pass.
No collection, agent calls, training, checkpoint promotion or enforcement.
Remaining: broader regressions, PR review/CI and authorized research-only merge.


### Phase 7B research PR #437 — specification delivered

Report: docs/research/first-responder/README.md. CONDITIONAL GO, no learning yet.
CI at 157be64: 569 RL tests plus existing 20k smoke/reload; 724 backend /14 skips;
4 integration; docs/secrets pass. Local 242 research and 91 shared tests pass.
CodeRabbit's three documentation findings addressed; adapter code unchanged.
Grok integration remains fixture-only pending exact reference and contract review.
No collection, first-responder training, promotion or enforcement follows merge.
Remaining merge gate: final documentation head review/CI. Future numerical protocol
and any collection require separate explicit owner approval. Historical source
ancestry retained and v6 registry hash unchanged (see verification report).

## 2026-10-24 — Devin (Adam): agent-visibility UI + prod automation enabled

- Prod: enabled `automatic_assessments` on adamissac/mypypath (ari-ai already on), enabled `monitoring_policy` on the `derv` project, approved mypypath's sandbox scope (active to 2026-11-18), stamped delivery authorization on both projects. Push→agents chain verified live: monitoring assessments COMPLETED within ~1-2 min of each push.
- Branch `feat/adam/agent-visibility-not-needed`: relay/team list now label skipped agents 'Not needed' (with the recorded skip note) or 'Not reached' on failed/cancelled runs. `earlyEndNote` in `frontend/lib/relay.ts` reads the reason from persisted `state_changed` events.
- Next: expand `security-agents/evals/commits/cases.json` toward ~40 cases, add check templates to lift `vuln validated` off 0/7 (only ownership + bounds templates exist).

## 2026-10-24 — Devin (Adam): specialist eval-brain + live eval harness

- `specialists/data/evalcommits.py`: eval cases become minimal pairs (planted edit = CWE/vuln_other positive, original = hard negative), splits pinned by case id; `scout()` merges the 60 rows.
- Scout retrained and **promoted**: macro_f1 0.349 vs best deterministic baseline 0.084 (static_analyze_patterns). Checkpoint + `results/scout.json`/REPORT.md committed. Other specialists have no field-compatible eval rows — their tuning signal is the live eval scores, not training data.
- Eval harness: `run.py` cleanup survives missing `gh` auth; `score_api.py` accepts the bare-list assessments response. All 35 cases pushed as `eval/20261024-110115/*` branches; assessments accumulate live pending a fresh `hackatl_session` for scoring + scope widening (`access_control.authentication` category).
- Test scope fixture (`make_scope` default) now grants all three check categories — required for the f2 listing invariant after the authentication template landed.

## 2026-10-24 — Devin (Alan): pilot deployment + release ops

- Branch `feat/alan/pilot-deployment-ops`. New: `backend/preflight.py` (startup config validation + Alembic-head preflight; wired into `serve`/`worker` CLI and `deploy/railway/start.sh` before `hackatl-migrate`), `backend/ops_logging.py` (JSON/text logging with `redact_text`; `LOG_FORMAT=auto|text|json`, json outside dev), `GET /api/v1/ops/status` (X-Ops-Token gate; queue counts by status, running jobs + heartbeat age, worker leases, migration current/expected/at_head, first-responder default mode + projects with non-default mode, alerting/flags/env/version), `backend/alerts.py` (`ALERT_WEBHOOK_URL` webhook on `job_failed_permanent`/`job_dead`), `FEATURE_FLAGS` allowlist, compose mem/cpu/pids limits on api+worker.
- Scripts: `deploy/scripts/db_backup.sh` (pg_dump -Fc, sha256), `db_restore.sh` (`RESTORE_CONFIRM` gate + production opt-in + re-migrate), `smoke_staging.sh` (15 HTTP checks), `integration_branch.sh` (merges PR heads onto `integration/pilot-<date>`; conflicts aborted+reported), `deploy/release_check.py` (17-point release checklist runner, PASS/FAIL/WARN/SKIP).
- Docs: `docs/deploy/PILOT_OPERATIONS.md` (staging bring-up, secrets, rollback, backup/restore, alerting, flags), `docs/deploy/RELEASE_CHECKLIST.md`. Env templates: `.env.example` gained `OPS_TOKEN`/`ALERT_WEBHOOK_URL`/`LOG_FORMAT`/`FEATURE_FLAGS`; new `.env.staging.example`.
- Executed verification (local embedded Postgres, APP_ENV=staging): `hackatl-preflight` PASS with 4 expected warnings; API+worker live on real stack; smoke 15/15 PASS; release check 18 checks, 0 failed; alert webhook received `[CRITICAL] ... job_dead` with `ghp_` redacted; backup 116K → restored into fresh DB, identical row counts and head `['0015']`; `ops/status` reports deferred job via `next_due_in_s`; negative preflight blocks start on unset secrets. Integration branch `integration/pilot-20261024`: #441 + #458 merge cleanly and 348 backend tests pass; #412 conflicts (superseded by #458) and #496 conflicts on CHANGELOG only — reported, not force-resolved.
- 19 new tests (preflight, ops status, alerts, ops logging) pass; mypy clean on touched files (7 pre-existing errors on main in live_defense/host/auto_commits/pipeline/routes). Not run: frontend Playwright e2e, CI (gh unauthenticated locally), real-Railway deploy.
