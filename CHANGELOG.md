# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions will follow
[Semantic Versioning](https://semver.org/) once releases begin.

## [Unreleased]

- Add `deploy/release_readiness.py`: a one-command full-stack release-readiness rehearsal that boots the real API/worker/migrations on an embedded Postgres scratch DB under the staging profile and writes a GO/NO-GO report to `deploy/reports/` — executed proof of preflight gates, smoke checks, job execution, alert-webhook delivery with secret redaction, worker crash recovery (SIGKILL → lease expiry → reclaim), pg_dump/pg_restore round-trip, CSRF and owner-only authorization, emergency-stop terminal state, off-mode default, artifact round-trip, and `release_check.py`. Ships `deploy/readiness_worker.py` (probe worker) and fixes `.gitignore` swallowing `.env.staging.example`.

- Add pilot deployment and release-operations tooling: startup configuration validation (`hackatl-preflight`, wired into `deploy/railway/start.sh` before migrations), Alembic head preflight, structured JSON/text logging with secret redaction (`LOG_FORMAT`), token-gated `GET /api/v1/ops/status` (queue depth, worker leases, migration head, first-responder default mode), critical-failure alert webhook (`ALERT_WEBHOOK_URL`), allowlist feature flags (`FEATURE_FLAGS`), Postgres backup/restore scripts and a restore-confirmation gate, compose resource limits for api/worker, a staging env template, a curl smoke suite, an integration-branch builder for validating open PRs together, and a `deploy/release_check.py` release checklist runner. Docs: `docs/deploy/PILOT_OPERATIONS.md`, `docs/deploy/RELEASE_CHECKLIST.md`.

- Scout specialist now trains on the labelled commit-eval set (each case's planted edit vs. its original code as a minimal positive/negative pair) and was promoted after beating the static-pattern baseline (macro F1 0.349 vs 0.084); new checkpoint committed under `specialists/checkpoints/scout/`.

- Assessment UI: agents the orchestrator skipped now stay visible as 'Not needed' with the recorded reason (e.g. 'no confirmed findings'); on failed/cancelled runs they show 'Not reached'. Replaces the ambiguous 'not run' label on the relay and team list.

- Fix the periodic stale-sandbox sweep to delete the orphaned stop-snapshots of leaked `ari-sbx-*` run boxes, freeing the Vercel Sandbox snapshot quota that caused HTTP 402 assessment failures; the shared base snapshot is unaffected.
- Add Live Defense Phase 7A deterministic first-responder orchestration: server-enforced OFF / MONITOR|SHADOW / ASSISTED RESPONSE (no AUTO-EXECUTE), seven-agent capability registry from real AgentSpecs, runtime↔code correlation only with an owner-attested deploy mapping, analyst handoff that is not delivered without provider confirmation, and versioned decision records for future RL. Patch stays worktree-only; Probe still requires AuthorizedScope; independently authorized controls keep their own expiry/rollback/kill-switch when first-responder is switched OFF. Contract schema 0.21.0 (0.20.0 left for open PR #412). Evidence is never `verified` from Verify eligibility; deploy mappings are resolved against the telemetry window; OFF serializes with dispatch and cancels in-flight first-responder steps. Eval: `docs/LIVE_DEFENSE_PHASE7A_FIRST_RESPONDER.md`.

- Add fixture-only first-responder research observations, proposal masks, source
  inventory and evaluation-readiness specification; no learning or production integration.

- Added read-only capacity/oracle and product-thesis assessment: current evidence does not justify RL as a primary near-term dependency; no training or control changes.

- Completed owner-approved resource-exhaustion feasibility001 (600 episodes), retaining all violations and raw evidence; training remains NO-GO.

- Add an isolated finite-resource service research model with observable queue/work accounting, bounded route/source controls and deterministic baseline rules; formal study and training remain approval-gated.

- Add an adaptive-defense research readiness assessment, causal observation prototype and read-only training-archive coverage inventory. Verdict: no new RL training; no measured residual adaptive task or control benefit is established.

- Add a research-only versioned payment authorization provider with explicit owner/delegated/unauthorized/unknown states, trusted payments-lite lookup and documented causal RL observation rules.

- Add an offline, read-only audit of retained finite-fixture evidence that distinguishes verified numerical counts from unknown timing/authority and explicitly keeps training/promotion readiness false.
- Audit Live Defense Phase 4+5 on current main (PRs #393 and #395 merged): prove recommendation-only shadow ingest/decision/review/report cannot execute even with an owned-test grant and loopback channel present; deny authorize/execute after disconnect; serialize disconnect behind the execute application-row lock; count assessment-bound jobs in project queue depth; keep recovered `last_error` rows on the pilot trail without calling them healthy. No Phase 7, no production grants, no RL training. Eval: `docs/LIVE_DEFENSE_PHASE6_READINESS.md`. Customer procedure: `docs/LIVE_DEFENSE_SHADOW_PILOT.md`.

- Harden the opt-in research payment-read wrapper against wrong-object responses, malformed/error payloads and contradictory fixture identities; emit only validated payment JSON. No serving registration or model change.

- Document the boundary of live policy enforcement (isolated lab only; platform inference stays recommendations-only) and the known live-loop gaps in ADR-0022 (proposed, RL owner review); no behaviour change.
- Harden Live Defense Phase 4 owned-test enforcement: exact-source block allowlist is only `198.51.100.66` in both the authorization gate and the adapter; idempotent control retries cannot change kind/target/duration/rate; `increase_logging` restores the prior log level on expiry, rollback, kill-switch, and restart; `x-ari-source` is documented as a fixture-only probe header, not production attribution. Still owned-test only. PR #393 merged as `57e0326`.

- Extend Live Defense to Phase 4 controlled enforcement on an **owned test environment** only: separate `LiveControl` lifecycle (not NIST incident statuses), 15-check deterministic authorization gate, HMAC loopback adapter for four minimal actions (request-scoped rate limit, exact-source block, increase logging, escalate to human), verification with real HTTP probes, app-local TTL, emergency stop, and a Controls dashboard. Analyst accept remains non-executing. RL cannot execute without independent authorization. Production, isolation, and autonomous enforcement are out of scope. Contract schema 0.18.0. Eval: `docs/LIVE_DEFENSE_PHASE4_EVAL.md`.
- Fix delivery modes (ADR-0021): verified fixes are auto-committed to the default branch by default (falls back to a PR if the branch moved or is protected); `pull_request` mode opens a PR for the team; `manual` keeps human approval. Needs GitHub App Contents and Pull requests: Read & write.

- Default repository AI automation and project 24/7 defense OFF; migration 0014 pauses existing automatic work until explicit owner opt-in. Manual runs and historical results remain available.
- Add Live Defense Phase 5 customer **shadow-pilot readiness**: staging-app disconnect, ingest-token rotate, shadow-verify from recorded ingest, worker/telemetry/policy health stay unknown without evidence, exportable pilot report (HTTP 200 ≠ attack success; no “shadow prevented breaches”). No customer production enforcement, no autonomous RL, no browser-callable block/isolate/rate-limit. Contract schema 0.18.0 additive. Eval: `docs/LIVE_DEFENSE_PHASE5_EVAL.md`.
- Add an opt-in research wrapper over owned payments-lite reads with store-derived identity/ownership, exact request binding, serialized permission changes and buffered freshness checks. No production or RL integration.
- Add a research-only deterministic payment-read guard and preregistered finite-case feasibility comparison with owner-selected zero unauthorized-read / zero legitimate-denial limits. No serving integration or model training.

- Add isolated Phase1 constrained-RL research: audited action semantics, versioned measured reward/cost adapters, explicit unknown/provenance validation, capability masks and reproducible fixed-baseline evidence.140owned-lab episodes completed; Phase2 remains no-go. No new model training, promotion, customer execution or recommendation behavior change.
- Extend Live Defense to Phase 3 shadow runtime: authenticated `owned_staging` ingest, deterministic rules, genuine `LivePolicyDecision` rows (NOT EXECUTED) from rules or validated frozen-v6 inference, and analyst review of those records. Q-values are persisted only when inference actually ran. Authenticated runtime events no longer force simulated incident labels. No autonomous enforcement. Contract schema 0.17.0. Pilot eval: `docs/LIVE_DEFENSE_PHASE3_PILOT_EVAL.md`.

- Extend Live Defense to a shadow-pilot Phase 2: wire events, NIST incidents, stored recommendations, explanations and reassessment; add staging-app onboarding, ingest-token metadata (secret shown once), recorded health/topology (status always unknown), and analyst accept/reject/needs-investigation that does not execute. No live policy-decision producer, no production enforcement, no autonomous block/isolate/rate-limit. Contract schema 0.16.0.

- Add a customer Live Defense dashboard (Overview, Investigation, Explainability) on `/projects/{id}/live-defense`, using existing monitoring, incident and simulation-recommendation APIs. Missing live-graph/inference endpoints stay empty. Isolated sample layout (`?preview=layout`, `/live-defense-pilot`) is labelled and never mixed with records. No apply/isolate/rate-limit actions.

- Add a bounded train/validation-only duration screen that reuses existing block policies and compares one fixed duration-aware heuristic with actual v6. Completed all520 episodes: validation availability gain was uncertain and failed the fixed screen; no v11 prepared or launched. No learned-policy, control or serving changes; no holdout execution.

- Add per-repository Automatic runs ON/OFF and manual commit runs when OFF. Queue excess commits under project quotas, require authorized push sender plus commit attribution, and enforce Claude Haiku 5.5 in production. Contract 0.15.0 and migration 0009.

- Automatically queue every eligible pushed commit, with verified GitHub author rollout (Railway defaults to `adamissac`), per-commit deduplication and resume after scope approval.

- Diagnose v10 availability failures from saved evidence and add an opt-in validation availability screen that stops before holdout execution. Retain targeted access/control regressions; pause further long training pending an evidence-backed policy improvement.

- Complete the preregistered two-arm v10 RL study: ten runs and33,000 held-out episodes, full checkpoint/trajectory evidence and seed-level comparisons. Balancing improves mean return but every candidate rejects; primary hypothesis unsupported and v6 remains deployed.

- Preserve RL aggregate source provenance and validate complete seed gates. Validate all evidence before staged writes, with rollback on I/O failure; recheck tracked source bytes for every training job.

- Add opt-in action-balanced demonstration sampling for the RL lab, saved-v9 failure diagnostics, and a fresh preregistered two-arm v10 study. Default replay is unchanged; the completed v10 study is reported above and v6 remains deployed.

- Tighten RL study source/teacher-budget/scenario validation, pin fixed references, and verify recorded trajectories during initial evidence packing. Historical v9 measurements are unchanged.

- Complete preregistered five-seed v9 RL lab study with 16,500 held-out episodes, full compressed evidence and comparison figure. Hypothesis unsupported and every seed rejected; v6 remains deployed. Record compromise/availability tradeoffs and the persistent-attack failure trace.

- Add read-only five-seed study evidence verification, metric/trajectory hash pins, explicit secondary diagnostic thresholds and optional scientific figures. Reject aggregation across different training budgets, source commits or reference checkpoints. Reporting tooling does not change the frozen learning experiment.

- Add opt-in seeded random-valid baseline and compressed raw holdout trajectories to the live online trainer, plus five-training-seed aggregation and preregistered v9 quiet-validation experiment. No policy promotion.

- RL lab continuation: add `online --registry PATH` to gate against the verified incumbent;
  reject stale-baseline promotion reports, changed/unpinned registered reports and absent
  or failed checks. Existing registry reports need explicit re-registration. No promotion.
- Document the repeatable `--reference-checkpoint NAME=PATH` option and reserve the legacy
  `reference` name for the first comparison to keep report aliases consistent.

- Polish the shared ARI interface using Kairo’s restrained grids and technical typography in ARI’s warm charcoal/ember palette. Add consistent workspace headers, numbered organization cards, clearer repository rows, quieter panels and a precise replay frame. Preserve the original hero and scroll choreography; keep Projects highlighted throughout the organization/repository/commit flow.

- Keep organization/repository/commit navigation scoped to the selected repository and SHA. Real commit runs stay inline in a two-tab reviewer/seven-agent workspace with demo-style agent rail and recorded replay; no redirect to shared project history. Resolve the repository’s actual project, search older run pages, reject mismatched results and retain scoped remediation approvals. Optional narrated quick reads are explicitly requested and separate from executed records.

- Simplify project navigation to Code review and 7 agents. Move settings, scopes, approvals and other records into More; lead with run/replay and starting a check; disclose findings, repositories and progress on demand. Keep all routes and approval requirements.

- Add staggered proof-card/footer entrances and subtle button, workflow-chip, process-evidence and footer-link motion. Reduce hero ants from 10–34 to 6–16. Honor reduced motion; preserve existing scroll traversal.

- Refine the landing headline with tighter two-line rhythm, a stronger cream first line and a lighter warm-ivory second line. Preserve wording, centered layout, pointer spotlight and scroll animations.

- Footer correction: use a conventional aligned footer with Ari branding/tagline on the left, two navigation columns on the right, and a separated copyright/back-to-top row. Stack branding above the link columns on mobile. Preserve Ari colors and restored hero/scroll animations. Validation recorded in PR.

- Add an oversized Ari footer with a grainy terracotta/cream horizon. Restore the original centered hero, scroll-driven chapter animations and wheel scrolling after owner feedback; remove the redesign's reading-distance cap and manual chapter controls.

- Contracts 0.14 (additive, ADR-0017): runtime route windows can carry pseudonymous per-principal counters (cross-tenant, delegated, own) from an operator's own gateway, and RL recommendations name the principals a block would cover. The backend's RL facts now include the lab's principal detection, so the promoted live policy can recommend a targeted block in the product. It remains a recommendation, never executed.

- Add the lab-only online DQfD training workflow (expert demonstrations plus online DDQN updates). Selected-checkpoint lineage records only its training prefix; full experiment logs remain separate, and evidence export includes reloadable checkpoint and experience bytes. Promotion refuses unsupported serving layouts and missing/hash-mismatched DQfD evidence. The historical v6 gate result is not independently reloadable because its original artifact bytes were not retained; no policy is promoted.

- Fix live-layout recommendation serving: load checkpoints without lab dependencies, explain the policy-selected action after abstention, keep Q confidence metadata ranked, and avoid inferring training origin from checkpoint layout.

- Contracts 0.13 (additive): `DefenseActionName` gains `block_principal_long` and `block_top_principal_long`, and `PlatformFacts` gains the live v3 detector fields (defaults keep old payloads valid). The schema bump also covers `block_principal` (#324) and the principal-block fields (#334). `PolicyRunner` can now serve live v3 checkpoints as lab-scoped recommendations. Inference no longer imports the live lab.

- Let project owners explicitly approve their own sandbox-only authorization, with audit recording. The commit run panel refreshes approval status automatically and no longer gets stuck on expired pending scopes.

- Slow the walkthrough zoom with a 1.5-second transform/radius transition and balanced scroll growth; reduced motion remains immediate.

- Land the first large homepage wheel gesture on the expanded walkthrough video; absorb that gesture’s momentum, allow the next scroll or Continue exploring link to skip, and keep video controls visible.

### Added
- Specialist training now augments the synthetic corpus with real public corpora (DiverseVul functions, NVD CVE descriptions, MITRE ATT&CK technique descriptions) fetched via `python -m specialists.data.fetch` into the git-ignored `specialists/specialists/data/cache/`; corpus-2 adds 5-6 code families per CWE so Diagnose/Patch labels can no longer be looked up from the CWE alone.
- Add a 24/7 defense switch to project monitoring and the Defense Lab, with light-panel theme colors, clearer push re-check wording in settings, and preserved contrast on dark headers.
- Show readable assessment state labels in the real-run panel, such as “Awaiting human approval”.
- Simplify the `/connect` drill-down and commit page: select an organization, repository and commit, then switch between Code review and 7 agents; keep real-run controls compact and reduce page copy.
- Default Anthropic model is now Claude Haiku 5.5 (`claude-haiku-5-5`) for the backend gateway, all seven security agents, the reviewer and the frontend live commit analysis, to cut API cost. `ANTHROPIC_MODEL` / `AGENT_MODEL_OVERRIDES` still override it. The server-side refusal fallback is no longer sent for Haiku models (Haiku has none); the frontend analysis prices usage by model instead of at Opus rates.
- Simplify the Defense Lab page for demo visitors: the intro collapses to one line plus the test-environment caveat, the headline numbers (compromise rate, uptime, steps to contain) lead, the per-scenario metric explorer and reward/technical-detail panels are removed, and provenance stays one click away in a collapsed footer.
- Rework the Defense Lab look again: the scoreboard is now a white section on the dark page (numbered eyebrows, ink numerals, no brand gradient), and the network map uses light node cards with dark labels so the page is no longer a wall of orange.
- Restyle the Defense Lab page: the intro and caveat now sit inside the dark hero, a monospace stat-chip strip summarizes the evaluation (held-out runs, defenders, scenarios, seeds), and the scoreboard is a "command console" panel — night surface with brand glow and faint grid, ember headline numerals, the trained defender's bar highlighted in brand gradient.
- `/projects` now opens the connected-organization drill-down (organization → repositories → commits → per-commit code review and the seven agents). The standalone project list and create-project form are gone; a project is still created implicitly when a repository is connected.
- Add a specialists live-comparison harness (Claude-only vs hybrid vs specialists-only) with n=1 inconclusive results; no performance claim.

- Refresh trusted personal GitHub App repositories live in the repo picker; organization repositories still refresh through GitHub sign-in.

- Remove the homepage closing invitation and its action cards.
- Live Probe provider test now issues NIST SP 800-115 rules of engagement before running, as the orchestrator does; without them the tools withheld parameter values and Probe correctly reported inconclusive.

- Add a connected-commit panel that requests scope authorization, starts a persisted seven-agent assessment, and links existing commit runs; relabel the separate one-call Claude analysis as a narrated quick read.

- Replace game-like language with evaluation terminology, and use synthetic/illustrative labels while preserving test-environment provenance.

- Center sign-in forms, remove the promotional side panel, and add subtle background orbits that respect reduced motion.

- Refine the homepage closing invitation with two clear action cards, and align the Defense Lab evaluation selector.

- Match Pricing to the other navigation links, including automatic surface contrast and hover styling.

- Clarify real commit metadata versus illustrative analysis, synthetic drill planning and response limits, and saved live evidence versus sample-account data. Disclose missing historical red-to-green proof, update the access-code demo runbook, and wrap long recorded-run IDs on phones.

- The demo is no longer public. `/demo` opens only after signing in on `/access` with an access code (`DEMO_ACCESS_CODE`, checked on the server; a signed httpOnly cookie, enforced by `proxy.ts`), and shows the Lumen Pay sample account with a small "Demo account · sample data" tag. Public "Demo" links were removed from the navigation, home page and pricing.

- Published a dated product-readiness audit and shared follow-up checklist (issue #244), distinguishing executed prototype capabilities, reported live evidence, hosted integration gaps and proposed work.

- Homepage navigation reveals the four secondary links at 100px with a subtle 250ms fade/slide; logo and account entry stay anchored. Hidden links are inert, phones retain Menu access, and reduced motion removes the animation. Other pages keep full navigation.

- ARI branding, a centered oversized two-line hero, transparent floating navigation with persistent phone links, and locally bundled Chakra Petch bold for the wordmark only. The logo and ordinary navigation links float without cards; text and icon contrast adapts over dark and light sections, with Pricing the sole filled CTA. The unchanged two-line hero headline has a warm off-white core and a restrained responsive ember bloom.

- Simulated demo at `/demo` (ADR-0013): GitHub-style sign-in, organization and repository pickers, recent commit history, and per commit an animated code review, seven-agent run and continuous-defense map. Every page is marked "Simulated demo"; nothing calls the backend.
- Defense Lab opens with a command center: the trained defender's recorded game on a continuous-defense map with the held-out headline scores, all from the validated bundle.

- Fix concurrent identical artifact publication and refresh ready snapshots after parser or ingestion-profile changes; preserve historical snapshots.

- Opening an incident says it records a title and report. It does not contain systems, notify outsiders, or attach evidence until linked, and new cases default to a labelled simulated drill.
- Requesting a scope says it records a pending authorization. It does not activate until another member approves it, and it does not start a check or open access outside the listed targets.
- Starting an assessment says it enqueues a scoped check. It does not merge, deploy, or prove the application is secure when it finishes.
- Project and assessment phase views say when they used a full first page of 500 assessment events, so a skipped phase may still exist later in the log.
- Cancel assessment says queued checks stop immediately and in-flight checks only get a stop request. It does not delete findings or undo recorded work, and it surfaces which outcome the API returned.
- Frontend fonts are bundled locally with their licenses, removing Google Fonts downloads from production builds and preserving the existing typography.
- Redact credential-shaped free-form citation metadata in context packs and public expectation views while preserving original authenticated history and source identities.
- Keep out-of-package relative imports unresolved instead of assigning unrelated top-level call targets; parser 0.3.1 reindexes corrected facts without relabeling historical snapshots.
- Assessment conflicts now offer explicit preparation of a separate action; ordinary network retries retain their key, and project changes get distinct keys.

- Assessment job IDs returned by the API now resolve through their persisted assessment, allowing authorized status reads while preserving tenant denial.

- Fix the development-login HTML pattern for current browser Unicode regex rules.

- Use the supplied Ari ant logo in the header, sign-in, intro and browser icon; preserve the original asset and reduced-motion behavior.
- Concurrent retries of an assessment start now return the same assessment/job instead of launching duplicate work. Retry keys cannot replay results from a different project URL.

- Indexing a commit says it records symbols, calls, entry points, and expectations. It does not run a security check or change the repository.
- Creating a project says it records a name. It does not connect a repository, approve a test environment, or start a check. Monitoring is on by default.
- The sign-in line and the projects headline no longer say every fix is already proven or that the product ships it. A fix counts only when independent tests agree.
- Connecting a repository says it records the name and default branch. It does not attach a GitHub App, approve a test environment, or start a check.
- The monitoring page leads with a count of recorded events, how many are labelled simulated, and how many simulation recommendations are stored. It says this page is not the Defense Lab.
- An assessment page lists Understand, Review, Security test, Repair, and Verify from that check's event log. Monitoring stays on the project page.
- The findings list no longer calls its open tab "Validated and beyond." Open includes inconclusive results and rejected fixes.
- An assessment page leads with one sentence for its recorded state. A finished check is not described as a secure application.
- Defense Lab replay names the highest recorded alert rate at the current step and the recorded action. It does not show the hidden attacker stage.
- A project page shows six phases of the latest check. A phase is completed only when that check's event log shows it was entered, and a verification verdict comes from the finding list.
- Defense Lab replay leads with one sentence from the recorded episode: first and last action, whether it was marked compromised, and which nodes reached impact. The metrics charts follow that replay.
- Finding detail leads with a before-and-after story taken from the recorded title, severity, validation, patch, and verification. It does not claim a fix worked without a verdict.
- Source links use the assessment scope's repository when that scope names exactly one, so a project can hold more than one repo.
- A project page leads with one next step and three counts taken from the assessments, findings, scopes and monitoring flag already loaded. It does not invent a security score.
- Finding detail shows the indexed expectation statement for each linked id, and keeps the id when that snapshot has no matching statement.

- Hosted security and functional verification retain actual regression sources as hash-pinned artifacts; incident lessons learned can cite them. Plan-bound security approvals are resolved through persisted finding/assessment records for incident eradication.

- Added canonical offline CVSS v3.1, OWASP API 2023 and NIST SP 800-115 reference catalogs with integrity checks and pinned-source refresh tooling.

- QA scripts execute the existing suites and preserve failed versus incomplete results; preflight no longer declares demo readiness from warnings.

### Fixed
- Intelligence caches now distinguish normalized repository coverage and ingestion limits; invalid prefixes cannot bypass validation through a cache hit. Historical snapshot evidence remains pinned to its original version.
- Hosted assessments with a still-pending functional approval now pause at
  `AWAITING_APPROVAL` even when security work ended early or was already
  approved. Early security exits walk skipped phases to the only legal pause
  edge (`DIAGNOSIS` → `AWAITING_APPROVAL`) instead of completing.
- Approval resume re-applies the `host.repo_id` target filter, atomically claims
  the checkpoint so two workers cannot both Patch/Verify, finishes the Verify run
  before publishing a verdict, rejects truncated pytest failure lists, treats
  already-approved functional findings as outstanding work, strips the complete
  recipe `target_dir`, and persists `patch_rejected` when functional Verify is
  not accepted.
- Functional Verify stays labelled `provider_mode="none"` (no model call). An
  escaping `build_recipe.json` `target_dir` raises `SCHEMA_INVALID`.

### Added
- Assessment page shows each runtime agent with the job it does, only the runs and tool calls that were recorded, and what is still open for the next agent.
- Finding detail shows the persisted standards view (CWE, CVSS, ATT&CK, D3FEND) as text, including when nothing was assessed.
- Canonical `CheckCategory` in `shared` (schema 0.9.0): Probe library ids
  `access_control.ownership` / `input_validation.bounds`, with published V0
  fixture aliases `authorization.ownership` / `input.bounds`. Scope matching
  treats each pair as the same category.
- Frontend: incident closure panel (link findings/verifications, closure criteria with artifact evidence, lessons learned, communications, readiness list).
- Hosted assessments persist security finding lifecycle (`hypothesis → … → hardened`),
  durable approval checkpoints (`save_checkpoint` / `load_checkpoint` so a worker
  restart can resume), and approval-gated functional repair plus independent Verify.
  Assessment usage now includes `tool_calls` and `sandbox_seconds` from host runs.
  Fake-provider only; live seven-agent path still unverified (no Anthropic key).
- Frontend console pages on existing backend APIs: **Expectations** (snapshot-pinned behavioral
  expectations with confirm/conflicting/reject decisions, optimistic `expected_revision` and
  decision history), **Incidents** (NIST SP 800-61r3 lifecycle, triage, owner, two-person
  approval gates, simulated-drill labels, timeline) and **Settings** (default and monitoring
  budgets, trigger policy). Transport gains `expectations`, `decideExpectation`,
  `expectationDecisions` and `updateProject` (PATCH).
- Exact-snapshot human intent decisions (`confirmed`/`conflicting`/`rejected`) with
  authenticated backend routes, append-only revisions and `human_input` provenance.
- Independently verified candidate-regression memory: accepted Verify results with all
  five unique checks enqueue a durable, idempotent regression join and surface through
  context packs and the shared hosted intelligence adapter. Candidate evidence only;
  never a merge/current-SHA claim.
- Security-agent findings now attach an evidence-derived `Finding.standards` view (catalog CWE, reviewed ATT&CK/D3FEND rules, CVSS v4.0 from recorded facts). Trace's optional metric dict is a provisional annotation only. Not a certification or compliance claim.
- Standards integration: pinned ATT&CK/D3FEND/CWE, deterministic CVSS 4.0, NIST SP 800-61r3 incident APIs with persisted evidence and approval gates, OWASP LLM Top 10 2025/2026 self-assessments, and frontend transport support. No certification or autonomous containment claims.
- Standalone frontend REST/SSE transport with canonical schema validation, cookie/CSRF auth, idempotency, durable event resume and real FastAPI/PostgreSQL integration tests. Page integration remains pending.
- Phase 0 repository foundation: product specification, system architecture,
  seven runtime agent contracts, RL defense design and evaluation protocol,
  shared context design, data contracts, API design, UX design, security
  boundaries, testing strategy, planning (roadmap, MVP scope, task board, backlog,
  dependencies, definition of done, handoffs), development-agent prompts, ADR
  template and proposed ADRs, research plan, GitHub templates.
- Interim proprietary `LICENSE` (all rights reserved); see `LICENSE-DECISION.md`.

### Added (rl-defense, branch `feat/adam/rl-defense`)
- `DefenseSimEnv-v0`, Double DQN training, baselines, protocol-17 evaluation and
  simulation-derived recommendation adapter; reference results in `rl-defense/results/`.

### Added (code intelligence, PR #6)
- Persistent Python intelligence at an immutable Git SHA: symbols, static call/import
  graph, route dependencies, source-cited behavioral intent, bounded context packs and
  two-version impact. SQLite and backend PostgreSQL share one payload port.
- Optional bounded Claude-gateway inference with validated citations, low confidence
  and persisted provider metadata; FakeTransport CI coverage.

### Not yet implemented
- Full cross-pillar runtime completion and production operation are not established here.

Review follow-up: retain accepted starts while history refreshes to prevent duplicate paid runs; lift newly connected repository IDs into the commit workspace; correct replay indexing; bound project lookup concurrency and tolerate unrelated project errors. Added regression coverage for these cases.
