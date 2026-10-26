# Integration workstream — 2026-11-02 release branch status

Branch: `integration/2026-11-02-release` on m-alan08/ari-ai (base: upstream/main @ 64bb24a2).

## Merged workstreams (in dependency order)
- upstream #412 Phase 6.5 @ a9f5ff31 (merge 87ad6266)
- upstream #458 Phase 7C @ db0a631d (merge a682e1a1)
- fork #2 Live Defense E2E @ e187e9a2 (merge e486d03c)
- fork #1 execution reliability @ 17c3ded0 (merge 83781d5b)
- fork #3 investigation observability @ b44b28aa (merge f9ae4933)
- fork #4 security hardening @ 92178d0e (merge b011c878)
- fork #5 deployment/ops @ c7c5336f (merge 2a3e89dc)
- fork #6 customer onboarding @ 8b366fc5 (merge 2292f350)

## Resolutions of record
- Alembic collision: fork #1 kept 0016 (execution_reliability); hardening's
  repo-disconnect renumbered 0016→0017. Head pin in test_parallel_migrations is 0017.
  **release-validation: preflight must expect head 0017.**
- Repository disconnect (hardening × onboarding): union semantics — sets
  `disconnected_at` kill-switch AND clears `installation_id` + `automatic_assessments`
  AND revokes `deploy_commit_mappings`; reconnect endpoint retained (access re-verify
  only when a stale installation link remains). `Repository` contract gained
  `disconnected`/`disconnected_at` fields (SCHEMA_VERSION 0.24.0).
- worker.py: reliability loop kept wholesale + phase7c JobDeferred (jobs.defer) +
  alerts import union. execute_control accepts JobContext.effect guard (F1-adjacent
  but only for adapter calls; provider-request replay F1 remains owned by Grok).
- Codex findings folded in: test_independent_integration.py vendored from
  codex/first-responder-integration-review @ 6e882045; each reproducing defect is
  strict-xfailed with its finding id. Owners: F1/F7/F8 Grok, F2–F5 Devin Reliability,
  F6/F9 Devin Observability, F10 Devin E2E (fixed here).
- F10 fixed: compat test deadline used Python utcnow() vs PostgreSQL transaction
  now(); now sets deadline genuinely in the past.
- F9 fixed at integration level: phase7c schema pin updated to assert the composed
  0.24.0 (all reserved versions claimed). Deeper contract-evolution work stays with
  Devin Observability.
- test_preflight.py restored alembic_version to a hardcoded '0015', polluting the
  shared test DB and breaking later upgrade() calls (surfaced as test_sandbox_target
  failure). Now captures and restores the real head. Flagging for the
  release-validation owner: the test is outside the listed protected files but
  adjacent to your preflight work.

## Open tracked defects (from Codex review, awaiting owner fixes)
F1/F7/F8 (Grok), F2/F3/F4/F5 (Devin Reliability), F6/F9 (Devin Observability).
Each xfail flips to XPASS (a strict failure, forcing an unmark commit) when an
owner fix lands on a recomposed head.

## Recomposition 2 (onboarding @ cb085ed0)
Second merge of feat/alan/customer-onboarding (merge b5bfd3d0): owner converged
disconnect on `disconnected_at` flag + keep `installation_id` + revoke
`deploy_commit_mappings` + dict response `{"id", "disconnected": true}` — adopted
verbatim over the earlier union (their test asserts installation_id is kept).
Found + fixed during re-verify: owner's `_status` dropped the parked row from
the `repository` step (their own test asserts it stays `done`); the step now
uses unfiltered rows while every *connected* step (github_access, snapshot,
deploy_mapping, test_event) honors `disconnected_at`. Also: hardening's
`login:{ip}` burst (20/300s) starved the real-stack suite (~50 dev logins);
new `auth_dev_login_burst` setting (default 20) is set to 500 by e2e stack.py.
release-validation: `!.env.staging.example` negation from PR #7 will be
preserved when it merges.

---

## Recomposition 2 — owner fixes incorporated (tip 0f93f12c)

### Source heads (exact SHAs)
- upstream/main: `fef073d6`
- upstream #412 (Phase 6.5 RL-gated pilot): `4ecd7558`
- upstream #458 (Phase 7C, Grok F1/F7/F8): `5417990f` merged as `871ad9fd`; later refresh `9486a5b0` also merged
- fork #1 execution reliability (Devin Reliability F2–F5): `f2d911d7` (rebased head)
- fork #2 Live Defense E2E: `fcca6ed7`
- fork #3 investigation observability (F6/F9): `92e00096`
- fork #4 security hardening: `c7841e93` (canonical disconnect now richer: revokes deploy mappings, cancels queued repo jobs, fails wedged snapshots, cancels in-flight assessments; reconnect requires renewed authorization)
- fork #5 deployment/ops: `c7c5336f` (unchanged)
- fork #6 customer onboarding: `7918bbee`
- fork #7 release-validation: `ac693214`

