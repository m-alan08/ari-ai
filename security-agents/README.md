# security-agents/

Pillar 2: the seven runtime security agents (Scout, Probe, Trace, Diagnose,
Patch, Fortify, Verify), the deterministic orchestrator, the tool registry, the
scope guard, the sandbox driver and the reviewed check-template library.

Specs: [docs/07](../docs/07_SEVEN_AGENT_CYBERTEAM.md),
[docs/08](../docs/08_AGENT_ORCHESTRATION.md),
[docs/agents/](../docs/agents/AGENT_CONTRACT.md),
boundaries [docs/15](../docs/15_SECURITY_BOUNDARIES.md).

> **Runtime agents are product features.** They are not Claude Code subagents
> and not the six developers' assistants.

## What exists now

| Area | Module | Status |
|---|---|---|
| Agent loop, budgets, recording | `security_agents/runtime/` | implemented, tested |
| Seven agent specs, prompts, I/O schemas | `security_agents/agents/` | implemented, tested |
| Tool registry + capability matrix | `security_agents/tools/registry.py` | implemented, tested |
| Tool implementations | `security_agents/tools/catalog.py` | implemented, tested |
| Worktree / test runner / check runner / patch applier | `security_agents/tools/services.py` | implemented, tested |
| `scope_guard` (pure) | `security_agents/scope_guard.py` | implemented, exhaustively tested |
| Sandbox: snapshots, diffs, local dev driver | `security_agents/sandbox/` | implemented, tested |
| Sandbox: hardened Docker driver | `security_agents/sandbox/driver.py` | implemented; **six real-container tests pass in CI** (digest-pinned base; isolation, timeout cleanup, fixture suite and checks) |
| Check templates v0 (ownership, bounds) | `security_agents/checks/library.py` | implemented, executed against the owned fixture |
| Orchestrator state machine + gates | `security_agents/workflow/` | implemented, tested; `external_pending` pauses for functional approvals |
| Host adapter | `security_agents/host_adapter.py` | implemented: finding lifecycle via `set_finding_status`, durable `save_checkpoint` / `claim_checkpoint` resume, Sai's functional repair + independent Verify |
| Contracts | `security_agents/contracts/` | local runtime adapters; host boundary exports canonical `shared/` contracts |
| Code intelligence | `security_agents/intel/` | hosted Git snapshots use the shared durable intelligence service; explicit dev fixtures use the local adapter |
| LLM gateway | `security_agents/llm/` | standalone providers plus hosted `host.llm()` gateway integration |
| Trained specialists | `security_agents/specialist_bridge.py` → `../specialists/` | one trained model per agent; advisory prompt data, Scout re-rank, Verify review note; audited `specialist.inference`; no authority ([ADR-0014](../docs/decisions/0014-trained-specialist-models.md)) |

## Install and run

