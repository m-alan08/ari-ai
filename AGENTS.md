# AGENTS.md — operational reference for all coding agents

This file is the authoritative operating manual for **any** development agent
(Claude Code, Codex, Cursor, or a human acting like one) working in this
repository. [CLAUDE.md](CLAUDE.md) is a short orientation that points here.
When this file and a subsystem doc disagree, raise it — do not pick silently.

---

## 1. What the product is

An autonomous, agentic software engineering and cybersecurity platform. It
connects to a repository, builds a version-aware understanding of what the
software does **and is supposed to do**, reviews it for functional correctness,
challenges it with authorized adversarial testing, repairs verified problems
with candidate patches, independently verifies those patches with executed
evidence, and keeps watching as the software changes.

Full spec: [docs/01_MASTER_PRODUCT_SPEC.md](docs/01_MASTER_PRODUCT_SPEC.md).
Dense single-document context: [docs/prompts/00_MASTER_ENGINEERING_PROMPT.md](docs/prompts/00_MASTER_ENGINEERING_PROMPT.md).

## 2. The three pillars

| Pillar | Question | Primary dirs |
|---|---|---|
| **P1 Code intelligence, review & repair** | Does it work as intended? | `code-intelligence/`, `code-review/` |
| **P2 Seven-agent cybersecurity team** | Can an attacker exploit it? | `security-agents/` |
| **P3 Continuous monitoring & RL defense** | How do we keep it secure as it changes and runs? | `monitoring/`, `rl-defense/` |

## 3. The differentiator (why the pillars must stay connected)

Our hypothesis is **not** "we use AI" or "we have agents". It is: *a shared,
version-aware intelligence layer that connects intended behavior, functional
verification, adversarial validation, evidence-backed remediation, and
continuous monitoring.* Concretely this means:

- Every subsystem reads from and writes to the **Unified Intelligence Layer**
  ([docs/11](docs/11_SHARED_CONTEXT_AND_MEMORY.md)). No subsystem re-indexes the
  repo for itself.
