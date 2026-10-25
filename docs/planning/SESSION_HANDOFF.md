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

### Automated independent release review setup — 2026-10-25

Automation-only installation prepared: immutable candidate/upstream pins, F1–F10/
N1–N7 strict evidence, protected signed budget ledger, workstream assignments and
bounded existing-session Devin handoff. Local controller/adversarial suite: 56
passed; Ruff clean. Fork trusted environments and ledger provisioned, API activation
OFF. Installation PRs require manual review/merge; Codex repository access and API
credentials/session budget confirmation are one-time prerequisites. Docker unavailable
locally; live loop not claimed. No production code, release merge or deployment.
See `docs/release/AUTOMATED_INDEPENDENT_REVIEW.md`.