```bash
cd security-agents
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Run the full assessment on the owned demo target (scripted model turns, real
sandboxed execution):

```bash
cd security-agents && .venv/bin/python -m security_agents.cli --target ../examples/payments-lite --auto-approve
```

Tests:

```bash
cd security-agents && .venv/bin/python -m pytest tests -q
```

Lint and types:

```bash
cd security-agents && .venv/bin/python -m ruff check security_agents tests
```

Opt-in live provider smoke (real Claude requests, costs money, needs an
authorized key):

```bash
cd security-agents && RUN_LIVE_LLM_EVAL=1 .venv/bin/python -m pytest tests/test_live_provider.py -v
```

The local sandbox driver is a **development** driver and not a security
boundary; it refuses to start unless `SANDBOX_ALLOW_LOCAL_PROCESS=1` is set (the
tests pass `allow=True` explicitly). Use the Docker driver for anything else, and
only with a digest-pinned image.

## Environment

| Variable | Meaning |
|---|---|
| `ANTHROPIC_MODEL` | default model id (falls back to `claude-haiku-5-5`) |
| `AGENT_MODEL_OVERRIDES` | per-role override, JSON (`{"patch":"claude-opus-5-5"}`) or `scout=...,patch=...` |
| `ANTHROPIC_SERVER_FALLBACKS` | server-side refusal fallback mode; `off` disables |
| `RUN_LIVE_LLM_EVAL` | `1` enables the opt-in live tests |
| `SANDBOX_ALLOW_LOCAL_PROCESS` | `1` enables the dev driver |
| `SANDBOX_IMAGE` / `SANDBOX_TARGET_IMAGE` | digest-pinned sandbox images for the Docker driver |
| `SANDBOX_MAX_MEMORY_MB`, `SANDBOX_DEFAULT_TIMEOUT_SECONDS` | sandbox limits |

No secrets are ever placed in prompts, sandboxes or stored transcripts; the
sandbox environment is scrubbed and transcripts pass through the redactor.

## Public API

`security_agents.api` is the surface other subsystems import:

| Export | For |
|---|---|
| `run_assessment` / `Orchestrator` / `OrchestratorConfig` | backend worker hosting an assessment |
| `run_agent_step` | a worker that owns its own state and runs one step |
| `scope_check` / `ActionRequest` / `Decision` | backend scope API and any network-capable caller |
| `PatchWorktree`, `TestRunner` | **shared repair sandbox** for `code_review` (Sai) |
| `ALL_SPECS` | UI/metadata: role, version, budget, tools per agent |
| `CodeIntel` | the port `code_intelligence` should satisfy |

## How the agents are kept honest

- **Permissions are code.** `AGENT_TOOL_ALLOWLIST` is enforced by the registry
  before a handler runs; a spec cannot grant itself a tool the registry withholds.
- **Scope is checked twice.** Once at `SCOPE_CHECK`, again before every
  network-capable tool call, and the sandbox driver refuses a target network
  without an allowed decision.
- **Outcomes come from execution.** Probe's outcome is derived from the check
  template's `matches_expectation`; if the agent disagrees, the executed result
  wins and the disagreement is recorded. Verify's verdict is computed by
  `compute_verdict()` from executed records; the model only writes the summary.
- **Verify is structurally independent.** Its own `AgentRun`, a fresh checkout,
  fresh sandboxes, the diff artifact only, and no Patch transcript.
- **Model output carries no authority.** No output schema has a field that
  approves, verifies or widens scope (asserted by a test).
- **Citations are validated.** A hypothesis, cause span or recommendation citing
  a file or line range that is not in the pinned snapshot is dropped.

## Layout

```
security-agents/
  security_agents/
    api.py            # public surface
    cli.py            # run an assessment locally
    contracts/        # local V0 typed contracts (adapter)
    llm/              # gateway port, live/fake/replay providers
    runtime/          # AgentSpec + the agent loop
    agents/           # scout probe trace diagnose patch fortify verify
    tools/            # registry, catalog, execution services
    checks/           # reviewed check templates
    sandbox/          # workspace, drivers, in-sandbox entrypoint
    intel/            # code-intelligence port + local adapter
    workflow/         # state machine, gates, orchestrator
    scope_guard.py
    redact.py
  tests/
  evals/investigation/   # end-to-end Scout→Diagnose quality benchmark
