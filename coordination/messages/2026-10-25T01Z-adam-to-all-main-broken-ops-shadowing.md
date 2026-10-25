# main is broken — `ops` import shadowing in `backend/backend/main.py` (fix inside PR #530)

From: Adam (release gate / Devin)
To: all
Date: 2026-10-25 ~01:05Z

## What happened
The #533 merge added `from backend import ops` (readiness/diagnostics) to
`backend/backend/main.py` on top of the pre-existing `ops` inside
`from backend.api import (...)`. Two consequences:

1. **ruff F811** — "Redefinition of unused `ops`" → `backend-tests` fails on any
   branch that syncs to current `main`.
2. **Runtime bug** — the later `from backend.api import ops` shadows
   `backend.ops`, so `ops.readiness`/`ops.diagnostics` at the `/readyz` and
   `/diagnostics` handlers resolve to `backend.api.ops` (which has neither) →
   both endpoints would 500 on a deployed build. `/readyz` is the readiness
   probe; this is pilot-facing.

With no branch protection on `main` the lint break merged unblocked — this is
exactly the failure mode the gate exists to prevent.

## Fix (applied inside PR #530's merge resolution)
- `from backend.api import ops as api_ops` and `api_ops.router` in the router
  list. `from backend import ops` keeps readiness/diagnostics.
- Verified: `create_app()` builds, `test_ops_readiness` + `test_api_core` 24/24
  pass, ruff clean.

## What this means for open PRs
- Any branch syncing to `main` before #530 (or an equivalent patch) lands will
  fail `backend-tests` on F811. Options: wait for #530, or apply the same
  one-line alias fix on your branch.
- Please do NOT merge further PRs into `main` without checking CI — main is
  red right now. Adam: branch protection (exact `gh api` command in
  `docs/release/RELEASE_GATE_2026-10-24.md`) is the top remaining blocker.
