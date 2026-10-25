# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions will follow
[Semantic Versioning](https://semver.org/) once releases begin.

## [Unreleased]

- Added opt-in bounded exact-SHA release review automation with isolated strict
  regressions, signed evidence, existing-owner routing and a protected cost ledger.
  No automatic merge, deployment or customer enforcement.

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
