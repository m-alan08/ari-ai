# Alan — implementation status

Owner: Alan (`security-agents/`, SEC-001 … SEC-013; interim Live Defense UI)
Branch: `cursor/live-defense-first-responder-0044`
Current task: Phase 7A deterministic first-responder orchestration (no Phase 7B, no AUTO-EXECUTE)
Last remote sync: 2026-10-24, `origin/main` @ `c5c2b47d` (PR #453 merged; #412 still OPEN)
API/contract dependencies: `shared` 0.21.0 first-responder contracts (0.20.0 reserved for open #412); seven AgentSpecs; backend host checkpoints
Recent PR/commit: https://github.com/adamissac/ari-ai/pull/439 on `cursor/live-defense-first-responder-0044`; rebased onto main; leave open for security review
Blockers: live seven-agent Claude run (issue #10, no key); human/security approval of #439 (do not merge); GitHub Actions may not queue from this App token
Next action: leave Phase 7A PR open; do not start Phase 7B; Codex must not train from these records yet

## What runs today

| Task | What exists | Evidence |
|---|---|---|
| SEC-001 | `AgentSpec` + bounded agent loop: budgets (token/usd/turn/tool-call/wall-clock), one schema-repair retry, cancellation, kill switch, redacted transcripts | `tests/test_runtime_loop.py` (17) |
| SEC-002 | Tool registry with the docs/07 capability matrix, typed args, scope enforcement, audit; `scope_guard` pure | `tests/test_tool_registry.py` (51), `tests/test_scope_guard.py` (24) |
| SEC-003 | Sandbox driver: pinned snapshots, profiles, network modes, limits, timeout, env scrubbing, reaper; hardened Docker argv | `tests/test_sandbox.py` (33) |
| SEC-004 | Check-runner path against the authorized target, scope-gated at the driver | `tests/test_checks.py` (19) |
| SEC-005 | Check-template library v0: `access_control.ownership@1`, `input_validation.bounds@1`, rendered from the scope-approved build recipe, deterministic `matches_expectation`, fails closed when unreviewed | `tests/test_checks.py` |
| SEC-006 … SEC-009, SEC-011, SEC-013 | Scout, Probe, Trace, Diagnose, Patch, Fortify: own prompt, I/O schema, tool set, budget, and deterministic post-processing | `tests/test_agent_logic.py` (47) |
| SEC-010 | Verify: own run, fresh checkout, fresh sandboxes, no Patch transcript, verdict computed from the 5 checks | `tests/test_agent_logic.py`, `tests/test_e2e_replay.py` |
| SEC-012 | Orchestrator state machine, gates, per-finding remediation sub-flow capped at 2 attempts, hash-chained audit | `tests/test_states.py` (47), `tests/test_e2e_replay.py` (28) |
| — | Export adapter onto canonical `shared` V0 | `tests/test_contracts.py` (16) |
| — | `AssessmentHost` adapter (`build_orchestrator().run(host)`) | `tests/test_host_adapter.py` (11) |
| — | Security-boundary suite (SB-07, SB-08, SB-11, SB-12 + the SB map) | `tests/test_security_boundaries.py` (32) |

## Verified tests

Executed on `origin/main` @ `39186cb`, 2026-10-17:

```
cd security-agents && .venv/bin/python -m pytest tests -q
451 passed, 18 skipped in 82.57s
uv run pytest -q          # repo root testpaths (shared, monitoring, backend, code-review, standards, tests/security)
415 passed, 9 skipped in 75.46s
```

Skips: opt-in live-provider tests, and Docker tests that run only in the CI
`docker-sandbox` / `docker-reviewer` jobs (6 passed there on `719ce98`).

## Honest status of what is NOT verified

Updated 2026-10-17 after #100 to #125.

- **No live-provider run of the seven agents has been executed (M3 open).** Every
  security-agents number comes from scripted model turns with real tools, sandboxes
  and tests. The only live attempt stopped at Scout on a tool-schema 400 (fixed since).
  `tests/test_live_provider.py::test_live_seven_roles_end_to_end` is ready for a key
  holder (issue #10). Live mode stays **UNVERIFIED** until it runs.
- **Docker sandbox: verified in CI for the paths we use.** The `docker-sandbox` job
  builds `security-agents/sandbox/Dockerfile` (digest-pinned python:3.12-slim) and passes
  6 real-container tests: uid 65534, read-only root, a raw socket blocked by the kernel,
  no host env, timeout kill and removal, the payments-lite suite, and the reviewed
  ownership and bounds checks with the same verdicts as the local driver. Sai's
  `docker-reviewer` job reuses the image. Not verified: v0 checks call the app in process
  (`TestClient`), so the target container and `--internal` network are created but no
  check sends traffic over them. The CI image tag is local, so the job sets
  `SANDBOX_ALLOW_UNPINNED_IMAGE=1` (the base is pinned).
- **Host adapter: runs against the real `BackendHost` on PostgreSQL in backend tests**
  (`backend/tests/test_security_agents_host_mode.py`, `test_m7_push_to_regression.py`,
  `test_standards_host_integration.py`, `test_roe_persistence.py`), with scripted model
  turns. Not verified: a deployed worker against a live repository.
- **Expectations.** Hosted runs use the shared code-intelligence index when the
  repository is bound (`intel.host_service.shared_host_intel`). Directory fixtures fall
  back to `SECURITY_AGENTS_LABELS` (dev/test only; the backend host-mode test sets it).
  The answer-key sections of labels.yaml are never read by an agent.
- **Probe's template library has two templates.** Both are marked
  `reviewed_by="security-owner:pending-human-signoff"`; no human security owner has
  signed them off. A template with `reviewed_by=None` is refused at runtime.
- **Standards.** The NVD terms of use for the 34 pinned CVSS v3.1 vectors are
  UNVERIFIED (Cloudflare challenge). OWASP API and LLM data are CC BY-SA 4.0; we store
  only ids and titles. Everything is "mapped to" or "aligned with"; no certification is
  claimed.

## Session entries

### 2026-10-24 (rebase #439 onto main — docs conflicts)

- GitHub showed **Checks awaiting conflict resolution** on `CHANGELOG.md` and `SESSION_HANDOFF.md` only. Rebased onto `origin/main` `c5c2b47d`. Kept both Unreleased/handoff sides plus the four Codex fixes. Schema still 0.21.0 / Alembic `0015`. #412 still 0.20.0, no Alembic.
- Executed: ruff pass; export 154 files; pytest phase7a + jobs + no-action — 33 passed. PR left **open**.

### 2026-10-24 (Phase 7A Codex review + schema 0.21.0)

- PR #439 left **open**. Backend/test CI: removed AUTO-EXECUTE route so `test_no_route_applies_a_defensive_action` passes (404, not a path containing `execute`). Four Codex findings fixed with regressions. Shared **0.21.0** / orchestrator `7a.1.1` / Alembic `0015`. #412 keeps 0.20.0. Message: `20261024T050000Z-alan-to-all-live-defense-phase7a-schema-0-21.md`.
- Executed: `pytest` phase7a + jobs + rl_no_action_path — 33 passed. Contract export 0.21.0. No merge, no Phase 7B, no RL training.

### 2026-10-23 (Live Defense Phase 6 post-merge audit)

- Branch `feat/live-defense-phase6-pilot-audit` from `origin/main` `02ab34de`. PRs #393 (`57e0326`) and #395 (`8bbc01e`) confirmed merged. Schema 0.19.0, Alembic `0014`. Shadow cannot execute with grant+channel present; disconnect blocks leftover execute and serializes on the application row; queue depth includes assessment-bound jobs; recovered failures stay on the trail. **CONDITIONAL GO** for recommendation-only shadow-pilot. No Phase 7 / no RL edits.
- Executed: `make check`; `uv run mypy` 40 files; focused live-defense pytest 149 passed / 1 skipped; frontend lint/typecheck; vitest 330; transport 35.

### 2026-10-22 (Live Defense Phase 3 shadow runtime)

- Branch `feat/live-defense-phase3-shadow-runtime` from `origin/main` `6e98b23`. Contracts 0.17.0. No `rl-defense/` training/checkpoint/promotion edits. No Phase 4 enforcement.
- Executed: `make check`; `uv run mypy` 40 files clean; focused pytest 85 passed / 2 skipped; frontend lint/typecheck; vitest 325; transport 35; `next build` ok.

### 2026-10-17 (verify honesty + CheckCategory)

- Kept #84 `provider_mode="none"` for functional Verify. Canonical `CheckCategory`
  with Probe ids + fixture aliases. `SCHEMA_INVALID` regression for recipe
  `target_dir`. Message: `20261017T092222Z-alan-to-srihith-check-category-canonical.md`.

### 2026-10-17 (Codex P1/P2 after #73 merged)

- #73 is on main. Did not push the merged branch. All seven Codex items were
  missing from `origin/main`; implemented on `feat/alan/host-resume-hardening`.
- Message: `20261017T085211Z-alan-to-srihith-host-resume-hardening.md`.

### 2026-10-17 (Srihith follow-up c75a56e on #73)

- Ported `_report_or_wait` + unconditional `external_pending` pause from
  `feat/srihith/functional-remediation` @ `c75a56e`. Did **not** merge #71.
- Complete-run functional reproduction was already `_functional_reproduced`
  on this branch; added hang/`exit_code=None` to the guards test.
- Message: `20261017T083214Z-alan-to-srihith-ported-c75a56e-pause-gates.md`.

### 2026-10-17 (host finding persistence)

- Branch `feat/alan/host-finding-persistence` off current main. Did not continue
  `feat/alan/security-standards` or merge PR #35.
- `HostSink` persists finding status/history; checkpoint save/load resumes after
  `pipeline._ORCHESTRATORS.clear()`; functional repair + five-check Verify via Sai's
  `functional_verification_inputs`; `tool_calls` / `sandbox_seconds` charged.
- Tests actually run: `make check`; host-adapter 16 passed; unified + host-mode +
  e2e-host included in 27 passed after merging main.
- Messages: srihith (checkpoints/lifecycle), sai (functional Verify wired),
  vihaan (status no longer stuck at hypothesis).

### 2026-10-17 (session 1)

- Read `MASTER_BUILD_SPEC.md`, `AGENTS.md`, `CLAUDE.md`, the coordination files,
  `team-prompts/ALAN_IMPLEMENTATION.md` and the subsystem specs (docs/07, 08,
  12, 15, 16, agents/*). Checked GitHub: at session start there were no branches
  besides `main`, no PRs and no messages.
- Implemented SEC-001 … SEC-013 on `feat/alan/seven-agent-runtime`:
  contracts adapter, LLM providers (live/fake/replay), scope guard, tool
  registry + catalog + services, sandbox (workspace, local and Docker drivers,
  in-sandbox entrypoint), check-template library, code-intelligence port and
  local adapter, agent runtime, the seven agents, the orchestrator, the CLI.
- Wrote and executed the test suite; it found three real defects in my own code:
  a plain `Path` workspace resolving to the filesystem root (`Path.root` exists),
  a call-graph self-resolution bug, and a redactor that missed a secret glued to
  other characters. All three fixed with a pinning test.
- Synced with `origin/main` after Srihith's PR #8 merged: took Adam's canonical
  `examples/payments-lite/` (`target/` + `labels.yaml`), dropped my own flat
  variant of the same fixture, added `build_recipe.json`, generalised the bounds
  template for the real API shape, and rewrote the scripted trajectories so the
  fix is the missing `require_owner` call.
- Built the export adapter onto `shared` V0 with 16 contract tests, which
  surfaced six field-level mismatches; acked the contracts and sent the deltas.
- Implemented Srihith's `AssessmentHost` interface so the backend worker can run
  the pillar, with the approval round-trip, and sent the two open questions.
- Messages sent: contracts ack + deltas (srihith), shared repair sandbox (sai),
  code-intelligence port (sarosh), agent UI fields (vihaan).

### 2026-10-17 — Codex recovered catalog consolidation
- Preserved Claude's uncommitted catalog work as 93c0225 on PR #35, with subsequent integration fixes committed/pushed.
- Reviewed/merged PR #69 with all CI green. PR #73 is a newer focused host/functional-Verify implementation; PR #35 will be superseded once unique fixes are preserved.
- Isolated canonical CVSS3.1, OWASP API2023 and NIST800-115 package additions onto feat/alan/recovered-catalogs, excluding the duplicate legacy security-agents standards tree and schema projection.
- Standards/runtime standards source tests passed 300 offline; narrow canonical tests will be rerun here. Earlier recovered runtime passed 524 tests (11 optional skips), root 358 (6 optional skips), browser 8. These counts belong to PR #35 and are not claims about this new branch.
- Exact .gitleaksignore fingerprint is a public OWASP source commit, not a credential; full-history scan clean and synthetic credential detection remains enforced.

### 2026-10-17 — Codex host-recovery hardening
- Continued PR #73 after isolating unique catalog recovery in PR #74. Preserved fail-closed scope/budget/human-approver mapping from #35; resume reloads authorization and rebinds the current worker host.
- Functional Verify requires actual failed assertions on every generated test file and recorded sandbox runs; errors, timeout, empty tests, skipped generated tests, source overwrite and test overwrite are rejected. Added 19 negative/authorization regressions (36 focused tests passed).
- Adopted PR #71's two-approval browser harness without merging its competing host implementation. Browser e2e: 8 passed (23.1s).
- Full root suite: 324 passed, 6 optional skips. Security suite: 379 passed, 11 opt-in/optional skips. Ruff/format/mypy pass. No new live-provider or Docker proof.
- PR #35 remains recoverable and will be superseded by #73/#74; no work discarded. Merge these only after current-head CI passes.

## Standards addendum (P0) on `feat/alan/standards-roe-wiring`

| Item | What exists | Evidence (offline) |
|---|---|---|
| Pinned indexes | CWE 4.20, ATT&CK Enterprise 20.0, D3FEND 1.6.0, OWASP API 2023, OWASP LLM 2026, CVSS v3.1 and v4.0, NIST SP 800-115; sha256 in `standards/standards/data/MANIFEST.json` | `standards/tests` 189 passed |
| CVSS v3.1 | `standards/standards/cvss31.py`, 34 NVD vectors plus property tests; level derives from the computed rating (ADR-0012) | `standards/tests/test_cvss31.py` |
| Rules of engagement | `security_agents/standards/roe.py`; registry gate fails closed with SCOPE_DENIED and `tool.roe_denied` | `tests/standards/test_rules_of_engagement.py`, `tests/standards/test_negative.py` |
| Agent wiring | Scout CWE plus OWASP API tag from code; Trace v3.1 metrics with evidence or unverified, optional ATT&CK; Diagnose and Fortify optional D3FEND | `tests/test_agent_logic.py`, `tests/test_e2e_replay.py` |

```
cd security-agents && .venv/bin/python -m pytest tests -q        # 441 passed, 10 skipped
.venv/bin/python -m security_agents.standards.report            # 217 standards tests, 0 failed, UNVERIFIED: NVD licence terms
.venv/bin/python -m security_agents.standards.smoke             # writes standards-smoke-output/{finding.tagged.json,rules_of_engagement.json,sp800115_coverage.json}
```

UNVERIFIED: NVD terms page (Cloudflare challenge). All runs above are replay or offline; no live Claude call.

### 2026-10-17 — Docker evidence reconciliation
- Confirmed #73/#74 merged, #35 closed, and no open PRs at session start. Preserved the primary Devin checkout and its untracked reports.
- Verified security CI 39033028814 on main `5b3ef50`: 6 real-container tests passed and ordinary security suite 451 passed/18 skipped. Reviewer Docker CI 39028078294 also passed.
- Corrected stale README integration dependencies and Docker-unverified claims in README/milestone/task evidence. No new live provider or local Docker run; published sandbox image digests and live seven-role proof remain open.

### 2026-10-17 — QA-002 M7 runner continuation
- Fixed the remaining Devin M7 runner's uv dependency/shared-db lifecycle operations and moved generated output into ignored .dev/qa (overridable). Exact current interpreter is retained; live calls forced off.
- QA runner13 tests passed; actual M7 backend workflow passed against PostgreSQL. Wrapper correctly exits2 for unexecuted browser stage. Full current security451 passed/18 skipped and standards190 passed earlier this session.

### 2026-10-17 — Codex application integration sprint
- Isolated checkout /private/tmp/omen-codex-integration, exact base37b0920c37d0460ecf988126693e79761478a888. Claude security-agent lane left untouched.
- API-RETRY-001 checkpoint d3320be: PostgreSQL transaction serialization and project-bound request digest prevent duplicate assessment starts and wrong-project cached acceptance. Before-fix reproduction2 failed; after-fix focused16 passed.
- Required suites: root420 passed/9 optional skips; backend79 passed/5 optional skips. Lint/format/mypy/contracts/links clean. Actual PostgreSQL/handlers/jobs; scripted providers. No new browser/live/Docker proof.
- PR for review only, no automatic merge. Compatibility note: old cached keys conflict after upgrade (fresh keys for new actions). Existing raw/stored patch hash issue155 remains in Claude's lane.

### 2026-10-17 — Codex offline typography stabilization
- Branch fix/alan/offline-font-build, isolated from main d1d324d; local next/font loader, five WOFF2 assets115,760 bytes, all licenses/hashes/provenance included.
- Original font-service build fails under unavailable proxies; local-font default production build passes under the same proxies.69 frontend tests, typecheck/lint pass. No landing/branding redesign or runtime dependency addition.

### 2026-10-17 — Codex explicit assessment conflict recovery
- fix/alan/explicit-conflict-recovery, own worktree from d1d324d. Reproduced absent reset and cross-project key retention (2 failed tests);3 targeted and70 frontend unit tests now pass. TS/lint/production build pass.
- Actual Chrome13 passed, including real409, retained-key retry, explicit preparation with zero requests, then exactly one new accepted assessment/job. Normal network-error retries keep the key; project identity is in the key signature. No product API or security changes.

### 2026-10-17 — Codex real application verification and job-status recovery
- Existing PR166 all CI green, no review findings; merged by Srihith. New work isolated on test/alan/demo-recovery-smoke at /private/tmp/omen-demo-smoke, initial base30e1234, synced main056f65b.
- Confirmed GET/jobs404 for a returned assessment job ID; resolved project through persisted assessment. Tenant404 and signed-out401 remain tested. Commit00d5f54, sync c185b67.
- Real Chrome14 passed (full scoped/scripted assessment/approvals/evidence/closure plus new outage/Retry/reload/missing-assessment cases); HTTP/client6 passed (lost response after server commit, same IDs,409 for conflicting body/project, fresh key, actual artifact bytes and corrupt-download rejection).
- Root422 passed/8 optional skips; backend80 passed/5 skips; targeted13 passed; frontend67/transport27 unit tests passed. Production webpack build, TS/ESLint, Ruff/format/mypy38 sources,112-file contracts current. No new live/Docker proof.
- Frontend follow-up: unchanged form after409 retains the conflicting key; add an explicit new-action control rather than automatically rotating keys after network loss. Issue155 raw/stored patch hash and Claude's live/Probe/RoE/Docker lane untouched. Initial Turbopack font download failed; webpack build and actual browser then passed.


### 2026-10-20 — Codex continuation of Claude RL v8

Branch `feat/alan/rl-v8-continuation` from `45f71eca`. Fixed PR #370's incumbent-gate
and reference-alias findings; added report hash pinning and registry-backed evaluation.
V8 historical report is preserved and cannot promote over v6. No training or promotion.
Focused RL suites: 35 passed; final evidence regression/full-suite results in continuation
PR and immutable coordination message. Await owner review; issue #317 authenticated
approval/report trust follow-ups remain. Next research step requires preregistration,
fresh holdout and incumbent comparison.

Final local verification: **228 RL tests passed** on `f9962eb`; PR #375 awaits review.

### 2026-10-20 — V9 explicitly requested by owner

IN_PROGRESS `feat/alan/live-retrain-v9`, stacked on PR #375. Fresh val_v9/holdout_v9,
five training seeds, actual incumbent v6, raw trajectories and seed-level aggregation.
Preregistration must be committed/pushed before execution. No automatic promotion.

### 2026-10-20 — V9 reporting worktree

Prepared `feat/alan/v9-reporting` in `/private/tmp/rl-v9-reporting` while training remains
frozen at29aa5f8 in `/private/tmp/rl-v8-continuation`. Reporting/split/cohort guards20
passed; earlier reporting/pipeline22 passed. No held-out v9 policy executed by these
tests. Integrate reporting only after the complete cohort exits; no model promotion.

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

### 2026-10-21 — V9 pre-merge review corrections

Addressed five PR #376 review findings: require source/teacher-budget/expert/rule
cohort provenance and complete holdout scenario sets; index trajectories at initial
export; pin fixed v7/v8 bytes in launcher/reporter. Historical reports remain unchanged;
explicit saved-command budgets reproduce the original summary. Focused31 tests pass.
No retraining, holdout replay or promotion. Owner authorized merging #375/#376 and
subsequent v10 failure analysis, regressions and preregistration.

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

#375 merged at64c38b7 after all checks. #376's eight review findings addressed;
its historical CLI reproduces the unchanged summary and77 stored artifacts verify.
#378 holds candidate demo balance and unexecuted controlled preregistration.
V10 review fixes include source-byte fingerprints and required secondary reporting;
58 integrated tests pass. No new policy selected or promoted.

#376 merged atce723f8 (all CI green,267 RL tests). Late review findings are fixed
in #378 before its merge; v9 original summary preserved, enriched provenance copy
added, and finalization fails without partial writes. No fresh evaluation or promotion.

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

### 2026-10-22 — owner-authorized RL-CMDP-001 Phase1

Research implementation/evidence in PR #388, `feat/alan/constrained-rl-phase1`.
Keep unmerged under the latest no-production-deployment restriction (main deploys).
Audit/CMDP/action matrix/offline readiness/four-paper decision delivered.140fixed
baseline episodes /4,200windows verified,8,400historical train transitions adapted
read-only. Budgeted heuristic has99.50% availability but93.16% compromise, so **no-go
now** for constrained-DDQN/CQL. No Phase2/training/promotion/serving change.

27focused tests pass (9.91s); original full347pass plus subsequent new regressions, shared
contracts60pass and skipped TypeScript check passed separately. All five CI workflows
passed source184a61a; review-proof fixes pending current-head CI. Four review findings
fixed with source Git-object proof, portable registry guard, policy-decision replay and
schema discriminators. See docs/research/constrained-rl-phase1/VERIFICATION.md.

## RL research continuation — 2026-10-23

RL-CMDP-003 on `feat/alan/read-context-integration`: opt-in owned payments-lite
read wrapper, store-derived context, serialized permission updates and buffered
freshness checks. Eight focused integration tests pass. No serving hook, model
training or operational-control integration; historical evidence remains unchanged.
Details: `docs/research/deterministic-feasibility/APPLICATION_CONTEXT.md`.

## RL response integrity — 2026-10-23

RL-CMDP-004: `feat/alan/read-response-integrity` closes a reproduced wrong-payment
response leak in the opt-in wrapper. Existing PaymentOut schema and expected
resource/owner must match before releasing buffered JSON. Twenty integration
tests pass; CI/review pending. No serving or model changes.

## RL readiness audit — 2026-10-23

RL-CMDP-005 on `feat/alan/feasibility-readiness-audit`: read-only original-fixture
audit with fixed source/index trust anchors and explicit inconclusive readiness.
Eighteen tests pass; no study rerun, training or serving integration.

## RL authorization-context provider — 2026-10-23

RL-CMDP-006, branch `feat/alan/authorization-context-provider`: owner-authorized
version 0.1.0 internal provider and payments-lite trusted-record adapter. 57 focused
tests pass; review/CI pending. Four authorization states, scoped clock/revision
metadata and documented future causal observation boundary. No new external
service, shared public API, RL observation modification, training or promotion.

## Adaptive-defense readiness — 2026-10-23

RL-CMDP-007, `research/rl-adaptive-defense-readiness`: user-requested research
assessment after #414; NO-GO/Option C, no new training. Pure cutoff observation
adapter and read-only training-archive inventory only. 54 focused tests pass;
review/full CI pending. See docs/research/adaptive-defense-readiness/README.md.
No new shared public contract, environment dynamics, collection or enforcement.

RL-CMDP-007 publication update: PR #422, tested code6c833795. Late arrivals enter
their knowledge-arrival window without rewriting earlier observations. Local166
research regressions passed in19.80s;91 shared contract tests including TypeScript
passed in0.96s; two new modules pass scoped strict mypy. NO-GO/Option C unchanged.
Exact commands and the pre-commit provenance rejection are retained in
docs/research/adaptive-defense-readiness/VERIFICATION.md. Final CI/review/merge state
is recorded on PR422; no new study may start without separate owner approval.

## Resource-exhaustion research — RL-CMDP-008

research/rl-resource-exhaustion-feasibility: owner-authorized finite-capacity model,
fixed baselines and causal observations. Core23 unit tests pass; Stage2 preparation
and final CI pending. No formal comparative study or model training authorized.

RL-CMDP-008 Stage2 preparation implemented: versioned experience/config/profile/
cutoff schema, separate reward/cost arrays, bounded unit-only replay, fixed unit
receipt writer/verifier with raw work/control/cost accounting, failure retention,
and deadline-bounded settlement. No optimizer, checkpoint writer or study runner.
Stochastic private phases/tie ordering avoid clock or label-priority shortcuts.
Proposed study now600 episodes/18,000 decisions plus bounded settlement, with
unapproved safety/sample/compute values. Training NO-GO pending approved formal
feasibility, safe baseline headroom, terminal-cost collector/learner review and
explicit training authorization. No historical evidence or customer control edits.

Resource feasibility001: owner approved one deterministic study with acceptance
failures retained and execution failures stopping. Freeze on research/rl-resource-feasibility-001;
no environment/baseline tuning, training, holdout, promotion or customer enforcement.

Feasibility001 completed600/18,000 within budget atbed44ff2, sealed evidence retained.
No eligible attack-family arm; training NO-GO. Final PR CI/review pending.

RL-CMDP-010 read-only product-thesis audit: archived capacity bounds do not settle
physical or existing-action safe feasibility. Recommend deprioritize RL as near-term
product dependency; non-RL evidence-to-regression milestone proposed, not built.
No old evidence, weights, shared contracts or customer controls changed.

Phase7B research-only first responder: fixture adapter/proposal boundary and Grok
contract proposal in research/rl-first-responder-orchestration. No production
interfaces, incidents, workers, controls or checkpoints changed; agreement pending.

Phase 7B PR #437: 242 research regressions, 91 shared tests, strict adapter mypy
and scoped Ruff pass. Verdict CONDITIONAL GO; no learning yet. Grok/shared-owner
contract agreement remains pending; no production integration. CI/review pending.

Phase 7B specification delivered in PR #437. CI at 157be64: 569 RL plus existing
20k smoke/reload, 724 backend /14 skips, 4 integration, docs/secrets passed. Three
CodeRabbit documentation clarifications addressed; no adapter semantics changed.
Final head must pass review/CI before research-only merge; then stop.

Customer onboarding (feat/alan/customer-onboarding, contracts 0.24.0 additive,
no migration): GET /projects/{id}/onboarding computes the 11-step journey from
existing tables; POST .../onboarding/test-event sends a synthetic spike through
the app's real owned_staging ingest (owner, rate-limited, never executes);
POST /repositories/{id}/disconnect revokes App grant + mappings, idempotent.
Wizard page + transport methods + 10 backend tests + 2 Playwright specs.
