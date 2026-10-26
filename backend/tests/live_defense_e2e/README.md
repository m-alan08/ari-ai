# Live Defense end-to-end suite (owned staging)

End-to-end integration tests for the first-responder pipeline. Every test
drives the **real** HTTP API (`TestClient` on the FastAPI app), the **real**
PostgreSQL database (embedded or `TEST_DATABASE_URL`), the **real** durable job
queue (`worker.drain`), the **real** `security_agents` runtime loop, and the
**real** artifact store (sha256-verified reads). The only fake is the model:
scripted `FakeLLM` providers, labelled `provider_mode="fake"` on every record.

> FakeLLM results do **not** validate real Anthropic execution. The single
> opt-in live test is marked `live_llm` and is skipped unless
> `RUN_LIVE_LLM_EVAL=1` and `HACKATL_LIVE_ANTHROPIC_API_KEY` are set.

## Layout

| File | Role |
|---|---|
| `harness.py` | `OwnedStaging`: reproducible owned-staging environment — project + repo + scope, owned-repo git checkout, app registration, ingest tokens, deploy mapping, snapshot rows, mode API, telemetry ingest, `worker.drain`, and DB/artifact readers. Talks only to the public API and tables, so future phases can reuse it. |
| `transports.py` | Failure-injection providers for `GATEWAY_OVERRIDE`: `FailAfterCalls` (provider dies mid-run), `HookProvider` (side effect on the Nth call, e.g. flip mode OFF), `adversarial_scout_script` (denied tools + forged submit fields). |
| `report.py` / `conftest.py` | Machine-readable results: per-test `report` fixture collects expected-vs-observed checks and artifact references; one JSON file is written per run to `test-results/live-defense-e2e/` with scenario id, outcome, duration, failure stage, and environment identity. |
| `test_scenarios.py` | Scenarios A–H plus the opt-in live test. |

## Scenarios

| ID | Test | Fault? |
|---|---|---|
| A | `test_a_successful_investigation` — telemetry → decision `confirm_with_probe` → Scout+Diagnose executed → artifacts persisted → analyst handoff (`delivery_pending` → ack → `confirm` still refused; hypotheses stay unverified) | smoke |
| B | `test_b_missing_authorization` — bad ingest token refused; no mapping → `correlate` + `skipped_no_mapping`; no approved scope → `skipped_no_scope`; member 403 / outsider 404 / `auto_execute` rejected | smoke |
| C | `test_c_stale_snapshot` — mapping pins an old commit; with no snapshot for it Scout is `skipped_no_snapshot` (never investigates HEAD); once the mapped commit is indexed it executes against the mapped sha | — |
| D | `test_d_provider_failure` — provider raises `LLMUnavailable` after a successful turn; partial transcript persists, invocation fails honestly (`error_code=LLM_UNAVAILABLE`, still unverified) | fault |
| E | `test_e_worker_failure` — worker crashes after Scout's artifacts persist; job requeues, retry recovers one executed invocation and retains both transcripts | fault |
| F | `test_f_off_mode_interruption` — mode flips OFF mid-run via the real mode API; Scout is cancelled, Diagnose never runs, no more provider calls, no control jobs | fault |
| G | `test_g_multi_project_isolation` — two projects in one org: disjoint decisions, invocations, artifacts; cross-tenant read is 404 | — |
| H | `test_h_agent_output_manipulation` — model calls denied tools and submits forged `verified`/`permission` fields; tool denial + `SCHEMA_INVALID` are recorded, semantics stay `unverified`, mode stays `monitor` | — |

## Running locally

```sh
uv sync
uv run pytest -q backend/tests/live_defense_e2e          # everything (deterministic)
make e2e-ld-smoke                                        # fast smoke tier (A-B)
make e2e-ld                                              # full suite (A-H)
make e2e-ld-fault                                        # failure-injection tier (D-F)
RUN_LIVE_LLM_EVAL=1 HACKATL_LIVE_ANTHROPIC_API_KEY=... \
  uv run pytest -q -m "live_llm and live_defense_e2e" backend/tests/live_defense_e2e -s   # opt-in, costs money
```

CI: `.github/workflows/live-defense-e2e.yml` runs `smoke`, `full`, and `fault`
on relevant PRs; `live` is manual-only, gated on
`vars.RUN_LIVE_LLM_E2E == '1'` and the authorized
`secrets.HACKATL_LIVE_ANTHROPIC_API_KEY`.

## Results

Each run writes `test-results/live-defense-e2e/results-<ts>.json` (plus
`latest.json`): scenario id, outcome, duration, failure stage, structured
checks (expected vs observed), artifact references, and environment identity
(git sha/branch, schema + orchestrator versions, provider mode, host, python).
