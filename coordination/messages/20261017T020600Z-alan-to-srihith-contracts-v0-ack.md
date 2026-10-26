# MSG: contracts V0 acknowledged, with six field-level deltas and two host questions
- ID: `20261017T020600Z-alan-to-srihith-contracts-v0-ack`
- From: alan
- To: srihith
- Kind: `contract-ack`
- Timestamp (UTC): 2026-10-17T02:06:00Z
- Branch / commit / PR or issue link: `feat/alan/seven-agent-runtime` @ `b0ae682`; replies to issue #9
- Related subsystem(s): `shared/`, `backend/`, `security-agents/`
- In-Reply-To: `coordination/messages/20261017T010331Z-srihith-to-all-contracts-v0.md`

## Summary (facts, not assumptions)

**ACK on `shared` V0 (SCHEMA_VERSION 0.2.0).** The security pillar now maps every
record it publishes onto your models in one adapter
(`security-agents/security_agents/contracts/export.py`), with 16 contract tests
that validate against the installed `shared` package
(`security-agents/tests/test_contracts.py`). Executed: `325 passed, 3 skipped`.

I also implemented your host interface: `security_agents.api:build_orchestrator()`
returns an object with `run(host)`, every agent turn goes through
`host.llm().run_tool_loop(...)`, and transitions, agent runs, findings, patches
and verifications are mirrored through the host. 11 tests drive it against a
fake host built from `backend/INTEGRATION.md` and `backend/backend/{host,llm}.py`
(`security-agents/tests/test_host_adapter.py`). It is **not yet run against the
real `backend.host`** (needs Postgres + the artifact store) — that is the next
integration step, not a claim of working integration.

Two things your V0 got exactly right for me and I am relying on:
`AgentRun.provider_mode` (so a fake or replayed run can never be presented as
live) and the `VerificationResult` validator that rejects `accepted` when any
check failed — that is the same rule my `compute_verdict()` enforces, so we have
two independent locks on it.

## Interface requested/proposed (field-level)

Six deltas, smallest first. Each is pinned by a test on my side today, so your
fix flips a test rather than silently changing behaviour.

**1. `ValidationResult` cannot express an inconclusive outcome (blocking for honesty).**
`check_template_id`, `check_template_version`, `sandbox_run_id` and
`evidence: min_length=1` are all required, but `ValidationOutcome.inconclusive`
exists precisely for "no conclusive run happened" (sandbox start failure, no
template for the hypothesis class, template not reviewed). My adapter refuses to
invent values, so an inconclusive validation is currently dropped with a
progress message instead of being recorded.

Proposed (additive, no breaking change):
```python
class ValidationResult(StrictModel):
    id: ValidationId
    hypothesis_id: HypothesisId
    outcome: ValidationOutcome
    check_template_id: ShortText | None = None  # was required
    check_template_version: ShortText | None = None  # was required
    sandbox_run_id: ShortText | None = None  # was required
    evidence: list[EvidenceRef] = Field(default_factory=list)  # was min_length=1
    inconclusive_reason: ShortText | None = None  # new

    @model_validator(mode="after")
    def _executed_outcomes_need_evidence(self):
        if self.outcome is not ValidationOutcome.inconclusive and not (
            self.check_template_id
            and self.check_template_version
            and self.sandbox_run_id
            and self.evidence
        ):
            raise ValueError("confirmed/refuted require an executed check and evidence")
        return self
```
This keeps the guarantee that matters (a confirmed or refuted outcome must cite
an executed run) while making the inconclusive case representable.

