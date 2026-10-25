# Pilot launch package

Owner: Adam Issac (pilot launch owner). Date: 2026-10-24.
Scope: the first **five authorized pilot customers** of Ari AI on the hosted
deployment (Vercel frontend + Railway control plane + Anthropic models).

Every claim in these documents was checked against `main` at `d0131167`
(2026-10-24) or against executed evidence linked inline. Where a capability is
specified but unproven, partial, or simulation-only, the document says so.
Do not soften or upgrade these labels in customer-facing material.

| Document | Contents |
|---|---|
| [PILOT_SCOPE.md](PILOT_SCOPE.md) | What the pilot covers: supported repos, limits, response times, exclusions |
| [FEATURE_MATRIX.md](FEATURE_MATRIX.md) | Truthful capability matrix: implemented / staging-only / simulation / partial / unavailable |
| [CUSTOMER_ONBOARDING.md](CUSTOMER_ONBOARDING.md) | Step-by-step onboarding checklist and how a customer revokes, disables, cancels and requests deletion |
| [DATA_HANDLING.md](DATA_HANDLING.md) | Customer-facing data-handling and processing disclosure (draft for review) |
| [OPERATIONS.md](OPERATIONS.md) | Incident reporting, customer notification, support ownership, recovery, escalation |
| [METRICS.md](METRICS.md) | Pilot feedback loop, quota/budget visibility, per-customer health report |
| [RISK_REGISTER.md](RISK_REGISTER.md) | Pilot risk register with mitigations and owners |
| [FIRST_FIVE_PLAN.md](FIRST_FIVE_PLAN.md) | Staging rehearsal plan and the exact first-five rollout sequence |
| [LAUNCH_DECISION.md](LAUNCH_DECISION.md) | Signed launch recommendation with every exception documented |

**Launch recommendation: CONDITIONAL GO** — see
[LAUNCH_DECISION.md](LAUNCH_DECISION.md) for the eight binding conditions.

## Non-negotiable claims policy for the pilot

Ari does **not** have, and pilot material must not claim:

- SOC 2 or any other compliance certification.
- Zero data retention (see DATA_HANDLING.md — retention is operator-managed,
  not automatic).
- Dedicated single-tenant infrastructure (the deployment is shared
  multi-tenant with application-level isolation only — no Postgres RLS yet).
- Autonomous defense (the Defense Lab ships simulation-derived and
  rules-based *recommendations*; enforcement is not offered to pilot
  customers).
- Security coverage beyond the three executable check categories
  (`access_control.ownership`, `input_validation.bounds`,
  `access_control.authentication`). Other weakness classes are *flagged*,
  not *validated*.
