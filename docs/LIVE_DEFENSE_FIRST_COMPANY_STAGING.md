# First-company staging package (RL-gated shadow-only)

This is a **package**, not an activation. Do not provision real customer
credentials. Do not point Ari at third-party infrastructure. Only the
repository owner can authorize a company pilot after RL evidence exists.

**Current verdict: INFRASTRUCTURE READY — RL BLOCKED.**

Phase 6 CONDITIONAL GO (rules-based recommendation-only shadow) is a different
gate. It does not satisfy this package.

## What the company would get (when the owner later approves)

1. One company-owned **staging** application (`sandbox` or `owned_test`).
2. Server-enforced **shadow-only**: no grant, no control channel, no authorize,
   leftover execute fail-closed.
3. Ingest token shown once; SHA-256 stored.
4. Recorded events, shadow `LivePolicyDecision` rows (`NOT_EXECUTED`), analyst
   opinions that do not execute.
5. Health stays `unknown` unless a separate health-check producer exists.
6. Exportable `GET /projects/{id}/pilot-report` and
   `GET /projects/{id}/rl-readiness`.

## Operator checklist (do not run against a customer yet)

1. Confirm `GET /projects/{id}/rl-readiness` still has `ready: false` unless
   the owner has later approved RL evidence.
2. Register the staging app with **RL-gated shadow-only** checked, or
   `POST /applications/{id}/enable-rl-gated-pilot` after leftover grants,
   channels, and live controls are removed and verified.
3. Issue an ingest token. Copy it once. Do not paste it into this repo.
4. Send authenticated runtime windows to
   `POST /api/v1/ingest/runtime/{project_id}`.
5. Read Overview, Investigation, Explainability, Readiness.
6. Abort if a `live_defense_execute` job, `LiveControl` row, or
   `executed: true` decision appears.

## What this package does not include

- Customer production credentials or hosts
- Autonomous defense / Phase 7
- Serving of v6 or any later research checkpoint as an approved policy
- Numeric RL pass thresholds
- A claim that infrastructure completeness authorizes a customer pilot

Customer procedure for rules-based shadow (Phase 6):
[LIVE_DEFENSE_SHADOW_PILOT.md](LIVE_DEFENSE_SHADOW_PILOT.md).
