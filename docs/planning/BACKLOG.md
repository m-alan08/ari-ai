# Backlog (post-MVP and stretch)

Owners UNASSIGNED. IDs use the `1xx` range per prefix. Stretch items may be
pulled into MVP only if MVP tasks they would compete with are `DONE`.

| ID | Title | Type | Rationale / trigger | Depends on |
|---|---|---|---|---|
| INTEL-101 | JavaScript/TypeScript extraction + Express adapter | stretch | FR-1-08; broadens demo | INTEL-003 |
| INTEL-102 | User confirm/edit expectations UI + API | stretch | FR-1-04 | INTEL-006, UI-001 |
| INTEL-103 | pgvector semantic retrieval | later | If lexical recall insufficient (ADR-0010 revisit) | INTEL-007 |
| SEC-101 | GitHub PR creation from verified patch | stretch | FR-2-08 | SEC-010, CORE-005 |
| SEC-102 | gVisor/Firecracker sandbox runtime | later | Before third-party repos (R-03) | SEC-003 |
| SEC-103 | Additional check-template categories | later | Coverage growth; each needs security review | SEC-005 |
| SEC-104 | Staging-URL scope targets with ownership verification | later | Requires new contract + legal review | CORE-007 |
| MON-101 | PR comments / commit statuses | stretch | Developer workflow | MON-003 |
| MON-102 | OpenTelemetry runtime ingestion | later | Real telemetry | MON-004 |
| MON-103 | Dependency advisory polling (OSV) | later | SCA coverage | MON-002 |
| RL-101 | Ablations (DQN vs DDQN, features, reward sensitivity) | later | Protocol 17 §Ablations | RL-004 |
| RL-102 | Replay real monitoring signals as eval observations | later | RQ-005 transfer question | RL-004, MON-004 |
| CORE-101 | Organizations RBAC (owner/member/viewer) | later | FR-P-06 | CORE-005 |
| CORE-102 | S3-compatible artifact store | later | Hosted deployment | CORE-009 |
| CORE-103 | Queue abstraction swap (Celery/Temporal) | later | R-09 trigger | CORE-006 |
| INFRA-101 | Hosted staging (Vercel + Railway free tiers) | later | Requires approval (NFR-09) | INFRA-004 |
| INFRA-102 | Dedicated sandbox host | later | Paid; requires approval + ADR | SEC-102 |
| DOCS-101 | Contributor License Agreement / DCO policy | later | Before outside contributions | DOCS-003 |
| TEST-101 | Labeled benchmark for finding precision | later | [research/EVALUATION_DATASETS](../research/EVALUATION_DATASETS.md) | TEST-005 |
| PROD-101 | Design-partner interviews (validate A-1…A-4) | later | [02](../02_PROBLEM_AND_USERS.md) | — |

- DONE (2026-10-17): Pricing navigation uses the same color, hover, and contrast handling as sibling links.


Completed: homepage invitation and evaluation selector alignment (feat/adam/home-next-step).


Completed: centered sign-in form and reduced-motion-aware background decoration.


Completed: evaluation terminology cleanup and explicit synthetic/illustrative provenance labels.


Completed: remove homepage closing invitation at owner request.


Completed: one-time homepage video wheel landing with bounded momentum absorption and explicit continuation.


Completed: slower, smoother walkthrough video zoom after large scroll input.


Completed: owner sandbox authorization flow, live approval-status refresh and expired pending scope recovery.


Completed: #359 review fixes for selected-prefix lineage, the promised online update count, self-contained evidence export, servability/evidence promotion guards and changelog. Historical v6 artifact bytes remain unavailable and are explicitly marked as such.

Completed: PR #360 review follow-up — standalone live-layout checkpoint loading, abstention-consistent explanations, Q-ranked confidence metadata and neutral CLI provenance. No checkpoint promotion.


Completed: align browser scope approval assertion with explicit sandbox-owner approval, retaining independent Bob approval and the full persisted Verify journey.


Completed: owner-requested landing design and readability update — Kairo visual study, editorial hero, persistent process controls, bounded wheel momentum and an original Ari horizon footer.

Owner correction: restore the original landing hero, scroll-driven story animations and original wheel easing; remove the reading-distance cap and manual controls. Keep the new Ari-colored footer. Branch `fix/adam/restore-landing`; validation in PR.

Footer correction: use a conventional aligned footer with Ari branding/tagline on the left, two navigation columns on the right, and a separated copyright/back-to-top row. Stack branding above the link columns on mobile. Preserve Ari colors and restored hero/scroll animations. Validation recorded in PR.

Refine the landing headline with tighter two-line rhythm, a stronger cream first line and a lighter warm-ivory second line. Preserve wording, centered layout, pointer spotlight and scroll animations. Branch `fix/adam/hero-headline`; validation in PR.

Add staggered proof-card/footer entrances and subtle button, workflow-chip, process-evidence and footer-link motion. Reduce hero ants from 10–34 to 6–16. Honor reduced motion; preserve existing scroll traversal. Branch `feat/adam/landing-motion`; validation in PR.