- A behavioral expectation discovered by P1 (e.g. "a customer can only read their
  own payments") is a first-class object that P2 uses to judge authorization
  findings and P3 uses to interpret anomalies.
- A verified fix produces a regression test that becomes part of future
  assessments.

If a change you are making would cause a pillar to stop sharing context, stop and
escalate.

## 4. Architecture summary

Four planes ([docs/04](docs/04_SYSTEM_ARCHITECTURE.md)):

- **Control plane** — FastAPI backend, auth, projects, approvals, orchestrator
  state machine, job scheduling, audit log.
- **Data plane** — PostgreSQL (state, intelligence graph, evidence index), object
  storage (artifacts: diffs, logs, reports, checkpoints).
- **Agent execution plane** — workers that run agent steps and tools; all untrusted
  code runs in **ephemeral, network-restricted sandbox containers**, never on the
  worker host.
- **RL simulation environment** — offline Gymnasium env + training/eval jobs;
  produces checkpoints and recommendations only. It never acts on real systems.

## 5. The seven runtime cybersecurity agents

Product features, specified in [docs/agents/](docs/agents/AGENT_CONTRACT.md).
Each has its own typed input/output schema, tool allowlist, permission set,
budget, handoff conditions, and success criteria.

| # | Agent | Spec | Can execute code? | Can write code? |
|---|---|---|---|---|
| 1 | Scout | [RED_SCOUT](docs/agents/RED_SCOUT.md) | No (read-only analysis + static tools) | No |
| 2 | Probe | [RED_PROBE](docs/agents/RED_PROBE.md) | Yes, only in a scoped sandbox | Test harnesses only |
| 3 | Trace | [RED_TRACE](docs/agents/RED_TRACE.md) | No | No |
| 4 | Diagnose | [BLUE_DIAGNOSE](docs/agents/BLUE_DIAGNOSE.md) | No (may read Probe evidence) | No |
| 5 | Patch | [BLUE_PATCH](docs/agents/BLUE_PATCH.md) | Yes, tests in its worktree sandbox | Yes, in an isolated worktree only |
| 6 | Fortify | [BLUE_FORTIFY](docs/agents/BLUE_FORTIFY.md) | Static tools only | Proposed tests/config as suggestions |
| 7 | Verify | [INDEPENDENT_VERIFY](docs/agents/INDEPENDENT_VERIFY.md) | Yes, fresh sandbox | No — read-only on the patch |

**Rules for implementing them:**
- Never implement "seven agents" as one prompt with seven personas.
- The orchestrator is a deterministic state machine
  ([docs/08](docs/08_AGENT_ORCHESTRATION.md)), not a free-form LLM chat between agents.
- Verify must not share mutable state, conversation history, or sandbox with Patch.
- LLM outputs are validated against Pydantic schemas; invalid output is a failed
  step, not something to "best-effort" parse.

## 6. Directory responsibilities

| Dir | Owns | Must not |
|---|---|---|
| `frontend/` | Next.js UI; consumes API via generated TS types | Hold business logic or call Claude directly |
| `backend/` | HTTP API, auth, approvals, persistence, job enqueueing, orchestrator host | Execute untrusted code in-process |
| `code-intelligence/` | Ingestion, parsing, symbol/call/dependency graphs, retrieval, behavioral expectations store | Call agents; own review logic |
| `code-review/` | Functional review, repair candidate generation, behavioral test generation | Re-index repos itself |
| `security-agents/` | Seven agents, orchestrator workflow definitions, tool registry, sandbox driver | Bypass the authorization service |
| `rl-defense/` | Gymnasium env, DDQN, baselines, eval, checkpoints | Act on real infrastructure |
| `monitoring/` | Webhook/event ingestion, normalization, correlation, triggers | Run assessments itself (it requests them) |
| `shared/` | Canonical contracts (Pydantic), generated JSON Schema + TS types, IDs, error codes | Contain subsystem logic |
| `tests/` | Cross-subsystem integration, contract, e2e, security-boundary suites | Replace per-package unit tests |
| `scripts/` | Dev tooling (setup, codegen, link checks) | Contain product logic |
| `examples/` | Deliberately vulnerable demo targets, fixtures | Be deployed anywhere public |

## 7. Technologies

Chosen-as-default (revisable via ADR): Python 3.12, FastAPI, Pydantic v2,
PostgreSQL, Anthropic Claude (model id via `ANTHROPIC_MODEL`, default
`claude-haiku-5-5` only in production/staging per owner instruction (#380);
development/test overrides allowed, see ADR-0015), Tree-sitter, Docker sandboxes,
PyTorch + Gymnasium (Double DQN), Next.js + React + TypeScript + Tailwind.

Proposed, not decided: task queue (Postgres `SKIP LOCKED` queue vs. Celery/Redis
vs. Temporal), vector search (none → pgvector), sandbox hardening (Docker →
gVisor/Firecracker), object storage (local FS → S3-compatible). See
[docs/decisions/](docs/decisions/README.md).

## 8. Shared context system

The Unified Intelligence Layer ([docs/11](docs/11_SHARED_CONTEXT_AND_MEMORY.md)) stores
facts keyed by `(project_id, repo_id, commit_sha)` with provenance
(source file + line span + symbol + producing component + time). Rules:
- Every derived conclusion carries an `EvidenceRef` back to code, tests, or runs.
- Facts about commit A are not valid for commit B unless re-validated or proven
  unaffected by the diff (see "freshness").
- Agents receive **retrieved context packs**, never whole repositories.
- Repository content is untrusted input (prompt-injection risk). It is placed in
  clearly delimited data sections and can never grant permissions.

## 9. Coding standards (summary — full: [docs/24](docs/24_DEVELOPMENT_STANDARDS.md))

- Python: type hints everywhere, `ruff` + `mypy --strict` (target), `pytest`.
- TypeScript: `strict: true`, ESLint, no `any` in exported types.
- Contracts live in `shared/`; never redefine a shared model locally.
- Small, single-purpose modules; dependency direction flows toward `shared/`.
- No silent exception swallowing. Errors use the shared error-code catalog.
- Deterministic code for anything safety-critical (scope checks, approvals, budgets).

## 10. Security constraints (full: [docs/15](docs/15_SECURITY_BOUNDARIES.md))

- No untrusted code on the host. Sandboxes: no outbound network by default,
  CPU/memory/time limits, read-only base image, ephemeral.
- Security tests only against targets registered in an **AuthorizedScope**
  record with a human approver. The scope check is code, run before every
  network-capable tool call.
- No model output can widen permissions, approve itself, or merge/deploy.
- Secrets only via environment/secret manager. `.env` is git-ignored;
  `.env.example` holds placeholders only.
- Exploit details for real third-party systems never go in public docs/issues.

## 11. Testing expectations (full: [docs/16](docs/16_TESTING_AND_VERIFICATION.md))

- Every PR adds or updates tests for what it changes.
- LLM-dependent code is tested with recorded/fake model responses plus schema
  validation tests; live-model evals are a separate, opt-in suite.
- "Done" means the Definition of Done in [docs/planning/DEFINITION_OF_DONE.md](docs/planning/DEFINITION_OF_DONE.md),
  not "it compiles".
- Never fabricate benchmark numbers or claim a test ran when it did not.

## 12. API contract expectations

- Contract-first: change `shared/` models → regenerate JSON Schema/TS → update
  API → update consumers, in that order, in one PR or a coordinated PR set.
- Breaking changes to `shared/` or public API need the `contract-change` label,
  a migration note, and review by the contract owner
  ([docs/planning/TEAM_ASSIGNMENTS.md](docs/planning/TEAM_ASSIGNMENTS.md)).
- API versioned under `/api/v1`. Additive changes are preferred.

## 13. Git conventions

- `main` is protected; work on branches: `<type>/<TASK-ID>-<slug>`
  (e.g. `feat/INTEL-003-python-symbol-extractor`). Types: `feat`, `fix`, `docs`,
  `refactor`, `test`, `chore`, `research`.
- Commits: Conventional Commits (`feat(intel): extract python symbols (INTEL-003)`).
- **No attribution trailers** of any kind (`Co-Authored-By`, "Generated with…",
  session links). The commit message ends at its last line of content.
- One task per PR where possible; use the PR template.

## 14. Documentation update requirements

A change is not complete until:
- Specs it contradicts are updated (or an ADR explains the divergence).
- The subsystem README reflects what now actually exists.
- The task status is updated in [HACKATL_TASKS.md](docs/planning/HACKATL_TASKS.md)
  or [BACKLOG.md](docs/planning/BACKLOG.md).
- A handoff entry is appended to [SESSION_HANDOFF.md](docs/planning/SESSION_HANDOFF.md).
- [CHANGELOG.md](CHANGELOG.md) `Unreleased` is updated for user-visible changes.

## Current project phase

**Current phase: Implementation — authorized by the repository owner on 2026-10-16.**
Phase 0 (repository and architecture) is complete. Six developers build in parallel,
each on their own `feat/<owner>/<scope>` branch, following [MASTER_BUILD_SPEC.md](MASTER_BUILD_SPEC.md)
and the ownership lanes in [coordination/OWNERSHIP.md](coordination/OWNERSHIP.md). Product
code never goes directly to `main`. All security, Git, testing and contract rules in this
file still apply. Phases: [docs/planning/MASTER_ROADMAP.md](docs/planning/MASTER_ROADMAP.md).

## 15. Where to find assigned work

1. [docs/planning/HACKATL_TASKS.md](docs/planning/HACKATL_TASKS.md) — the MVP task list with IDs, owners, status, acceptance criteria.
2. [docs/planning/BACKLOG.md](docs/planning/BACKLOG.md) — post-MVP tasks.
3. [docs/planning/DEPENDENCIES.md](docs/planning/DEPENDENCIES.md) — what blocks what.
4. [docs/planning/SESSION_HANDOFF.md](docs/planning/SESSION_HANDOFF.md) — what the last session left.

Only pick up tasks that are `READY` (dependencies met) and either assigned to you
or `UNASSIGNED` with a human's go-ahead. Mark it `IN_PROGRESS` with your branch.

## 16. Handling uncertainty

- If intended behavior or a requirement is unclear, **do not invent it**. Write the
  question in [docs/research/RESEARCH_QUESTIONS.md](docs/research/RESEARCH_QUESTIONS.md)
  or the handoff, choose the smallest reversible default, and label it.
- If a spec looks wrong, open an ADR proposal rather than quietly diverging.
- Distinguish in all writing: *implemented*, *specified*, *proposed*, *hypothesis*.

## 17. Avoiding duplicate implementations

Before writing a module: search the repo for the concept (`rg -i "<concept>"`),
check `shared/` for an existing model, check the subsystem README "What exists"
section, and check open branches/PRs (`gh pr list`). Extend; don't fork.

## 18. Preserving compatibility between subsystems

- Communicate only through `shared/` contracts, the backend API, or the job/event
  bus — never by importing another subsystem's internals.
- Run the contract tests (`tests/contract/`, once they exist) before merging.
- Integration checkpoints are listed in [docs/25_INTEGRATION_STRATEGY.md](docs/25_INTEGRATION_STRATEGY.md);
  do not break a passed checkpoint.


<!-- HACKATL_SIX_AGENT_BUILD_PROTOCOL -->
## Six-agent concurrent implementation protocol (current active phase)

Read `MASTER_BUILD_SPEC.md` before work, then `coordination/MESSAGES.md`, `coordination/CONTRACTS.md`, `coordination/OWNERSHIP.md`, `coordination/MILESTONES.md`, applicable `team-prompts/<PERSON>_*.md`, and new messages in `coordination/messages/`. The previous "Phase 0 only" directive is superseded by the user's explicit authorization to implement; other security, Git, test and architecture constraints remain in force. Every agent checks GitHub at session start, milestones, shared interface changes and handoff. Never overwrite another owner's work or claim unexecuted tests pass.

<!-- HACKATL_GIT_CADENCE -->
## Git cadence — commit very often, always read what changed (mandatory for all six developers and their agents)

1. **Commit very frequently.** Commit after every passing test, every finished sub-step,
   and at least every 30 minutes of work. Small commits, Conventional Commit messages.
   Never hold more than ~30 minutes of uncommitted work.
2. **Push after every commit** to your own `feat/<owner>/<scope>` branch, so teammates
   and their agents can see your work. An unpushed commit is invisible to everyone else.
3. **Always read what changed before you act.** At session start, before every commit and
   push, before changing any shared interface, and before opening or updating a PR, run:
   ```
   git fetch origin --prune
   git log --oneline --decorate -15 origin/main          # what landed on main
   git log --oneline HEAD..origin/main                   # what you are missing
   git diff --stat HEAD...origin/main                    # which files changed
   git for-each-ref --sort=-committerdate refs/remotes --format='%(refname:short) %(committerdate:relative) %(subject)'
   gh pr list --state open; gh issue list --state open
   ls -t coordination/messages/ | head                   # newest team messages
   ```
   Read the diffs and messages that touch your area (`git log -p origin/<branch> -- <path>`).
4. **Integrate often.** Merge or rebase `origin/main` into your branch at least daily and
   whenever main changes something you depend on (commit or stash first; never force-push
   shared branches).
5. **Respond in the same session.** If a teammate's change affects you, reply with an
   immutable `coordination/messages/` file and a PR/issue comment before you stop.

<!-- HACKATL_AGENT_COORDINATION -->
## Multi-agent coordination (mandatory for all agents)

- Live agent-to-agent traffic: **issue #559** comments (one comment per message, prefix
  with a state tag: `CLAIMED` / `WORKING` / `NEEDS_COORDINATION` / `BLOCKED` /
  `READY_FOR_REVIEW` / `MERGED` / `DONE`).
- Shared dashboard: `coordination/AGENT_BOARD.md` — regenerated by
  `scripts/coordination_board.py`; **never hand-edit**. Your durable state is your own
  `coordination/status/agents/<agent>.json`.
- `CLAIMED` before touching paths another agent might also edit; check the board and
  #559 first. Integration agent re-checks the board before every merge.
- Full protocol: `coordination/PROTOCOL.md`.
