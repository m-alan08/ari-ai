# Shadow-pilot (recommendation-only) — what a company actually gets

Ari can watch **one company-owned staging application** and write down what it
saw. It does not block traffic, isolate hosts, rotate customer credentials, or
change firewalls.

This is not a compliance certification, not a penetration-test report, and not
proof that shadow mode stopped an attack.

## What you do

1. Turn 24/7 defense **on** only if you want repository re-checks. That switch
   does not arm live enforcement.
2. Register the staging app as `sandbox` or `owned_test`. Production is not an
   allowed environment.
3. Issue an ingest token. Copy it once. Ari will not show it again.
4. Send authenticated runtime windows to `POST /api/v1/ingest/runtime/{project_id}`.
5. Read Overview, Investigation, and Explainability. Shadow decisions say
   **NOT EXECUTED**.
6. Accept / reject / needs-investigation is a recorded opinion. It does not
   apply a control.
7. Export `GET /api/v1/projects/{id}/pilot-report` if you want a snapshot.
8. Rotate the token when people change. Disconnect when the pilot ends; further
   ingest returns 401.

## What you must not do

- Do not open **Controls**, create a **control grant**, or register a
  **control channel**. Those are a separate owned-test loopback path on the
  same dashboard. Using them leaves recommendation-only shadow mode.
- Do not point ingest or controls at a third-party system you do not own.
- Do not treat HTTP 200, a green page, or a Q-value as “the attack succeeded”
  or “the service is healthy”.
- Do not treat analyst accept as approval to enforce.

## What the report will not say

- “Shadow prevented breaches”
- “Health is healthy because this endpoint returned 200”
- “HTTP 200 means the attack succeeded”

Worker, telemetry, and policy stay **unknown** unless a separate health-check
producer exists. Missing numbers are unavailable, not zero-by-default success.

## Suggested abort checks (owner sets the window)

During the pilot, abort if any of these appear: a `live_defense_execute` job,
a `LiveControl` row, a decision with `executed: true`, or use of the Controls
tab. Those mean the install is no longer recommendation-only.

Readiness evidence and the integration audit:
[LIVE_DEFENSE_PHASE6_READINESS.md](LIVE_DEFENSE_PHASE6_READINESS.md).

RL-gated pilot (separate gate; currently **INFRASTRUCTURE READY — RL BLOCKED**):
[LIVE_DEFENSE_PHASE6_5_RL_GATED_PILOT.md](LIVE_DEFENSE_PHASE6_5_RL_GATED_PILOT.md).
First-company staging package (not an activation):
[LIVE_DEFENSE_FIRST_COMPANY_STAGING.md](LIVE_DEFENSE_FIRST_COMPANY_STAGING.md).