```

## Investigation-quality evaluation

`evals/investigation/` measures whether the Scout → Probe → Trace → Diagnose
chain produces useful, correct, reproducible security findings. It runs the
**real orchestrator, real tools and real sandbox driver** against a synthetic
corpus of labelled cases; only the model provider is swapped.

- **Corpus** (`corpus/skeletons/` + `cases.json`): ~31 cases — planted
  vulnerabilities across CWEs (IDOR, SQLi, traversal, SSRF, shell injection,
  session expiry, cookie flags, timing-safe compare, TLS verify, bounds),
  hard negatives and lookalikes, benign changes, ambiguous/missing-evidence
  cases, three repository structures (single-file, package, flat scripts),
  multi-commit snapshots, and a noisy-telemetry variant. Ground truth lives
  only in `cases.json` — `labels.yaml` ships legit `expectations` context but
  the answer key never enters the materialized snapshot, and a regression test
  greps the recorded provider transcript for label terms.
- **Provider modes**: `fake` (deterministic scripted FakeLLM policy —
  simulated provider, honestly labelled), `live` (Anthropic via
  `live_gateway_from_env()` — requires an authorized key + budget; absent one
  it reports `unavailable`), `record` (wraps live, saves
  `provider_recording.jsonl`), `replay` (replays a recording).
- **Scorer** (`score.py`): TP/FP/FN, precision/recall/F1 at flag level (Scout
  hypothesis emitted) and resolved level (confirmed/diagnosed finding),
  findings per investigation, completion rate, p50/p95 latency, tokens/USD,
  tool calls, snapshot-sha correlation, artifact integrity, cross-repeat
  reproducibility, and per-FN failure attribution (scout_miss / rank_filtered /
  probe_refuted / probe_inconclusive / agent_error).
- **Report** (`report.py`): renders `REPORT.md` with measured tables, failure
  attribution, per-case outcomes, example successful/unsuccessful/FP
  investigations and explicit limitations.

```
cd security-agents
PYTHONPATH=.:evals/investigation:../shared:../standards \
  .venv/bin/python evals/investigation/run.py --provider fake --repeat 3 \
  --out evals/investigation/results
PYTHONPATH=.:evals/investigation:../shared:../standards \
  .venv/bin/python evals/investigation/report.py evals/investigation/results/<run_dir>