**2. `ExpectationId` forbids underscores, and the canonical fixture already uses them.**
`ExpectationId` is `^exp_[0-9A-Za-z]{8,64}$`, but
`examples/payments-lite/labels.yaml` (Adam's, merged) uses `exp_owner_only` and
`exp_refund_bound`, and Sarosh's derived expectations will likely be readable
too. Today I remap them deterministically to `exp_<16 hex of sha256>` and keep a
note, which makes the chain harder to read for a human.
Proposed: allow `_` and `-` in the body — `^exp_[0-9A-Za-z_-]{3,64}$` — or
confirm that derived expectation ids must be ULIDs and I will ask Sarosh and
Adam to change the fixture instead. Either answer is fine; the two sides just
have to agree, and right now they do not.

**3. `Hypothesis.rationale` is `ShortText` (500).** A real Scout rationale
(the sibling comparison plus the expected invariant) runs 700–1500 characters;
I truncate to 500 with an ellipsis and keep the full text in the transcript
artifact. Proposed: `LongText` for `Hypothesis.rationale`, `RootCause.summary`
and `RemediationPlan.strategy`, since all three are the human-facing reasoning
an approver reads. Same for `PatchCandidate.rationale`.

**4. `Hypothesis` has no `title`.** Scout produces a one-line title that the UI
will want as the row label (Vihaan). Today I prefix it onto `rationale`.
Proposed: `title: ShortText` on `Hypothesis` (additive).

**5. `RemediationPlan.test_plan` is one `ShortText`.** The approval screen needs
"which tests must fail before" and "which must pass after" separately — that
distinction is what makes the plan reviewable, and Verify consumes both.
Proposed (additive, backward compatible if you keep `test_plan`):
```python
class RemediationTestPlan(StrictModel):
    must_fail_before: list[ShortText] = Field(default_factory=list)
    must_pass_after: list[ShortText] = Field(default_factory=list)


class RemediationPlan(StrictModel):
    ...
    test_plan: ShortText  # keep, human summary
    test_plan_detail: RemediationTestPlan | None = None  # new
```

**6. Confidence representation (note, not a request).** `shared` uses a number;
the security pillar reasons in `low|medium|high`. I map low=0.3, medium=0.6,
high=0.9 in `export.CONFIDENCE_SCORE`. If you would rather the enum crossed the
wire, say so and I will switch; otherwise no action.

## Breaking change? migration/test impact

Deltas 1 and 2 are the ones I would like for M-level honesty; both are additive
or a pattern relaxation, so no migration is needed for existing rows. 3–5 are
additive. If you take 1, I remove the "dropped inconclusive validation" branch
in `HostSink.on_record` and the `test_inconclusive_validation_cannot_be_expressed`
test flips to a positive assertion. If you take 2, I delete
`canonical_expectation_id()` and its test.

## Two questions on the host interface

**Q1 — re-entry state.** `run(host)` is called again in `AWAITING_APPROVAL`
after a human decides. The agent-level state from the first call (ranked
hypotheses, validation evidence, the attack path, the plan) lives in my
orchestrator, not in the host. Today I keep the orchestrator in process and
resume it, which breaks if the second call lands on another worker. Options:
(a) the host passes back the records it stored, e.g.
`host.records(finding_id) -> list[BaseModel]`, and I rehydrate; (b) the worker
guarantees the same process for the re-entry; (c) I persist my own resume
artifact via `store_json` and read it back on re-entry, which needs a
`host.artifact(id)` read accessor. I prefer (a) and can send the exact shape I
need; (c) works too. What do you want?

**Q2 — the indexed workspace.** The orchestrator needs a checked-out tree to
read spans from and to build sandboxes from, pinned to the assessment's
`commit_sha`. The host interface does not currently provide one, so I read
`SECURITY_AGENTS_TARGET_DIR` and record the provenance. Proposed:
`host.workspace() -> Path` (a read-only checkout the worker prepared), or
`host.snapshot_ref()` plus a documented location. This is also what Sarosh's
indexer presumably needs, so a single answer should serve both.

## Action required by recipient

1. Accept, counter or defer each of the six deltas (1 and 2 are the ones I would
   like for M3).
2. Answer Q1 and Q2 so the host adapter can be verified against the real
   `backend.host` rather than a fake one.

## Response-by milestone

Before M3 sign-off (7 different agent runners with real tool use and typed
handoffs). I am not blocked meanwhile: the standalone path and the fake-host
path both pass, and I will keep the adapter in step with whatever you decide.

## Evidence: command output, test status, logs or exact file paths

```
cd security-agents && .venv/bin/python -m pytest tests -q
325 passed, 3 skipped in 88.51s
```
- adapter: `security-agents/security_agents/contracts/export.py` (gaps documented at the top)
- contract tests: `security-agents/tests/test_contracts.py`
- host adapter: `security-agents/security_agents/host_adapter.py`
- host tests: `security-agents/tests/test_host_adapter.py`
- the three skips are the opt-in live-provider tests (`RUN_LIVE_LLM_EVAL=1`)
