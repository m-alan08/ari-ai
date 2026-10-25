# Staging rehearsal + first-five pilot plan

## Part 1 — staging rehearsal (mandatory before customer 1)

Goal: prove the whole pilot on infrastructure that is not the polished demo
setup — an **unfamiliar owned repository** and **two independent
organizations** — before a real customer's repo is touched.

### Setup

- Two GitHub logins → two personal orgs in Ari (auto-created on first login;
  verified). Org A = "customer", Org B = "other tenant" for isolation checks.
- One owned repo neither org has assessed before (not `payments-lite`, not
  `mypypath` — pick a Python repo the team has never connected; a small
  FastAPI service with a deliberately planted access-control defect is
  ideal because it exercises the ownership template end-to-end).

### Rehearsal script (half a day, two people)

| # | Step | Pass criteria |
|---|---|---|
| 1 | Org B connects its own repo, runs an assessment | Completes; B sees only B's project in `/projects` |
| 2 | Org A: full onboarding checklist steps 1–5, unaided from the doc | No step needs an engineer; consent + permissions understood |
| 3 | Org A: manual assessment on a planted-defect commit | Completes; IDOR-class finding flagged **and validated** (ownership template), fix PR opened |
| 4 | Org A: enable monitoring; push a benign commit | Assessment completes ~1–2 min; no false positive recorded |
| 5 | Org A attempts to open Org B's findings/artifacts by ID | Every attempt 404s (spot-check via API + UI) |
| 6 | Org A: cancel a running assessment | Ends `cancelled`, not green |
| 7 | Org A: flip all opt-out switches; push again | No run is scheduled; event recorded only |
| 8 | Org A: uninstall the App on GitHub | Fetch/scheduling stops; owner runs repo cleanup procedure |
| 9 | Simulate deletion request | Operator runs deletion job against Org A data; verify removal from DB, artifacts, clones |
| 10 | Kill the worker mid-run | Run stays failed/queued honestly; no phantom success |
| 11 | Defense Lab on Org A staging telemetry (optional if no app) | Recommendations only; nothing executes |
| 12 | Cost + support review | Report generated; spend inside caps |

Exit gate: steps 1–10 pass. Any Sev-1/Sev-2 finding re-opens the launch
decision.

## Part 2 — first-five rollout

Throttled invites — never more than one new customer per week until the
metrics say otherwise.

| Wave | Customer profile | Entry gate | Exit gate to next wave |
|---|---|---|---|
| C1 | Friendliest: Python/FastAPI repo, technical point of contact | Rehearsal passed; deletion runbook exists; consent signed | Clean week: onboarding unaided ≥ step 5, 0 Sev-1/2, spend < $25 |
| C2 | Same profile, different org structure | Agent 2 two-tenant suite green on main | Repeat C1 gates + FP rate < 20% of flagged |
| C3 | Larger Python repo (near ingest limits) | C1–C2 stable 2 weeks | Index succeeds inside limits; runtime p95 < 10 min |
| C4 | Customer wanting monitoring on active repo | C3 stable | Deferral/cap behavior observed correct under real push volume |
| C5 | Defense Lab interested; has staging telemetry | C4 stable + ingest-token drill rehearsed | Recommendations-only proven; no enforcement attempted |

Stop conditions (any → pause invites, convene team): Sev-1 incident;
cross-tenant report; spend cap hit twice; >30% of flagged findings disputed;
deletion request mishandled.

## Customer consent record (sign at step 0)

The customer acknowledges in writing:

1. Ari stores full repository copies, indexed content, and agent transcripts
   on shared infrastructure; retention is indefinite until a deletion
   request; deletion is operator-run within ~30 days (no self-serve).
2. Code context is processed by Anthropic under their standard API terms.
3. The GitHub App requests Contents + Pull requests write (needed to deliver
   fixes); per-call tokens are least-privilege and write tokens are
   repo-pinned.
4. Security findings outside the three executable check categories are
   flagged, not proven.
5. Fixes arrive as pull requests or in-app proposals; Ari never auto-commits
   during the pilot.
6. Defense Lab output is decision support from simulation/rules — not live
   protection.
7. Pilot quotas apply: ≤ 2 repos, ≤ 10k files/32 MB each, assessment and
   daily caps as listed in PILOT_SCOPE.md.
8. No SOC 2, no zero-retention, no single-tenant deployment is offered.

## Tooling notes

- Weekly health report: owner script over the endpoints in METRICS.md with a
  personal-org owner session; keep output in `coordination/status/` or the
  ops spreadsheet — never inside the product.
- Rehearsal evidence: keep run IDs, screenshots, API responses under the
  rehearsal report; attach to the launch-decision record.