```

Latest committed fake-provider run: `evals/investigation/results/20261024T204625Z_fake/`
(31 cases × 3 repeats, 100% completion, reproducibility 31/31 identical
hypothesis sets). Fake-provider numbers measure the harness + scripted
policy, not real model quality — they are clearly labelled as simulated.
Live-provider results were unavailable (no authorized API key in the
evaluation environment). Regression tests: `tests/test_eval_investigation.py`.

## Tasks

SEC-001 … SEC-013. Status and evidence: [coordination/status/ALAN.md](../coordination/status/ALAN.md).

### Standards classifications

Producer standards fields resolve through the pinned offline `standards` catalog.
CWE proposals must identify real, nondeprecated weaknesses (categories such as
CWE-840 are not accepted as weakness classifications). CWE/CAPEC/ATT&CK catalog relationships are review candidates; they are not
automatically attached as ATT&CK mappings. These are
classifications, not evidence of production adversary activity.

Canonical `standards.mapping` validates classifications and reviewed rules.
Trace proposes the eight CVSS v3.1 base metrics, each citing a file span or the
executed validation run; uncited metrics are recorded as `unverified`. Code computes
the v3.1 vector, base score (FIRST specification with Roundup) and rating, and the
platform severity level comes from that rating (ADR-0012). This is a base score
only: it measures severity, not risk. Trace can also supply a complete set of CVSS
v4.0 base metrics backed by the executed run; that assessment is shown alongside as
provisional and never sets the level. Missing v4.0 metrics leave it unscored.

Scout's CWE must exist in the pinned MITRE catalog with mapping usage Allowed or
Allowed-with-Review; the OWASP API Security Top 10 2023 tag is derived in code from
a project mapping (`standards/standards/data/rules/cwe_owasp_api.json`), never by the
model. Trace steps may carry an ATT&CK Enterprise id and Diagnose and Fortify a
D3FEND id; each is validated against the pinned index, and "no applicable technique"
is an accepted answer.

### SARIF 2.1.0 export

`security_agents.standards.sarif.to_sarif` writes findings as a SARIF 2.1.0 log (one rule per CWE,
CVSS v3.1 base score in `security-severity`, OWASP API tag as a project mapping, ATT&CK and D3FEND in
result properties). The log is validated against the OASIS schema pinned in
`standards/standards/data/sarif-2.1.0.json` (OASIS Standard incorporating Approved Errata, 28 August
2023, notice included). The severity to SARIF level mapping is a project mapping.

### Rules of engagement

At SCOPE_CHECK the orchestrator issues a rules-of-engagement document aligned with
NIST SP 800-115 Appendix B (project mapping) from the approved scope. Every
scope-required tool call (Probe checks and Verify's re-check) is checked against it
after the scope guard: a missing, altered, stale, expired or non-matching RoE fails
closed with `SCOPE_DENIED` and a `tool.roe_denied` audit event. The host resume path
re-issues it from the reloaded scope.

```
cd security-agents
.venv/bin/python -m security_agents.standards.report   # offline table: standard, ids pinned, version, pass/fail, UNVERIFIED
.venv/bin/python -m security_agents.standards.smoke    # replay run; writes tagged finding JSON, RoE, SP 800-115 coverage and SARIF 2.1.0
```

D3FEND mappings use the owner-reviewed rules keyed by Fortify category and
confirmed check category. Unsupported combinations remain unmapped. Reviewed RL actions have simulation-derived D3FEND
classifications, while unsupported actions stay unmapped. No rule
executes a defensive action against real systems.

Model-proposed CVSS metrics are explicit provisional assumptions, even when they cite an executed run. A run identifier supplies provenance but does not prove an impact. Such scores list every assumption and require human review; missing metrics remain unscored. Incident closure requires actual regression artifact IDs/hashes; eradication requires a succeeded independent Verify run. All currently supported runtime sources are demo/replay/simulation, so correlated incidents retain simulated labeling even for executed demo logs.

Security Verify requires the same of Patch's added tests: the platform runs them on a
fresh unpatched checkout and they must fail there in a clean run, then pass after the
patch (`verify.clean_reproduction`, shared with the functional path; #248, #264).
Functional Verify requires nonempty generated regression tests that demonstrably
fail on the unpatched commit and pass after the candidate diff. Runner errors,
collection failures, timeouts, missing sandbox records and skipped generated tests
cannot establish a fix. Generated tests may not overwrite existing files. Human
approval resume reloads the current scope and worker host before execution.

## Retained regression sources

Accepted independent Verify can retain bounded test sources from its fresh workspace. Sources are captured before tests and compared afterward; unsafe, missing, symlinked, empty, changed or oversized sets produce no eligible archive. Host mode stores a redacted JSON archive with patch/verification/run IDs, checks and original source hashes, and attaches the returned artifact ID to hardening recommendations. Functional verification emits a deterministic policy-test retention recommendation. The archive associates source files with accepted verification; it does not claim every line or individual node ran. Incident evidence must use the stored artifact hash, since source redaction may change bytes.

## Docker execution evidence

[Security CI run 39033028814](https://github.com/adamissac/hackatl-agentic-security/actions/runs/39033028814)
on main commit `5b3ef50` built the sandbox image from its digest-pinned Python base
and executed `tests/test_docker_sandbox_live.py`: **6 passed**. The tests cover
non-root execution, read-only root filesystem, writable work/output mounts, kernel
network isolation, absence of host environment secrets, timeout removal, the owned
payments-lite suite, and ownership/bounds checks (including the protected lookalike).
The image tag is explicitly allowed only for this CI build; this evidence does not
provide a published image digest or a deployment attestation.

The same run's ordinary security suite passed **451 tests, with 18 skips**. These
scripted model runs and container tests do not establish a live seven-role provider
trace. Docker execution is CI evidence; no local Docker engine is claimed.


Scope guard policy follows ADR-0016: an owner-approved sandbox-only scope may have the same requester and approver, including in multi-member organizations. Empty or mixed/non-sandbox target sets do not receive the self-approval exception.
