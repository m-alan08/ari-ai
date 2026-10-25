# CLAUDE.md — mandatory orientation for Claude Code

You are working in **hackatl-agentic-security**, an autonomous software engineering
and cybersecurity platform with three interconnected pillars sharing one
intelligence layer. This file is the entry point. It is deliberately short; it
tells you **what to read for the work you are doing**. [AGENTS.md](AGENTS.md) is the
full operational reference and applies to you too.

## Non-negotiable facts

1. **Specs are not implementations.** Nearly everything in `docs/` describes the
   *intended* system. Before you assume something exists, find it in code. Check
   [docs/planning/SESSION_HANDOFF.md](docs/planning/SESSION_HANDOFF.md) and the task
   status in [docs/planning/HACKATL_TASKS.md](docs/planning/HACKATL_TASKS.md).
2. **Current phase:** see the "Current phase" line in [AGENTS.md](AGENTS.md#current-project-phase).
   Do not start work from a later phase without an explicit instruction.
3. **Two kinds of agents.** *Runtime agents* (Scout, Probe, Trace, Diagnose, Patch,
   Fortify, Verify) are product features. *You* are a development agent. Never
   conflate them in code, naming, or docs.
4. **Evidence over assertion.** No LLM output — including yours — counts as proof that
   code works or a fix is correct. Only executed tests do. Never report tests as
   passing without running them.
5. **Safety boundaries are code, not prompts.** Authorization, scope checks, sandbox
   limits, and approval gates must be deterministic. Read
   [docs/15_SECURITY_BOUNDARIES.md](docs/15_SECURITY_BOUNDARIES.md) before touching any
   tool that executes code, sends network traffic, or changes state.
6. **Never** add secrets to the repo, target systems outside the authorized sandbox,
   make the GitHub repo public, or provision paid infrastructure without explicit
   human approval.
7. **No attribution lines** (`Co-Authored-By`, "Generated with…", session URLs) in
   commits or PRs.

## Before beginning any work

1. Read this file and [AGENTS.md](AGENTS.md).
2. Understand the vision: [docs/01_MASTER_PRODUCT_SPEC.md](docs/01_MASTER_PRODUCT_SPEC.md)
   (or the denser [master engineering prompt](docs/prompts/00_MASTER_ENGINEERING_PROMPT.md)).
3. Find your task and its owner/status in [docs/planning/HACKATL_TASKS.md](docs/planning/HACKATL_TASKS.md)
   and its dependencies in [docs/planning/DEPENDENCIES.md](docs/planning/DEPENDENCIES.md).
4. Read the contracts you will touch: [docs/12_DATA_MODELS_AND_CONTRACTS.md](docs/12_DATA_MODELS_AND_CONTRACTS.md),
   [docs/13_BACKEND_API_DESIGN.md](docs/13_BACKEND_API_DESIGN.md), and for agents
   [docs/agents/AGENT_CONTRACT.md](docs/agents/AGENT_CONTRACT.md).
5. Read the subsystem spec (table below) and the role prompt in [docs/prompts/](docs/prompts/).
6. Inspect the related implementation directories. Confirm what already exists;
   extend it rather than duplicating it.
7. Implement **only the assigned scope**. If the task is ambiguous, record the
   ambiguity and the assumption you made in the handoff, or ask.
8. Run the relevant checks (lint, type-check, tests). Report actual results.
9. Update docs that your change made stale, the task status, and
   [docs/planning/SESSION_HANDOFF.md](docs/planning/SESSION_HANDOFF.md).
10. If you made a decision with lasting consequences, write an ADR in [docs/decisions/](docs/decisions/README.md).

## What to read for which work

| Work type | Read (in order) | Role prompt |
|---|---|---|
| Frontend / UI | 14, 13, 12 | [01_FRONTEND_ENGINEER](docs/prompts/01_FRONTEND_ENGINEER.md) |
| Backend API / persistence / jobs | 04, 13, 12, 18 | [02_BACKEND_ENGINEER](docs/prompts/02_BACKEND_ENGINEER.md) |
| Code intelligence / indexing | 05, 11, [architecture/code-intelligence](docs/architecture/code-intelligence.md) | [03_CODE_INTELLIGENCE_ENGINEER](docs/prompts/03_CODE_INTELLIGENCE_ENGINEER.md) |
| Functional review / repair | 06, 05, 16 | [04_CODE_REVIEW_ENGINEER](docs/prompts/04_CODE_REVIEW_ENGINEER.md) |
| Runtime security agents / orchestrator | 07, 08, [agents/](docs/agents/AGENT_CONTRACT.md), 15 | [05_SECURITY_AGENT_ENGINEER](docs/prompts/05_SECURITY_AGENT_ENGINEER.md) |
| RL defense | 09, 17, [architecture/rl-training-and-inference](docs/architecture/rl-training-and-inference.md) | [06_RL_ENGINEER](docs/prompts/06_RL_ENGINEER.md) |
| Monitoring / integrations | 10, 25, [architecture/event-lifecycle](docs/architecture/event-lifecycle.md) | [07_INTEGRATION_ENGINEER](docs/prompts/07_INTEGRATION_ENGINEER.md) |
| Tests / evaluation | 16, 17 | [08_TESTING_ENGINEER](docs/prompts/08_TESTING_ENGINEER.md) |
| Architecture review | 04, 26, decisions/ | [09_ARCHITECTURE_REVIEWER](docs/prompts/09_ARCHITECTURE_REVIEWER.md) |
| Demo prep | 20, planning/MVP_SCOPE | — |

Numbers refer to `docs/NN_*.md`.

## Commands

When a subsystem gains tooling, add its commands **here** and in that subsystem's README.

Control plane (`shared/`, `backend/`, `monitoring/` — uv workspace at the repo root, Python 3.12;
details in [backend/README.md](backend/README.md)):

```
uv sync                         # install the workspace
make check                      # ruff, ruff format --check, generated-contract freshness
uv run mypy                     # type-check shared + monitoring
make test                       # pytest; real PostgreSQL (embedded pgserver unless TEST_DATABASE_URL)
make contracts                  # regenerate shared/generated (JSON Schema + TS) and fixtures
make dev                        # migrate + API (:8000) + worker on embedded PostgreSQL
make smoke-live                 # opt-in real Claude call (RUN_LIVE_LLM_EVAL=1 + key; costs money)
uv run python -m backend.recipes <git checkout>   # dry run of the build-recipe PR Ari opens on connect (--live: Claude drafts routes)
```

Reviewer (`code-review/`, details in [code-review/README.md](code-review/README.md)):

```
uv run pytest -q code-review/tests                                   # FakeTransport + real tool execution
RUN_LIVE_LLM_EVAL=1 uv run pytest -q -m live_llm code-review/tests -s  # live reviewer; costs money, $1 cap/phase
uv run python -m code_review.eval_corpus                              # prove the reviewer corpus labels (free)
```

Security agents (`security-agents/`, details in [security-agents/README.md](security-agents/README.md)):

```
cd security-agents && .venv/bin/python -m pytest tests -q                 # scripted model turns + real tools, sandboxes, tests
.venv/bin/python -m security_agents.cli --target ../examples/payments-lite --auto-approve   # replay run, seven agents
.venv/bin/python -m security_agents.standards.report                      # offline standards table (pinned data)
.venv/bin/python -m security_agents.standards.smoke                       # tagged finding, RoE, SP 800-115 coverage, SARIF
RUN_LIVE_LLM_EVAL=1 LIVE_SEVEN_MAX_USD=8 .venv/bin/python -m pytest -q -m live tests/test_live_provider.py -s  # live; costs money
```

If imports fail on macOS (hidden `.pth` files in the venv), set
`PYTHONPATH=.:../shared:../standards:../code-intelligence:../examples/payments-lite/target`.

## Multi-agent coordination (mandatory)

- Live agent-to-agent traffic: **issue #559** comments — one comment per message,
  prefixed with a state tag (`CLAIMED` / `WORKING` / `NEEDS_COORDINATION` / `BLOCKED` /
  `READY_FOR_REVIEW` / `MERGED` / `DONE`).
- Dashboard: `coordination/AGENT_BOARD.md` (regenerate with
  `scripts/coordination_board.py`; never hand-edit). Your durable state is your own
  `coordination/status/agents/<agent>.json`.
- `CLAIMED` on #559 before touching paths another agent might also edit; check the
  board + #559 at session start and before merges. Full protocol:
  `coordination/PROTOCOL.md`.

## When you are uncertain

Prefer, in order: (1) the spec, (2) an existing ADR, (3) asking the human,
(4) a documented assumption in the handoff with the smallest reversible choice.
Never silently invent product requirements.


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

### Frontend (Next.js app + transport; details in [frontend/README.md](frontend/README.md))

```sh
npm ci --prefix frontend/transport && npm test --prefix frontend/transport   # transport build + node tests
npm ci --prefix frontend
npm run lint --prefix frontend && npm run typecheck --prefix frontend && npm test --prefix frontend && npm run build --prefix frontend
cd frontend && ../.venv/bin/python tests/e2e/stack.py   # real API + Postgres + worker + next start + Playwright
```

The e2e harness uses the backend's TEST-ONLY scripted model transports (`provider_mode=fake`,
labelled in the UI); everything else is real. `npm run test:integration --prefix frontend/transport`
remains the transport-only real-backend proof.
