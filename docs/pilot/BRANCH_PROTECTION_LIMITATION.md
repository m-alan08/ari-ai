# Branch protection — infrastructure limitation and migration plan

**Status (2026-10-25, owner decision):** enforced branch protection on `main` is
an **infrastructure limitation**, not an unfinished setup task. This document is
the standing record for the pilot release report.

## What is limited

`adamissac/ari-ai` is a **private repository on a personal GitHub account**.
GitHub does not enforce branch-protection rulesets on private repositories
owned by personal (non-organization) accounts on the current plan. Required
status checks, required reviews, and push restrictions therefore **cannot be
technically enforced by GitHub** for this repository today.

Consequence for the release gate: the required-checks list defined in the
release-gate work (`feat/adam/release-gate` / #530) is **advisory** — CI runs and
reports on every PR, but nothing blocks a merge mechanically. Enforcement is
currently procedural: agent and human merges must require green CI and
appropriate review, and security-sensitive changes require explicit human
approval before merging.

The repository **must not** be made public or transferred to a different owner
as a workaround without the owner's explicit authorization.

## What still holds without GitHub enforcement

- All CI checks run on every PR and on `main` after each merge; the release
  gate's `pilot-verify` pipeline reports pass/fail truthfully.
- Procedural enforcement: the merge order is coordinated through
  `coordination/AGENT_BOARD.md` and team messages; merges to `main` only happen
  through reviewed PRs by convention.
- Merge audit trail: git history + PR records remain the accountable record.

## Migration plan to enforced branch protection

Goal: `main` protected by required checks + required reviews, enforced by
GitHub — not by convention.

| Step | Action | Owner | Notes |
|---|---|---|---|
| 1 | Create a GitHub **organization** (e.g. `ari-ai` or agreed name) on a plan that includes rulesets for private repos (Team tier or higher). | Adam | Org name/members decided by owner. |
| 2 | Transfer `adamissac/ari-ai` into the organization (`Settings → Transfer`). GitHub redirects all git/PR/issue links automatically. | Adam | Preserves issues, PRs, actions history, wiki. |
| 3 | Re-point the GitHub **App installation** to the org repo (re-install or grant the new location). | Adam | App 5360865 must be installed on the moved repo or webhooks/installation tokens break. |
| 4 | Re-point deployments: Vercel project git source, Railway GitHub service, webhook URLs (if the App's callback URL embeds repo paths). | Adam + infra | The webhook URL itself is app-scoped and survives; repo-scoped settings do not. |
| 5 | Re-invite team members with org roles; re-map `CODEOWNERS` if used. | Adam | — |
| 6 | Install ruleset on `main`: require PR, require green required checks (the release-gate list), require ≥1 review, block force-push/deletion. | Adam | The check list already exists in the release-gate doc — apply it verbatim. |
| 7 | Verify: attempt a direct push to `main` (should be denied), open a test PR and confirm required checks gate the merge button. | any agent | Verification only — do not leave test artifacts. |
| 8 | Update docs: this file flips to "enforced"; LAUNCH_DECISION/RISK_REGISTER reference the ruleset. | any agent | — |

**Do NOT do as an alternative:** making the repo public (exposes planted
fixtures' answer keys and product internals), moving to a fork-based flow
(loses CI/app integration), or weakening the procedural gate in the meantime.

## Pilot posture

The limited pilot proceeds under procedural enforcement, documented here. The
advisory gate stays on; the owner is informed that merge-blocking is social,
not mechanical, until step 6 lands.
