# CI workflows — planned, not configured

Each workflow below is added by the task that first creates something for it to
check. **Implemented:** `docs.yml`, `secrets.yml` (INFRA-003), `frontend.yml` (UI-001), `backend.yml` (CORE-002/003), `rl.yml` (RL-001), `examples.yml` (DEMO-001), `security-agents.yml` (SEC). The others are still planned.

| Workflow file (planned) | Triggers | Jobs | Added by |
|---|---|---|---|
| `docs.yml` ✅ | PR touching `**/*.md` | Markdown link + anchor check, Mermaid block-type check | `INFRA-003` |
| `secrets.yml` ✅ | every PR + push to `main` | gitleaks scan of full history with `.gitleaks.toml` (adds a rule for every `sk-ant-*` key form) | `INFRA-003` |
| `backend.yml` ✅ | PR touching `backend/`, `shared/`, `monitoring/`, `rl-defense/rl_defense/` | ruff, mypy, generated-contract freshness, pytest against a Postgres service (incl. TS compile of generated types); `rl-integration` job runs `backend/tests/test_rl_integration.py` with CPU torch + rl-defense installed and fails if it skips | `CORE-002` |
| `frontend.yml` ✅ | PR touching `frontend/`, `shared/generated/`, `backend/`, RL bundles | transport tests, eslint, strict tsc, Vitest, next build; Postgres-backed real-stack Playwright e2e | `UI-001` |
| `contracts.yml` | PR touching `shared/` | folded into `backend.yml` (`shared.export --check`, `shared.examples --check`) | `CORE-003` |
| `rl.yml` ✅ | PR touching `rl-defense/` | env API checks (`gymnasium.utils.env_checker`), determinism test, short smoke train (CPU, < 5 min) | `RL-001` |
| `examples.yml` ✅ | PR touching `examples/` | payments-lite lint, its own tests, label checks, Docker build (non-root) | `DEMO-001` |
| `security-agents.yml` ✅ | PR touching `security-agents/`, `shared/`, `code-intelligence/`, payments-lite | ruff + the seven-agent suite (FakeLLM, real registry/scope_guard/sandbox) | `SEC` |
| `e2e.yml` | nightly + manual | docker compose up; demo-scenario e2e against `examples/` target | `TEST-005` |

## Rules for future workflows

- Least-privilege `permissions:` block on every workflow (`contents: read` default).
- Pin third-party actions to a full commit SHA.
- No secrets exposed to workflows triggered by forks (`pull_request_target` is banned
  unless reviewed by the security owner).
- Live-LLM evaluation suites never run on PRs automatically (cost + secret exposure);
  they run manually or on schedule with a budget cap.
- Use only free GitHub Actions minutes unless paid usage is approved.

## Code intelligence (INTEL-001..008)

`code-intelligence.yml` adds locked-workspace Ruff/format/strict mypy, intelligence tests
and real PostgreSQL payload/webhook integration. FakeTransport runs in CI; provider
inference is opt-in and skipped without a key.

## Code review (REVIEW-001..006)

`code-review.yml` runs on PRs touching `code-review/` or its dependencies (shared, backend,
code-intelligence, the security-agents sandbox, payments-lite). It runs Ruff, format and
strict mypy on `code_review`, the reviewer suite against a Postgres service (FakeTransport;
real git exports and pytest executions), and the reviewer through the shared
`security_agents` sandbox driver. The live reviewer eval (`-m live_llm`) never runs here.

## Path gates (2026-10-24, release gate)

Workflow triggers no longer use `paths:` filters — every job runs on every push/PR so a GitHub "required check" can never sit at *Expected — waiting for status*. Instead each job's first step runs `scripts/ci/path_gate.py`, which diffs base→HEAD against the suite's original path globs and emits `run=true|false` plus a `**RUN**`/`**SKIP** — <reason>` line in the step summary; the real steps are conditional on `run == 'true'`. An unknown or unfetchable base fails safe to RUN (prefer extra CI over a skipped check). Skip is therefore an explicit, visible verdict — not an absent job.

Job ids are unique across workflows (e.g. `backend-tests`, `intel-tests`, `review-tests`, `secagents-tests`, `rl-tests`) so each required check names exactly one thing. `pilot-verify.yml` is the manual release pipeline — see `docs/release/RELEASE_GATE_2026-10-24.md` for the category matrix and the required-check list.
