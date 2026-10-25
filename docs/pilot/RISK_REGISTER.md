# Pilot risk register

Pilot-scoped risks (delivery risks live in `docs/planning/RISK_REGISTER.md`,
architectural in `docs/26_RISKS_AND_TRADEOFFS.md`). L/I: H/M/L.
Owner = accountable person; "Pilot owner" = Adam unless named.

| ID | Risk | L | I | Mitigation / status | Owner |
|---|---|---|---|---|---|
| R-01 | Cross-tenant data exposure through an unchecked route or new feature | M | H | `require_project` on every scoped route (verified); Agent 2's two-tenant adversarial suite must land and pass **before customer 2**; no pilot claim of RLS | Security/privacy |
| R-02 | No Postgres RLS — isolation rests on application code | M | H | Disclosed in DATA_HANDLING.md; RLS decision tracked by privacy workstream; do not claim otherwise | Security/privacy |
| R-03 | Indefinite retention + no self-serve deletion violates customer expectations | H | M | Disclosed pre-signup; operator-run deletion ≤ 30 d; retention runbook being produced by privacy workstream — gate customer 1 on it existing | Pilot owner |
| R-04 | Repo disconnect not auto-reflected after App uninstall; Ari keeps a repo row + clones | M | M | Ops procedure: on uninstall or request, remove repo row + clone + artifacts; verified token cutoff is immediate from GitHub | Pilot owner |
| R-05 | Write-scope GitHub permissions surprise (Contents+PR write) | M | H | Onboarding walks the exact permission list; least-privilege token scoping verified; deploy doc corrected (see R-11) | Pilot owner |
| R-06 | `auto` fix-delivery could push code without human check | M | H | Pilot excludes it: delivery mode fixed to pull_request/manual at onboarding; settings warn; monitor audit for `auto` | Pilot owner |
| R-07 | Flagged-but-inconclusive findings read as "validated" by customers | H | M | Onboarding step 7 drills flagged vs validated vs inconclusive; report wording reviewed; only 3 executable check categories exist | Pilot owner |
| R-08 | Anthropic provider terms (30-day abuse retention) conflict with any zero-retention marketing | M | H | DATA_HANDLING.md states standard API terms; no zero-retention claim anywhere | Pilot owner |
| R-09 | Cost runaway on monitoring bursts | L | M | $50/day global cap + per-run budgets + per-project 5/h 20/d caps + deferral; weekly spend report; $25/customer stop-loss | Pilot owner |
| R-10 | RL/Defense Lab oversold as autonomous protection | M | H | Matrix labels: simulation-only / shadow / recommendation-only. RL retrain was rejected by the promotion gate — recommendations stay rules/simulation-derived; no enforcement offered | Adam (RL) |
| R-11 | Documentation drift vs. reality (e.g. deploy doc says App is read-only; CVSS v3.1 runtime vs v4 docs; STATUS.md initial-briefing rows stale) | H | M | This package is the dated truth at `d0131167`; fix deploy doc line; treat open PRs (#496, #458, #441, #412, #517) as branch-only | Pilot owner |
| R-12 | Worker/API single-service failure during pilot | M | M | Durable queue + leases + readiness endpoint; honest FAILED states; recovery documented in OPERATIONS.md | Pilot owner |
| R-13 | Support load exceeds founder capacity (5 customers × unfamiliar repos) | M | M | Staging rehearsal exposes onboarding friction first; support-burden metric reviewed weekly; pilot throttles invites (FIRST_FIVE_PLAN) | Pilot owner |
| R-14 | Prompt injection via repo content manipulates agents | M | H | Repo content treated as untrusted data in delimited context packs; cannot grant permissions; scope gates are code | Security/privacy |
| R-15 | Secret leakage into transcripts/artifacts | L | H | `redact()` before persist/model; assistant-turn re-send residual disclosed; ingest quarantine for secret-like payloads | Security/privacy |
| R-16 | Sandbox escape / unsafe local execution | L | H | Prod microVM driver; Docker hardened; local driver refuses outside dev; verified in audit | Security/privacy |
| R-17 | Open PRs merged mid-pilot change behavior under customers | M | M | Pilot tracks `main` only; merges during pilot trigger a claims re-check against FEATURE_MATRIX | Pilot owner |

## Review-of-evidence notes (task 10)

Reviewed on `main` `d0131167`: 45-case live eval baseline + scorer fix (#516),
push→agents gating verification, sandbox-leak sweep (#495), specialist
training (#503), eval expansion (#513/#514). Open agent PRs assessed for pilot
impact: #496 (frontend nav — UI-only, non-blocking), #458 (Phase 7C —
branch-only, keep out of pilot claims), #441 (redaction-parser fix —
desirable before indexing customer code with secrets; recommend merging),
#412 (RL-gated pilot infra — blocked by rejected promotion, stays out),
#517 (agent cadence chore — harmless).
