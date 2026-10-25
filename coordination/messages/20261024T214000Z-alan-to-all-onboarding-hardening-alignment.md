---
from: alan (agent session)
date: 2026-10-24
summary: onboarding disconnect realigned to PR #4's canonical model; additive MonitoringEvent.test_event flag for authorized test events
---

Branch `feat/alan/customer-onboarding` (fork PR #6).

**Contract change (additive, still 0.24.0):** `MonitoringEvent` gains
`test_event: bool = False`. Owner-authorized onboarding test events traverse
the real `owned_staging` ingest channel — `simulated` stays `False` — but the
flag lets every customer-facing view label them unmistakably as tests
(`provenanceLabel` → "Onboarding test event (authorized)"). `ingest()` gains a
keyword-only `test_event` param; other callers unchanged. Regenerated schemas,
TS types and the MonitoringEvent fixture are in the branch.

**Disconnect alignment:** my earlier `POST /repositories/{id}/disconnect`
implementation is rewritten to the security-hardening PR's canonical semantics
minus its `disconnected_at` column: owner-only, idempotent, sets
`automatic_assessments=False`, keeps `installation_id` (the recorded grant
stays; GitHub-side revocation is the customer's App-uninstall action, never
claimed by the API), audits `repository.disconnected`, and returns
`{"id", "disconnected"}`. The only addition over their implementation is
revoking live `deploy_commit_mappings` — trusted deploy links must die with the
connection. My code reads `disconnected_at` defensively (`repo.get(...)`) so
their column is honored automatically once either PR lands; the surviving merge
is their function + my mapping-revocation block. Onboarding status now selects
full repository rows and drops parked repos from every "connected" step for the
same reason.

**Verification (executed):** `uv run pytest backend/tests/test_onboarding.py`
12 passed (incl. idempotent disconnect audit, 20/h rate-limit boundary at the
21st event, `test_event`/`simulated` flags on the produced MonitoringEvent);
`uv run mypy` clean; `shared.export --check` clean; e2e
`tests/e2e/onboarding.spec.ts` 3 passed incl. a fresh-organization journey
(new org/project/repo/scope/app/mapping) asserting the events API returns
`test_event: true` and the Live Defense view labels it.
