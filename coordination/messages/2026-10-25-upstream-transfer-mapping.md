# Upstream transfer mapping — canonical = adamissac/ari-ai

Date: 2026-10-25 · Author: Devin (integration) · Branch: `integration/2026-11-02-release` @ `f51e2410`

Per Alan's repository-correction directive: canonical repo is `adamissac/ari-ai`,
upstream main is the integration target. All future workstream PRs target
`adamissac/ari-ai:main`; the fork's existing PRs #1–#8 are preserved as historical
work — nothing deleted, rebased, or force-pushed.

## Access restrictions (measured, not assumed)

- **No push access to `adamissac/ari-ai`** — `git push --dry-run` returns 403.
  Upstream PRs therefore cannot be created from this session: `gh` is
  unauthenticated and the PR API does not support cross-repository heads.
- **Proposed upstream PRs are expressed as compare URLs** on
  `m-alan08/ari-ai` branches → `adamissac/ari-ai:main`. Anyone with upstream
  write access (Alan/Adam) opens them in one click, or cherry-picks the
  transfer branch onto a namespaced upstream branch.
- All `transfer/*` branches live on the fork, each based on upstream/main
  @ `f8ab19e7` — clean diffs containing only the intended workstream changes.

## Transfer mapping

| Fork PR | Workstream | Source SHA | Upstream-targeted branch (on fork) | Head SHA | Proposed upstream PR (compare URL) |
|---|---|---|---|---|---|
| #1 | Execution reliability (incl. R5 `6063d9b5`) | `f2d911d7` | `transfer/execution-reliability` | (tip) | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:transfer/execution-reliability |
| #2 | Real-stack e2e harness | `247c2312` | — folds into integration PR — e2e content is inside #8's history; remaining commits are re-verify docs | — | (none — lands with integration review PR) |
| #3 | Investigation observability | `92e00096` | `transfer/investigation-observability` | (tip) | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:transfer/investigation-observability |
| #4 | Pilot security hardening (incl. R2 `356cd535`) | `356cd535` | `transfer/pilot-security-hardening` | `a68817fb` | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:transfer/pilot-security-hardening |
| #5 | Pilot deployment ops | (branch tip) | — direct PR from `feat/alan/pilot-deployment-ops` — diff is clean workstream-only | — | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:feat/alan/pilot-deployment-ops |
| #6 | Customer onboarding (incl. R3/R4 `41b77fc2`) | `41b77fc2` | `transfer/customer-onboarding` | (tip) | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:transfer/customer-onboarding |
| #7 | Release validation | `f771b3fb` | `transfer/release-validation` | (tip) | https://github.com/adamissac/ari-ai/compare/main...m-alan08:ari-ai:transfer/release-validation |
| #8 | Integration (all workstreams) | `f51e2410` | dedicated review PR **deferred** — transfers only after recompose onto latest upstream + owner R2–R5 fixes | — | TBD |

## Rebase decisions baked into transfer branches

- **Alembic collision**: upstream has `0016_tenant_rls_intelligence` +
  `0017_worker_heartbeats`. Workstream migrations renumbered to `0018`:
  - `transfer/execution-reliability`: `0018_execution_reliability` (down 0017);
    #1's registry table renamed `worker_registry` (upstream keeps
    `worker_heartbeats` for its liveness schema — genuine semantic split, not
    a revert).
  - `transfer/pilot-security-hardening`: `0018_repository_disconnect`
    (down 0017); vendored `0016_execution_reliability` file dropped — upstream
    #1 lands it instead.
  - **Convention**: whichever of #1/#4 merges second renames its 0018 to 0019
    and re-parents (both claim 0018 today — intentional, documented).
- **SCHEMA_VERSION claims** (upstream is at `0.22.0`):
  `transfer/investigation-observability` claims `0.24.0`;
  `transfer/customer-onboarding` claims `0.25.0`;
  `transfer/pilot-security-hardening` claims `0.26.0`.
  (`0.23` reserved for upstream #458 Phase 7C.) This matches the numbering
  used on the frozen RC `bc490484` — merge in that order and the chain is
  linear.
- `transfer/customer-onboarding` @ `41b77fc2` includes the R3/R4 fixes
  (fresh `verify_installation_access` on reconnect; tenant-scoped
  `connected_repo_id` join). 12/12 onboarding tests pass.
- `transfer/pilot-security-hardening` includes the R2 disconnect-side fix
  (row-lock, cancels queued/waiting steps, `repository_revocation()` service).
  The provider-turn half of R2 remains integration glue (first_responder.py)
  tracked in `coordination/messages/2026-10-25-r-findings-owner-fixes.md`.
- `transfer/investigation-observability`: 9/9 investigation tests pass.
- #5 direct PR: stale `0.21.0` version claim is NOT in its diff (unchanged
  from merge-base), so the cross-repo diff is clean; merge-time conflict at
  `shared/__init__.py` expected — resolve by keeping upstream's version line.

## Dependency order (sequential merge proposal, unchanged)

`upstream #412` → `upstream #458` → fork `#1` → `#2`(w/ integration) → `#3` →
`#4` → `#6` → `#5` → `#7` → integration review PR last.

## Status gates still in force

- Codex NO-GO on `070f75a9` (R2–R5 + C4) — nothing merged upstream until
  re-review passes on a new RC SHA.
- Frozen RC preserved: `bc490484` on `integration/2026-11-02-release`
  (fork PR #8, historical traceability).
- Recompose pending owner-delivered fixes: R2 `356cd535` ✓ delivered,
  R3/R4 `41b77fc2` ✓ delivered, R5 `6063d9b5` ✓ delivered — all on fork
  branches and inside the transfer branches above.