C-landing brief completed on `feat/adam/landing-motion` (2026-10-26): warm transform-driven hero motion, continuous ember atmosphere, eased problem chapter, translucent walkthrough/pricing surfaces, and reduced-motion fallbacks. Verification: lint, typecheck, build, 98 unit-test files / 418 tests, and 10 landing E2E tests passed.

Simplify project navigation to Code review and 7 agents. Move settings, scopes, approvals and other records into More; lead with run/replay and starting a check; disclose findings, repositories and progress on demand. Keep all routes and approval requirements. Branch `feat/adam/project-simplify`; validation in PR.

Keep organization/repository/commit navigation scoped to the selected repository and SHA. Real commit runs stay inline in a two-tab reviewer/seven-agent workspace with demo-style agent rail and recorded replay; no redirect to shared project history. Resolve the repository’s actual project, search older run pages, reject mismatched results and retain scoped remediation approvals. Optional narrated quick reads are explicitly requested and separate from executed records. Branch `fix/adam/commit-workspace`; validation in PR.


## Automatic commit assessments — 2026-10-21

IMPLEMENTED in [PR #377](https://github.com/adamissac/ari-ai/pull/377), pending merge/deployment verification: signed pushes fan out into durable per-commit jobs; GitHub API author allowlist, exact SHA, duplicate protection, no docs-only skip/coalescing loss, scope-approval resume. Railway defaults to `adamissac`; configurable for other accounts. Full runs use the normal project assessment budget (operator-capped), not the deliberately cheaper legacy monitoring budget. Shared contracts unchanged. Full-chain fixture tests use scripted model responses, not live provider evidence. Validation: 181 local backend/monitoring/security tests passed (7 optional/dependency skips); 12 final focused scheduling tests passed. Full-chain fixture covers reviewer and all seven roles with required repair approvals. All GitHub checks passed on cbc0782; checks rerun after integrating newer main documentation/RL changes. Production readiness is reachable; authenticated production worker/run verification remains outstanding.
### RL-REGISTRY-001 — v8 promotion review follow-up (Alan, 2026-10-20)

Merged in PR #375 on `feat/alan/rl-v8-continuation`. Bind reports to the current incumbent,
pin report bytes, reject missing/failed checks, add registry-backed experiment baseline,
and resolve reference-name ambiguity from PR #370. Historical v8 evidence is preserved.
Authenticated approvals and signed/schema-complete reports remain open in issue #317;
next training experiment needs preregistration and a fresh holdout.

RL-REGISTRY-001 validation: full RL suite 228 passed; Ruff lint/format and diff checks pass.

### RL-V9-001 — Quiet-validation five-seed lab experiment

Merged in PR #376 on `feat/alan/live-retrain-v9` (Alan).
Preregister val_v9/fresh holdout_v9; train seeds0–4; use actual incumbent v6; preserve
all trajectories and seed-level uncertainty. Completed all five seeds and 16,500 held-out
episodes. Hypothesis unsupported; all seeds reject, v6 unchanged. Evidence/figure in
`rl-defense/results/live-retrain-v9/`; no automatic promotion.

- [x] UI-POLISH: coherent Kairo-inspired visual system across ARI workspace and landing chapters, with responsive navigation coverage (user-authorized Adam frontend work; `feat/adam/ui-polish`).

### RL-V10-001 — Demonstration action balance (Alan, owner authorized)

MERGED in PR #378 (`fdc8e45`) on `feat/alan/v10-demo-balance`. Saved-data failure analysis, opt-in
balanced demo replay, regression tests and fresh two-arm preregistration implemented.
Five seeds per arm; no v10 training/evaluation results yet. V6 unchanged.

RL-V10-001 validation: all CI green,282 RL tests plus20k-step checkpoint smoke;
64 local focused tests;78 saved v9 artifacts verified. Full v10 execution remains
a future experiment (~6h CPU); no model promotion.


### Automatic/manual mode and Haiku-only follow-up (#377 / #380)

Implemented per-repository persisted ON/OFF, owner-only API, migration 0009 and proposed additive schema 0.15.0; manual Run shown when OFF. Added signed-sender gate and per-project start quotas; excess automatic commits are recorded as skipped and not requeued. Production/staging gateway, Railway startup and quick-read enforce Haiku 5.5. Historical evidence labels preserved. Contract review requested from Alan; full test/CI and deployment verification remain pending.

Follow-up completed on `fix/adam/project-page-limits`: authenticated GET/HEAD requests use a separate 600/minute limit, mutations retain 60/minute, manual quotas count only manual-trigger assessments, and automatic quotas still count every trigger. The shared $50/day spend cap is unchanged.
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

DONE in PR #383 (`79a21e8`) on `feat/alan/rl-targeted-diagnosis`. All ten selected checkpoints already
miss85% validation availability; rotation exposes principal-block collateral cost,
not token-rotation bypass. Added saved-data/input-pinned diagnostics and opt-in
`--require-validation-availability` rejection before any holdout policy execution.
Targeted gateway regressions preserve own/delegated access assertions and prove the
current broad-control tradeoff. No new training/holdout replay or promotion. V6 stays
deployed. No v11 launch: process guard is not proof of a better learned policy.

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


### 2026-10-21 — automation opt-in reset

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

- IMPLEMENTED; research PR #388, owner-authorized merge after CI / no Phase2 (2026-10-22): **RL-CMDP-001**, Alan (owner explicitly authorized),
  `feat/alan/constrained-rl-phase1`: audit and additive offline-first constrained-RL
  foundation. Separate measured reward/cost, provenance, capability shield and
  deterministic baseline evaluation. No Phase 2, training, holdout or serving changes.

- IMPLEMENTED / finite fixture passed; integration pending (2026-10-23), **RL-CMDP-002**, Alan, `feat/alan/deterministic-feasibility`: owner-authorized finite-case deterministic authorization feasibility; owner limits zero unauthorized successes / zero valid owner/delegated denials. Protocol and isolated guard in `docs/research/deterministic-feasibility/`. No training or production integration.

- IMPLEMENTED / CI and review pending (2026-10-23), **RL-CMDP-003**, Alan, `feat/alan/read-context-integration`: owner-authorized owned-app context binding, freshness and serialized revocation tests. No serving registration or training.
Owner requested AI automation and Defense Lab OFF until explicitly enabled. Branch `fix/adam/automation-opt-in`: schema 0.15.1 changes Repository.automatic_assessments and TriggerPolicy.enabled defaults to false; migration 0010 resets stored switches, requests cancellation of unfinished automatic assessments, and wakes deferred jobs. Worker checks cancellation before checkout. Manual runs and historical results remain intact. Focused PostgreSQL/monitoring checks: 36 passed. Full tests pending. Railway plugin is installed but no callable Railway tools/credentials are exposed in this conversation; live deployment/settings verification remains blocked on access. Alan review requested for default semantics and backend migration.

- IMPLEMENTED / CI and review pending (2026-10-23), **RL-CMDP-004**, Alan, `feat/alan/read-response-integrity`: bind buffered payment responses to the authorized resource/owner and existing schema; reject malformed/error payloads and contradictory customer identities. Twenty owned-app tests pass; no serving or training changes.

- IMPLEMENTED / CI and review pending (2026-10-23), **RL-CMDP-005**, Alan, `feat/alan/feasibility-readiness-audit`: read-only anchored original-fixture audit, independent numerical checks and explicit inconclusive training/promotion readiness. No new study or serving integration.

- IMPLEMENTED / review and CI pending (2026-10-23), **RL-CMDP-006**, Alan, `feat/alan/authorization-context-provider`: owner-authorized internal versioned provider proposal and payments-lite adapter, four authorization states, fixture clock/revision semantics and future causal RL observation requirements. No external provider, shared public contract, training or evidence changes.

- IMPLEMENTED / training NO-GO; PR #422 holds final CI/review status (2026-10-23), **RL-CMDP-007**, research owner Codex, `research/rl-adaptive-defense-readiness`: owner-requested adaptive-task CMDP/observation/action/objective/dataset/baseline audit and blocked preregistration. Pure causal adapter and fixed train-only inventory; NO-GO for new training, no collection/environment/control execution.

- IMPLEMENTED / review and CI pending; training NO-GO (2026-10-23), **RL-CMDP-008**, research owner Codex, `research/rl-resource-exhaustion-feasibility`: owner-authorized finite-resource service model and instrumentation, fixed baselines, Stage2 preparation. Formal comparison/numerical thresholds/training require separate approvals. No customer controls or promotion.

- IMPLEMENTED / PR review and CI pending **RL-CMDP-009** (Codex), `research/rl-resource-feasibility-001`: owner-approved single600-episode deterministic comparison, frozen acceptance/execution clarification; no training. Runner, analysis and integrity-unit checks implemented; formal launch pending committed source.

- IMPLEMENTED / review and CI pending **RL-CMDP-010** (Codex), `research/rl-feasibility-and-product-thesis`: authorized read-only capacity/oracle bounds, control expressiveness and product architecture assessment. No new workloads, policy execution or training. Preserve feasibility001 and v6.

- DONE (research specification; PR #437 merge gated on review/CI) **RL-CMDP-011** (Codex), `research/rl-first-responder-orchestration`: Phase7B fixture-only observations/proposals, agent capability/data audit, future evaluation and Grok integration contract proposal. No collection/training/customer enforcement; no production shared/worker/incident changes.

### Independent review automation — activation pending (2026-10-25)

Automation package implemented; local safety suite 56 passing. Manually review/merge
installation PRs, verify GitHub container CI, then configure native Codex, optional
OpenAI project key and existing Devin sessions with confirmed native hard caps.
No prompt relay is needed after activation. See
`docs/release/AUTOMATED_INDEPENDENT_REVIEW.md`; paid calls remain OFF.

Installation PRs: canonical #614; fork #9 (main), #10 (ci/upstream-main). Fork #9
controller Docker unit/layout/actionlint checks passed at abd61416; paid activation
remains OFF pending manual installation and protected credential/session setup.
