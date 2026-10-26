# Live Defense Phase 6.5 plan — RL-gated pilot engineering

Status: **implemented (infrastructure)**. Verdict remains
**INFRASTRUCTURE READY — RL BLOCKED**. This does not start Phase 7,
does not train/promote/edit Codex research, and does not authorize a
company pilot.

Branch: `feat/live-defense-phase6-5-rl-gated-pilot` from live `origin/main`
`74a0a3c2` (PR #410 merged after #411).

## Goal

Surrounding product infrastructure so a later owner-approved RL policy could
be evaluated in server-enforced shadow-only mode. The final readiness
verdict stays blocked while required RL evidence is missing.

## Explicit non-goals

- Training, promotion, dataset, holdout, v6 weight, or historical-eval edits
- Converting Phase 6 CONDITIONAL GO into customer deployment
- Expanding the executable control surface
- Auto-serving unapproved checkpoints
- Customer contact / real customer credentials