### Integration defects found and fixed (no failures hidden)
1. `jobs.defer_count` column added to db.py/code but missing from Alembic 0016 — added to 0016 (upgrade+downgrade).
2. Legacy `jobs.defer()` definition still present in jobs.py, shadowing the new lifecycle-recording `defer()` — deleted (F4 fix was live but unreachable).
3. `job_max_running_emergency` setting dropped during settings union — restored (F2).
4. `repositories.disconnected_at` dropped from db.py during the db.py union — restored.
5. Union placed `provider_attempts`/`uncertain_provider_outcome` inside `ProviderAttempt` — moved onto `AgentExecutionRecord`; `ProviderAttempt` gained `model_id`/`cost_usd`/`error_code` from refreshed 7C.
6. Duplicated hard-coded `login:{ip}` 20/300s limiter coexisted with `auth_dev_login_burst` — deduped (burst default 120 per owner, e2e stack 500).
7. `reconnect` route returned Repository under dict signature — declared `response_model=Repository`.
8. F9 pin `SCHEMA_VERSION == "0.22.0"` → floor assertion `>= (0,22)`; SCHEMA_VERSION is now **0.25.0** (owner bump for `Repository.disconnected_at`).
9. `deploy/sandbox_target.py` used `AssessmentState.completed` (lowercase) → `.COMPLETED`; `test_sandbox_target` used GET for POST recipe routes → took upstream #533's method-aware test (identical fix — complementary with Adam).
10. `test_scenarios.py::test_e_worker_failure` asserted pre-F1 semantics (crash → invocation stays queued, replay doubles provider calls). Updated to the post-F1 contract: crash → invocation `failed`, redelivery does NOT re-run the physical provider loop.
11. `docs/security/CHECK_TEMPLATE_REVIEW.md` regenerated; `shared` contracts/fixtures regenerated (179 files clean).
12. Provider-ledger → job bridge added (`provider_ledger.mark_attempt_uncertain` sets `jobs.effect_state="in_flight"` + `effect_key=attempt_id` on the running agent-step job) so reconcile parks crash-uncertain billable work as `recovery_required` (F1's hard guarantee) instead of silent requeue.
13. `_enqueue_agent_step` + the inline decision-time enqueue now pass `deadline_at=decision.deadline_at` to `jobs.enqueue` — durable queue deadline survives redeliveries (F7).

### Codex F1–F10 status on this tip
All 10 findings are fixed and run as STRICT assertions (zero xfails):
`test_independent_integration.py` 10/10, `test_exec_reliability_compat.py` 8/8, plus the updated worker-failure scenario.

### Adam-overlap conflict report (upstream #530/#533/#535/#538/#540 open PRs)
- **#533** (`0016_worker_heartbeats`): adds its own Alembic 0016 — collides numerically with our `0016_execution_reliability` (three-way 0016/0017 situation: our chain is 0016 reliability → 0017 repo-disconnect; Adam's adds 0016 heartbeats + #535 adds 0016 tenant_rls_intelligence). NEEDS OWNER DECISION on renumbering before merge — documented, not resolved.
- **#533** also touches worker.py/jobs.py/settings.py/db.py/ops.py/test_parallel_migrations/conftest + the same `sandbox_target` fixes we independently applied (compatible, same direction).
- **#535** (`0016_tenant_rls_intelligence`): another competing 0016 + intel_store/privacy changes — complementary scope but same migration-collision problem.
- **#540** routes.py/github_repos/auto_commits/installations/pipeline — same files we touched; review on merge.
- **#530** routes.py/jobs.py/redaction/CI workflows — likely complementary; CI workflow files not merged by us.
- **#538** frontend console pages — additive; no overlap detected.
- None of Adam's PRs were merged into the integration branch; our changes do not revert his work. The three-way `0016` migration numbering needs his decision (he may already have resolved it — #533's diff showed a `0017_worker_heartbeats.py` file, suggesting he may renumber; confirm).

