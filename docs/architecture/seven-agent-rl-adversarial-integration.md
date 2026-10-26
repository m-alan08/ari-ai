# Seven-Agent → RL Defender: adversarial post-training integration plan

| Field | Value |
|---|---|
| Status | **Design only — not scheduled for implementation.** See [Implementation gate](#implementation-gate). |
| Owner | RL (Adam) + Security-agents (Ari), jointly; shared contracts via Srihith |
| Related | [ADR-0014](../decisions/0014-seven-agent-rl-adversarial-post-training.md), [ADR-0008](../decisions/0008-offline-double-dqn.md), [docs/07](../07_SEVEN_AGENT_CYBERTEAM.md), [docs/08](../08_AGENT_ORCHESTRATION.md), [docs/09](../09_RL_DEFENSE_SYSTEM.md), [docs/17](../17_RL_EVALUATION_PROTOCOL.md), [docs/15](../15_SECURITY_BOUNDARIES.md), RQ-005 |

This is the "account for future integration in the architecture" deliverable. It records
*how* the existing seven-agent offensive pipeline would eventually feed the RL defender,
the **reusable schemas** that make that possible, the **exact integration points** in today's
code, and the **constraints** that keep the two systems decoupled. It is grounded in the code
as it exists on this branch, not in the specs' aspirations.

**Nothing here is wired into training or orchestration yet, and must not be.** Implementation
is gated (below).

---

## 1. What actually exists today (grounding, not assumptions)

### 1.1 The seven agents are a pipeline, not seven attackers

From [`security-agents/security_agents/agents/`](../../security-agents/security_agents/agents/)
and the state machine in
[`workflow/states.py`](../../security-agents/security_agents/workflow/states.py), the seven
runtime agents run as a single staged assessment, not as independent attackers:

| Agent | Stage (`AssessmentState`) | Produces (`contracts/models.py`) | Offensive / defensive |
|---|---|---|---|
| Scout | `RECON` | `EntryPoint`, `BehavioralExpectation`, `Hypothesis` | offensive (recon) — **not ground truth** |
| Probe | `VALIDATION` | `ValidationResult` (outcome ∈ confirmed/refuted/inconclusive) with `sandbox_run_id`, `observations` | offensive (validation) — **instrumented ground truth** |
| Trace | `ANALYSIS` | `AttackPath` (verified `PathStep`s, `preconditions`, `impact_summary`), `SeverityAssessment` | offensive (analysis) |
| Diagnose | `DIAGNOSIS` | `RootCause`, `RemediationPlan` | defensive |
| Patch | `REMEDIATION` | `PatchCandidate` | defensive |
| Fortify | `HARDENING` | `HardeningRecommendation`, `RegressionTest` | defensive |
| Verify | `REMEDIATION`/verify | `VerificationResult` | defensive (independent) |

**Consequence for RL.** Only the offensive sub-pipeline (Scout→Probe→Trace) describes "attack
scenarios," and of those only **Probe's instrumented `ValidationResult` and Trace's verified
`PathStep`s** count as attack success. Scout's `Hypothesis` and any agent's free-text
`interpretation`/`summary` fields are explicitly **non-authoritative** (see the field comments
in `models.py`: `interpretation: … # non-authoritative LLM text`). This is exactly the spec's
rule: *do not use the agents' textual descriptions as ground-truth attack success.*

### 1.2 The RL environment is a fixed 5-node abstraction

From [`rl-defense/rl_defense/envs/`](../../rl-defense/rl_defense/envs/):

- `DefenseSimEnv-v0` has a **fixed topology**: `NODE_NAMES = ("gateway","api","auth","payments","db")`.
- The adversary is an **abstract stage model** (`none→foothold→escalated→impact`), not a model
  of real techniques. The hidden stage never enters the observation.
- A scenario is a `ScenarioConfig` (versioned, `config_hash()`-ed) parameterizing adversary
  transition probabilities, signal noise, defense effects, and reward weights.
- Defender observations already come from a typed bridge:
  [`observations.from_platform_facts(PlatformFacts)`](../../rl-defense/rl_defense/observations.py)
  → the `OBS_DIM` vector, with `mask_from_facts` for the valid-action mask.
- `PlatformFacts` / `NodeSignals` are **canonical shared contracts** (`shared.rl`), re-exported
  by `rl_defense.contracts`. The RL side does not own them.

**Consequence for RL.** A confirmed finding is **not** an RL episode. An attack path over real
code must be *projected* onto the 5-node abstraction as a `ScenarioConfig` plus seed
`PlatformFacts`. That projection is a hand-written, lossy mapping today (documented honestly in
`observations.py` and the RL README's "Limits"), and the integration must not pretend otherwise.

### 1.3 Safety is already deterministic code

- [`scope_guard.check()`](../../security-agents/security_agents/scope_guard.py) is pure,
  fails closed, and gates every network-capable tool. Only `PROBE` and `VERIFY` may touch a
  target; model output reaches it only as already-validated typed args.
- Sandboxes default to no network ([ADR-0006](../decisions/0006-docker-sandbox-no-network-default.md)).
- The RL system has **no code path from a recommendation to a real action** (RL README, docs/09),
  and training is **offline** ([ADR-0008](../decisions/0008-offline-double-dqn.md)).

The integration must preserve all of these. It adds no new authority.

---

## 2. Target architecture (the eventual integration)

```
 Seven-agent assessment (authorized scope only)
   Scout ─▶ Probe ─▶ Trace ──────────────────────────────┐
                                                          │ (persist typed artifacts; no RL import)
                                                          ▼
                                        ┌─────────────────────────────────┐
                                        │  OffensiveScenario extractor      │  (new, offensive side)
                                        │  reads ValidationResult+AttackPath│
                                        │  from the assessment store        │
                                        └─────────────────────────────────┘
                                                          │  OffensiveScenario (versioned artifact)
                                                          ▼
                                        ┌─────────────────────────────────┐
                                        │  Scenario projector               │  (new, RL side)
                                        │  OffensiveScenario → EpisodeSpec   │
                                        │  (ScenarioConfig + seed facts)     │
                                        └─────────────────────────────────┘
                                                          │  EpisodeSpec (train/val/holdout split)
                                                          ▼
   Disposable isolated training env  ◀───────────────────┘
   (DefenseSimEnv replay of the projected scenario)
                                                          │  Transition / Experience records
                                                          ▼
                                        ExperienceDataset (versioned, offline)
                                                          │
                                                          ▼
                     Offline retrain ─▶ candidate checkpoint ─▶ held-out eval ─▶ promotion gate ─▶ (human) promote
```

Two hard boundaries, both required by [ADR-0014](../decisions/0014-seven-agent-rl-adversarial-post-training.md):

1. **The orchestrator never imports the RL package and never calls training.** It only persists
   its normal typed artifacts. Extraction reads the store after the assessment completes.
2. **Training never updates a deployed policy.** It writes a *candidate* checkpoint; promotion is
   a separate, human-gated step.

---

## 3. Reusable schemas (proposed; not yet committed to `shared/`)

These are the "reusable episode and experience schemas" the spec asks us to *design now*. They
are presented as proposals. **They must land in `shared/` (owner: Srihith) via the contract-first
process, not be unilaterally added by the RL or offensive package.** Marked `PROPOSED` so a reader
never mistakes them for shipped contracts.

### 3.1 `OffensiveScenario` — the offensive→RL hand-off artifact

The structured interface the spec's Stage 1 requires. One per usable, instrumented finding/path.
Deliberately captures every field Stage 1 enumerates:

```python
# PROPOSED shared.rl_adversarial (not implemented)
class OffensiveScenario(StrictModel):
    id: ScenarioId
    source_assessment_id: str  # provenance back to the AgentRun chain
    schema_version: str

    # -- what the offensive pipeline actually proved --
    attack_category: str  # CWE/CAPEC from Hypothesis/Trace (classification, not proof)
    validated_finding_ids: list[str]  # ValidationResult ids with outcome == CONFIRMED
    attack_path_id: str | None  # Trace AttackPath id (verified steps only)
    instrumented: bool  # True ONLY if backed by sandbox_run_id observations

    # -- Stage 1 required fields --
    application_conditions: dict[str, object]  # relevant app state/config at reproduction
    preconditions: list[str]  # from AttackPath.preconditions
    affected_assets: list[str]  # mapped toward NODE_NAMES (see §4)
    observable_telemetry: dict[str, object]  # measured signals, from Probe observations
    success_criteria: SuccessCriteria  # measurable, instrument-derived (see below)
    available_defensive_actions: list[str]  # subset of DefenseSimEnv actions
    measurable_outcomes: list[str]  # what instrumentation reports as mitigation

    # -- safety --
    authorized_scope_id: str  # the AuthorizedScope this was produced under
    authorization_expires_at: datetime
    reproducible_in_disposable_env: bool


class SuccessCriteria(StrictModel):
    source: Literal["sandbox_instrumentation", "verified_attack_path"]  # never "agent_text"
    sandbox_run_id: str | None
    observed_signal: str  # the concrete instrument reading that defines success
    threshold: float | str
```

**Rule encoded in the type:** `success_criteria.source` cannot be an LLM text field, and
`instrumented=False` scenarios are extraction candidates only — they may not enter any
training/eval split.

### 3.2 `EpisodeSpec` — the RL-side projection

The RL side owns the lossy projection onto `DefenseSimEnv`. It is explicit and auditable:

```python
# PROPOSED shared.rl_adversarial (not implemented)
class EpisodeSpec(StrictModel):
    id: EpisodeId
    offensive_scenario_id: ScenarioId
    env_id: Literal["DefenseSimEnv-v0"]  # pinned; bump on env change
    scenario_config: ScenarioConfig  # the projected simulator parameters
    scenario_config_hash: str  # ScenarioConfig.config_hash()
    seed_facts: PlatformFacts  # initial observable state
    split: Literal["train", "val", "holdout"]  # assigned ONCE, by scenario id hash
    projection_notes: str  # documents what the 5-node mapping dropped
    difficulty_tier: int  # curriculum rung (§5)
```

`split` is assigned deterministically from `offensive_scenario_id` so the **same scenario can
never appear in two splits** (spec Stage 3: separate train/val/holdout; never eval on training
scenarios).

### 3.3 `Transition` / `Experience` — the offline dataset

Matches what `DefenseSimEnv.step` and the DDQN replay buffer already consume (obs vector,
discrete action, reward, mask):

```python
# PROPOSED shared.rl_adversarial (not implemented)
class Transition(StrictModel):
    obs: list[float]  # OBS_DIM
    action: int  # 0..N_ACTIONS-1, DefenseSimEnv layout
    reward: float  # from REWARD_COMPONENTS, measured in-sim
    next_obs: list[float]
    done: bool
    action_mask: list[bool]  # mask_from_facts at this step
    reward_breakdown: dict[str, float]  # per-component, for reward-hacking audits


class EpisodeRecord(StrictModel):
    episode_spec_id: EpisodeId
    env_id: Literal["DefenseSimEnv-v0"]
    policy_ref: str  # which policy generated it (for off-policy bookkeeping)
    seed: int
    transitions: list[Transition]


class ExperienceDataset(StrictModel):
    id: DatasetId
    schema_version: str
    env_id: Literal["DefenseSimEnv-v0"]
    episode_specs: list[EpisodeId]  # provenance; split membership
    episodes: list[EpisodeRecord]
    created_at: datetime
    source_scope_ids: list[str]  # all authorizations that contributed
```

### 3.4 `PromotionReport` — the Stage 4 gate

```python
# PROPOSED shared.rl_adversarial (not implemented)
class PromotionReport(StrictModel):
    candidate_checkpoint: RLCheckpointRef
    baseline_checkpoint: RLCheckpointRef  # currently deployed
    holdout_dataset_id: DatasetId
    mitigation_effectiveness: MetricCI
    false_positive_rate: MetricCI
    action_cost: MetricCI
    availability_impact: MetricCI
    regression_check: Literal["pass", "fail"]
    reward_hacking_check: Literal["pass", "fail"]
    meets_acceptance_criteria: bool
    decided_by: str | None  # human user id; never an agent
    simulation_caveat: str  # SIMULATION_CAVEAT_DEFAULT
```

All metrics reuse the RL side's existing `MetricCI` style ([`lab_export.py`](../../rl-defense/rl_defense/lab_export.py)).
Every report carries the simulation caveat — these numbers are simulator-derived (RQ-005).

---

## 4. Integration points in today's code (where the seams go)

| Seam | Location today | What the integration adds (later) |
|---|---|---|
| Extract confirmed findings | assessment artifact store; `ValidationResult`, `AttackPath` in [`contracts/models.py`](../../security-agents/security_agents/contracts/models.py) | A read-only extractor on the offensive side emitting `OffensiveScenario`. No orchestrator change. |
| Asset → node mapping | `NODE_NAMES` in [`envs/config.py`](../../rl-defense/rl_defense/envs/config.py); `from_platform_facts` in [`observations.py`](../../rl-defense/rl_defense/observations.py) | A `projector` module mapping `affected_assets` onto the 5 nodes, producing `ScenarioConfig` + seed `PlatformFacts`, recording the lossy mapping in `projection_notes`. |
| Episode replay | `DefenseSimEnv` + `evaluate.run_episode` | Replay the projected `ScenarioConfig` to emit `EpisodeRecord`s. Reuses the existing env; no new env unless topology must grow (then bump `env_version`). |
| Dataset + retrain | [`train.py`](../../rl-defense/rl_defense/train.py), `agents/ddqn.py` (`ReplayBuffer`) | Load `ExperienceDataset` for **offline** retrain into a *candidate* checkpoint. `train.py` already isolates eval seeds from train seeds. |
| Held-out eval + promote | [`evaluate.py`](../../rl-defense/rl_defense/evaluate.py), `checkpoint.py`, docs/17 protocol | Produce `PromotionReport`; promotion is a separate human-gated step, never automatic. |
| Authorization | [`scope_guard.py`](../../security-agents/security_agents/scope_guard.py), `AuthorizedScope`, `Approval` | `OffensiveScenario` carries `authorized_scope_id`; extraction refuses scenarios from expired/revoked scopes. No new authority path. |
| Shared contracts | `shared.rl` (`PlatformFacts`, `RLCheckpointRef`, …) | New §3 schemas land in `shared/` via contract-first (Srihith), consumed by both sides. |

---

## 5. Curriculum (Stage 3), honestly scoped

A `difficulty_tier` on `EpisodeSpec` orders scenarios from simple, single-node, single-stage
reproductions up through multi-node lateral paths, altered timing, and combined behaviors.
Because every projected scenario still runs inside the abstract `DefenseSimEnv`, the curriculum
increases **simulator** difficulty, not real-world fidelity. Train/val/holdout membership is fixed
per scenario id so difficulty progression never leaks a holdout scenario into training.

The research value the spec is after (does adversary-generated scenario distribution expose
weaknesses that hand-written `ScenarioConfig`s miss?) is answerable only by comparing, on the
**same held-out set**, a defender trained with seven-agent-derived episodes against (a) the
static threshold-rule baseline and (b) a defender trained only on hand-written configs. The RL
side already has those baselines (`baselines/policies.py`).

---

## 6. Non-negotiable constraints (carried from docs/15 and the spec)

1. Authorized, owned targets only. Every `OffensiveScenario` is bound to a live `AuthorizedScope`;
   extraction fails closed when it is expired or revoked.
2. Instrumented ground truth only. Attack success comes from sandbox instrumentation
   (`ValidationResult.observations` + `sandbox_run_id`) or verified `AttackPath` steps — never
   from any LLM text field.
3. Offline training only. No online learning on live signals; no auto-update of a deployed policy
   during an incident. Promotion is human-gated and reversible (keep versions + rollback).
4. Decoupled. Orchestrator ⇎ training share only persisted typed artifacts; neither imports the other.
5. Simulation caveat on every metric. These results bound claims to the simulator (RQ-005).
6. Reproducible splits. One scenario → one split, by id hash. Never train and evaluate on the same scenario.

---

## Implementation gate

Per the spec's own Implementation Priority, **do not begin building this** until the standalone
RL Defense Lab is functional, measurable, and tested end-to-end (live telemetry → inference →
action execution → reward → training). Today the RL system is offline-simulator-only with no live
telemetry or action-execution path; that prerequisite is unmet.

**Build order when the gate opens:**
1. Ratify §3 schemas into `shared/` (contract-first, with Srihith).
2. Offensive-side `OffensiveScenario` extractor (read-only over the artifact store).
3. RL-side `projector` + `EpisodeSpec` split assignment.
4. Episode replay → `ExperienceDataset`.
5. Offline retrain → candidate checkpoint.
6. Held-out eval → `PromotionReport` → human promotion.
7. Only then, the Stage 5 asynchronous versioned cycle — never an uncontrolled loop over a live model.

Until step 1, this document is the whole deliverable.
