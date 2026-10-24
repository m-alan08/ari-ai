# 19 — Observability and cost control

Status: **specified**.

## Logging

- Structured JSON logs (`structlog` proposed) with fields: `ts`, `level`,
  `service` (api|worker|frontend), `request_id`, `assessment_id`, `agent`,
  `step_key`, `event`, `error_code`.
- Redaction filter applied before emit ([15](15_SECURITY_BOUNDARIES.md) §6).
- No repository file contents in logs; reference by path + line span.

## Tracing

- OpenTelemetry SDK in api and worker (proposed); trace per HTTP request and per
  job; spans for LLM calls (model id, tokens, latency, cost) and sandbox runs
  (image, duration, exit code, network mode).
- Export target: console/OTLP to a local collector in dev; no paid backend.

## Metrics (MVP, from DB queries + logs)

| Metric | Use |
|---|---|
| Assessment duration by state | Find bottlenecks |
| Tokens / USD per assessment and per agent | Cost control |
| Schema-invalid output rate per agent | Prompt quality |
| Hypotheses → validated ratio | Scout precision proxy |
| Patches accepted on attempt 1 / 2 / unresolved | Patch quality |
| Sandbox start latency, timeouts | Infra health |
| Webhook processing latency | Monitoring health |

## LLM gateway accounting

Every call records: `assessment_id`, `agent`, `model_id`, input/output tokens,
cache read/write tokens if applicable, computed USD (from a pricing table in
config, updated manually and dated), latency, outcome. Budget checks happen
**before** the call using a conservative estimate, and actuals are reconciled
after.

## Cost controls

| Control | Default |
|---|---|
| Per-assessment USD cap | 10 (`LLM_MAX_USD_PER_ASSESSMENT`) |
| Per-assessment token cap | 400k (`LLM_MAX_TOKENS_PER_ASSESSMENT`) |
| Per-agent share | [08](08_AGENT_ORCHESTRATION.md) §Budgets |
| Org daily cap | USD 25 |
| Hypotheses validated per run | 10 |
| Monitoring-triggered run cap | USD 3 |
| Cheaper model for triage/summaries | `ANTHROPIC_MODEL_FAST` (optional) |
| Prompt caching of stable system prompts/context | Use where the SDK supports it (proposed) |

## Dashboards

MVP: an internal `/projects/[id]/assessments/[asm]` usage panel plus SQL
queries in `scripts/` (planned). No paid observability SaaS.

## Alerting

MVP: none beyond UI. Post-MVP: alerts on budget exhaustion rate, sandbox
failure rate, webhook backlog.

## Execution reliability metrics

Queue and worker figures follow the same DB-query approach: `GET /api/v1/projects/{id}/execution` and `python -m backend.execution stats` report queue wait time, active workers, lease age, execution duration, retries, cancellation outcomes, failure categories, recovery status and stuck executions, computed from `jobs`, `job_events` and `worker_heartbeats`. See `docs/architecture/execution-reliability.md`.