### Verification on tip 0f93f12c (all executed)
- backend pytest: **510 passed, 8 skipped, 0 xfailed**
- security-agents + shared: **712 passed, 24 skipped** (review packet regenerated)
- `make check` clean (ruff + format + export + examples)
- `uv run mypy`: clean (41 files)
- frontend: **347 unit tests**, lint, typecheck, `NEXT_PUBLIC_DEV_LOGIN=1 npm run build`
- transport: **35 tests**
- migrations: single head `0017` (parallel-migration test green)
- Playwright real-stack: **49/49**
- Codex independent regressions: **10/10 strict** + compat **8/8 strict**

### Blockers / GO-NO-GO
- F1–F10 all closed with strict assertions; remaining hard blocker = independent security re-review (per release protocol) + Devin Bot readiness re-run on this tip.
- Adam 0016/0017 migration-collision decision is required before any upstream merge — NOT a deploy blocker for the fork-side release candidate.
- Verdict: **GO for review + staging validation; NO-GO for customer deploy until independent security re-review and readiness suite pass on 0f93f12c.**

## Post-verdict fix — alert webhook wiring (tip 5eee457e)
Readiness re-run on 7cfba90f found `alert.webhook` dead: the F2–F5 worker rewrite dropped the `backend.alerts` import and both `send_alert` call sites (PermanentJobError → `job_failed_permanent`, attempts-exhausted → `job_dead`). Cherry-picked `fix/devin/alert-webhook-wiring` @ 93d5ecdd → `5eee457e`; worker/alert tests 30/30, readiness suite GO 25/25 per release-validation.

---

## Recomposition 3 — upstream reconciliation @ ee8b91e2 (tip 7bcf1b56)

