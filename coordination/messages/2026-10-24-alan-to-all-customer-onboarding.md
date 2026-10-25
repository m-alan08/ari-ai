---
from: alan (agent session)
date: 2026-10-24
summary: customer onboarding branch claims contracts 0.24.0 — status endpoint, owner test event, repo disconnect
---

Branch `feat/alan/customer-onboarding` (PR to follow, fork → upstream main).

**Schema:** claims **0.24.0**, additive only — new `shared/onboarding.py` models
(`OnboardingStatus`, `OnboardingStep`, `OnboardingTestEventCreate`,
`OnboardingTestEventResult`). No Alembic migration: repository disconnect reuses
the existing `installation_id IS NULL` = "connected by name" semantics and the
existing `deploy_commit_mappings.revoked_at`. 0.22.0 stays reserved for #458,
0.23.0 for the fork observability workstream, 0.20.0 for #412.

**New routes:** `GET /projects/{id}/onboarding` (viewer+; steps computed from
repos/snapshots/scopes/apps/tokens/mappings/windows/events — no stored state),
`POST /projects/{id}/onboarding/test-event` (owner; rate-limited 20/h; requires
an active ingest token; routes through the real owned_staging ingest),
`POST /repositories/{id}/disconnect` (owner; clears App grant, disables
automatic_assessments, revokes live mappings; idempotent).

**Known collision — fork PR #4** (`feat/devin-pilot-security-hardening`, opened
2026-10-24 ~20:16Z, same base): it also adds `POST /repositories/{id}/disconnect`
in `api/routes.py`, but with different semantics — a new `repositories.disconnected_at`
column via migration `0016_repository_disconnect` (note: that migration number
collides with fork PR #1's 0016), while my version clears `installation_id` and
revokes live deploy_commit_mappings with no schema change. Same route path +
same file = a guaranteed textual conflict; whichever PR lands second must
rebase and reconcile the two semantics (e.g. keep the column AND clear the
grant + revoke mappings). Its App-only connect gate (installation_id required
outside dev) is compatible with the onboarding journey — connect-by-name stays
a dev/test path. Flagging now so neither PR merges blind.

**No other conflicts expected:** touches `shared/registry.py`, `shared/__init__.py`
(SCHEMA_VERSION), `api/routes.py` (one new endpoint), `api/main.py` (router
import), `components/project.tsx` (one nav tab), transport client. No edits to
`live_defense/first_responder.py` or any file owned by the four open
workstreams.
