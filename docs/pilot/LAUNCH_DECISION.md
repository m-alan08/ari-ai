# Launch decision — first five pilot customers

**Decision: CONDITIONAL GO**
Signed: Adam Issac, pilot launch owner — 2026-10-24
Code basis: `main` @ `d0131167`; hosted `readyz` = database ✓ queue ✓
llm_mode `live` ✓ github_app_configured ✓ webhook_secret ✓.

## Why GO is defensible for the narrow scope

- Onboarding, push→assessment, monitoring, opt-outs, scope approval/revoke,
  cancel, budgets, audit and tenant membership checks are all implemented
  and the hosted path is verified live (45-case baseline + two connected
  production repos).
- The platform is honest under failure: transient failures stay FAILED,
  deferred work is queued-not-dropped, inconclusive findings are labelled.
- No claim in this package exceeds executed evidence.

## Why not an unconditional GO

Binding conditions — each maps to a documented gap. Customer 1 may not
start until C1–C6 are done; C7–C8 are standing rules for the whole pilot.

| # | Condition | Blocking |
|---|---|---|
| C1 | Pilot scope held exactly as PILOT_SCOPE.md (Python, ≤2 repos, ingest caps, quota table) | Yes — scope discipline |
| C2 | Fix delivery restricted to `pull_request`/`manual`; `auto` never enabled for pilot orgs (watch audit log) | Yes |
| C3 | Defense Lab offered as recommendation-only/shadow; no control grants, no enforcement, simulation labelled | Yes |
| C4 | Deletion handled by an operator runbook with a ≤30-day SLA — `purge_project` verified (PR #535, 17/17 tests on branch) + drill verified end-state 404s; **merge #535 before relying on it in prod** | Yes |
| C5 | Customer consent record (FIRST_FIVE_PLAN.md) signed before any repo access, covering: data storage, Anthropic processing, write-scope token, flagged-vs-validated, no auto-commit, no enforcement | Yes |
| C6 | Staging rehearsal (FIRST_FIVE_PLAN.md part 1) passes steps 1–10 on an unfamiliar repo + two orgs — local two-org leg PASSED 2026-10-24 (REHEARSAL_REPORT.md); production leg pending owner setup | Yes |
| C7 | Pilot claims track `main` only — open PRs (#496, #458, #441, #412, #517) are branch-only until merged and re-verified; recommend merging #441 (redaction-parse fix) before indexing customer code | Standing |
| C8 | Flagged ≠ validated is drilled at onboarding and in every report; Probe's three-category limit is always disclosed | Standing |

## Documented exceptions (accepted for the pilot)

- No self-serve deletion or retention automation (R-03) — compensated by
  disclosure + operator SLA.
- No Postgres RLS (R-02) — compensated by application-level checks +
  adversarial suite before customer 2.
- No repo-disconnect endpoint (R-04) — GitHub-side uninstall is the real
  cutoff; ops cleanup follows.
- No billing/metering — pilot is free; pricing remains a proposal.
- Strict all-seven-roles live success remains partially proven — IDOR
  chains complete end-to-end; broad CWE validation does not. Disclosed.
- Support is founder-led with response *targets*, not SLAs.

## What would change this to NO-GO

Rehearsal step failures; any cross-tenant access observed; deletion runbook
not delivered; the live eval materially regressing (re-run the 45-case set
if any of the open PRs merge first).