Upstream main moved to `ee8b91e2` (Adam's #533 infra, #496 frontend, #558 ops storage, #529/#543 eval, #546 authz, #537/#550 rehearsal, #565/#566 docs). Merged in full; nothing of Adam's reverted.

### Migration reconciliation (the 0016 three-way — RESOLVED)
Upstream landed `0016_tenant_rls_intelligence` (#535) + `0017_worker_heartbeats` (#533). The release workstreams' competing migrations were renumbered onto that chain:
`0015 → 0016 tenant_rls → 0017 worker_heartbeats (upstream) → 0018 execution_reliability → 0019 repository_disconnect` — single head **0019**, `test_parallel_migrations` pin updated.

### Semantic conflicts resolved
1. **`worker_heartbeats` table name collision**: our exec-reliability worker registry (state/jobs_finished/last_seen_at) vs Adam's liveness table (heartbeat_at/last_poll_at) — different schemas, same name. Ours renamed to `worker_registry` (db.py + migration 0018 + execution.py + tests). Adam's schema/migration untouched.
2. **`ops` name shadowing in main.py**: `from backend.api import ops` shadows `from backend import ops` — on upstream `ops.readiness`/`ops.diagnostics` would AttributeError (latent upstream bug). Fixed via `ops as ops_api` alias; readiness/diagnostics still resolve to backend.ops. Also added missing `jobs` import for the QueueBackpressure handler.
3. **Two ops surfaces preserved**: our `GET /ops/status` (X-Ops-Token, OPS_TOKEN) + upstream's `/ops/storage`, `/ops/maintenance`, `/ops/storage/prometheus`, `/ops/maintenance/dry-run-report` (Bearer OPS_API_TOKEN). Both auth mechanisms kept verbatim.
4. **SCHEMA_VERSION → 0.26.0**: upstream took 0.22 = ops readiness; our chain renumbered (7C=0.23, observability=0.24, onboarding=0.25, disconnect=0.26). Version comment documents the mapping.
5. worker.py heartbeat loop unions our `heartbeat_state` lease-state semantics + upstream's `jobs.worker_heartbeat` liveness row; startup does `prune_worker_heartbeats` then `reconcile_safely`; shutdown does `execution.worker_gone` + `jobs.worker_stopped`.
6. settings union: exec-reliability knobs + upstream `worker_heartbeat_interval_s`/`worker_liveness_s`/`readyz_require_workers` + both ops-token validators.
7. `score_api.py`: took upstream's superset (adds `_finding_paths` fallback).

### Verification on tip 7bcf1b56
- backend pytest: **558 passed, 9 skipped**
- security-agents: **605 passed, 23 skipped** (packet regenerated)
- shared: **115 passed, 1 skipped**
- `make check` clean · `uv run mypy` clean (41 files)
- frontend: **351 unit**, lint, typecheck, build
- transport: **35**
- migrations: single head 0019, ops-readiness suite 13/13
- Playwright real-stack: **49/49**
- Codex F1–F10 strict assertions: inside backend suite (10/10 + 8/8, zero xfails anywhere)

### E2E PR #2 F7 xfail (item 8)
Already resolved in recomposition 2 — the compat file runs all 8 assertions strict (deadline_at propagation landed). No remaining F7 xfail to coordinate.

### Sequential upstream merge proposal
Adam's merged work is already composed here. For landing OUR workstreams upstream, dependency-safe order:
1. **upstream #412 (Phase 6.5 RL-gated)** — base contract layer; already on upstream main? NO — check: upstream still says "0.20 reserved by open PR #412", so #412 remains open. Land first (our integration includes it).
2. **upstream #458 (Phase 7C @9486a5b0)** — execution substrate + provider ledger; depends on #412's contract fields. Renumber contract to post-0.22 ops readiness if Adam re-bases.
3. **fork #1 execution reliability** — needs migration rename to stay linear (now 0018) + `worker_registry` table (not `worker_heartbeats`).
4. **fork #2 Live Defense E2E** — test-only, safe anytime after #458.
5. **fork #3 observability** — contract 0.24 read-only DTOs.
6. **fork #4 security hardening** — disconnect canon → migration 0019; must land after #1.
7. **fork #6 onboarding** — depends on #4's disconnect + #5's ops env vars.
8. **fork #5 deployment/ops** — independent of the app-code chain but PILOT_OPERATIONS/preflight expects the final migration graph; land after 0018/0019 are decided upstream-side.
9. **fork #7 release-validation** — gates on everything above; land last.

Every upstream-bound PR must rebase past `ee8b91e2`: our migration numbers (0018/0019) already sit on Adam's 0016/0017 so no renumber needed if order above holds.

### Blockers / GO-NO-GO
- Independent security re-review on 7bcf1b56 (pending).
- Devin Bot readiness re-run on 7bcf1b56 (requested).
- Verdict: **GO for review + staging validation; NO-GO for customer deploy** until re-review + readiness pass.

---

## Reconciliation pass — upstream/main 580d2f67 (final RC, 2026-10-24 late UTC)

Merged upstream/main `ee8b91e2..580d2f67` (material delta: #540 backend onboarding lifecycle + continuous assessment, #530 CI release-gate + dead-letter jobs list + scored-only evals, #531 intel pipeline audit + secret redaction at write time, #567 `ops as ops_api` alias — same fix we made). User-requested base was `2c8ad50d`; upstream moved further — merged the actual tip `580d2f67`.

### Conflicts resolved
- `main.py` — upstream #567 applied the same `ops as ops_api` fix we made; kept ours (identical semantics) + our extra routers.
- `jobs.py` — union: our lifecycle-recorded `claim()`/`fail()` + upstream's `redact()` wrapping of every persisted `last_error` (write-time secret scrubbing). **Two real integration defects introduced by the merge, both caught by upstream's new tests and fixed** (`be4d746f`):
  1. `auto_commits.assess_commit` — union duplicated the `github_client().commit()` call ahead of the try/except, so 404s propagated instead of routing to `_permanent_failure` → `policy_exit`. Deduped; upstream `test_missing_commit_records_skipped_instead_of_dead_lettering` green.
  2. `jobs.fail()`/`claim()` — upstream's `redact(error)` lived inside the conflict region and was dropped; persisted `last_error` kept secrets. Re-applied `redact()` on all 4 sites; upstream `test_fail_scrubs_secrets_at_write_time` green.
- `routes.py` — unioned all three imports (`_refresh_personal_installation_repositories`, `can_connect`, `verify_installation_access` — both new upstream functions coexist with ours).
- `auto_commits.py` — our `disconnected_at` guard first, then upstream's `_permanent_failure` try/except (complementary).
- Docs — union/appended (CHANGELOG, SESSION_HANDOFF).

### #458 status
Confirmed **still open, not merged** on upstream/main — `provider_ledger.py` absent. Our branch carries it at `9486a5b0` merge-point.

### Migration graph
No new upstream migrations in this delta. Chain unchanged: `0015 → 0016 tenant_rls → 0017 worker_heartbeats → 0018 execution_reliability → 0019 repository_disconnect`. Single head `0019`; `test_parallel_migrations` + `test_ops_readiness` green inside the backend run.

### Results on e27b6a43 (post-merge tip)
- backend: **610 passed, 9 skipped, 0 failed** (upstream added ~52 tests since 558)
- security-agents: 604/23 after regenerating `CHECK_TEMPLATE_REVIEW.md` for upstream template changes (`e27b6a43`)
- shared: 115/1 · make check + mypy clean · contracts regenerated, 180 files fresh
- frontend: 355 unit + lint + typecheck + `NEXT_PUBLIC_DEV_LOGIN=1` build
- transport: 35/35
- Codex F1–F10: zero xfails; all 17 independent/compat assertions strict — all green inside backend run
- Playwright real-stack: see below (running at time of note)

### Freeze
Release-candidate SHA = tip after Playwright verification; handed to Codex re-review + Devin Bot readiness re-run.

### Final freeze (2026-10-24)
Playwright real-stack: **49/49**. **RC SHA = `bc490484`**, pushed to PR #8. Handed to Codex re-review + release-validation readiness re-run. Remaining blockers: security re-review on bc490484, readiness re-run (expected GO — head still 0019), Adam's buy-in on the upstream merge sequence. Nothing auto-merged.

### Release-validation gate — final
Devin Bot readiness re-run on `bc490484`: **GO — 25/25 PASS, 0 WARN, 0 FAIL** (`deploy/reports/release-readiness-20261025T021130+0000.{json,md}` on their branch; coordination file `20261025T021140Z-release-validation-rc-go.md`). Every code-side gate green. Remaining blockers are external: Codex independent security re-review on bc490484, and operational pre-pilot items (real secrets, backup scheduling, first real staging deploy → `release_readiness.py --remote`).

---

## Status check — upstream 91b234d2 + Codex verdict + #458 conflicts (2026-10-25)

### Codex exact-SHA verdict — FOUND
`upstream/codex/release-0019-rereview` @ `005ea42d`: **NO-GO on `070f75a9`** (pre-580d2f67 tip — no verdict exists yet on frozen RC `bc490484`). Report: `docs/reviews/first-responder-release-0019/REPORT.md`. Ran Codex's transplanted probes on `bc490484` in scratch (not committed): **4 FAIL — R2, R3, R4, R5 all still reproduce**; migration probes pass. C-status on bc490484: C1 fixed (last_error redaction, test green), C2 fixed (`GET /projects/{id}/jobs` present via #530 merge), C3 fixed (dup commit call removed → `_permanent_failure`, test green), **C4 OPEN** (mid-line redaction = upstream #570 `2c825c4e`, inside the new 91b234d2 delta, not on the RC), C5 fixed (`_SECRET_VALUE` quarantine present via #532 merge).

Open findings on the RC: **R2 (P1) disconnect doesn't stop active provider work; R3 (P2) stale cached authz on reconnect; R4 (P2) cross-tenant connection-ID leak in picker; R5 (P2) abrupt worker death → queued not recovery_required; C4 (P2) mid-line secret redaction.** Owner-assigned work, not integration glue.

### Upstream 91b234d2 materiality (for frozen RC)
Material: #570 `2c825c4e` redaction gaps (fixes C4), #582 `MIGRATION_DATABASE_URL` + env.py (upstream-owned files, compatible), #574 idempotent 0016/0017 (upstream files, additive), #584 per-project review quota, #587 sealed-diff delivery, #588 first-review CTA, #572 cross-org jobs test, SaaS sidebar layout + e2e spec updates (frontend conflicts expected next recomposition). Not merged into the RC — `bc490484` stays frozen.

### #458 conflicts resolved
Branch `merge-proposal/pr458-onto-main-91b234d2` pushed to fork. 4 conflicts: shared/__init__.py (Phase 7C renumbered 0.22→**0.23.0** — upstream owns 0.22 ops readiness; #412 keeps 0.20), test_sandbox_target.py (dup `_fill_body`→upstream's `_render_body`, functionally identical), CHANGELOG + SESSION_HANDOFF union. 45 targeted tests green (`test_live_defense_phase7c*`, `test_sandbox_target`, `test_parallel_migrations`, `test_ops_readiness`). Ready to push to #458's upstream head branch with Alan's approval — updates the PR head, no merge.

### Merge sequence + authorization
Adam's coordination record `20261024T231500Z`: #535✓ → #533✓ (both landed) → **#458 next — but NO-GO until R5+R2 fixes land and independent re-review passes** → #412 last (needs rebase + contract review). Fork: unchanged — #1→#2→#3→#4→#6→#5→#7; Codex scoped GOs: #2 (test-only), #7 (tooling). Nothing is upstream-mergeable right now.
Authorization needed: (a) owner fixes for R2–R5 (+C4 via next recomposition), (b) Codex re-review on new exact SHA, (c) Alan's go-ahead to push `merge-proposal/pr458-onto-main-91b234d2` to #458's upstream branch, (d) Adam's sign-off on the upstream landing order.
