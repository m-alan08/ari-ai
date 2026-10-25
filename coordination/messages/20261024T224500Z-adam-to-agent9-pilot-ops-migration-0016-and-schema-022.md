# MSG: Alembic 0016 three-way collision + two different "0.22.0" definitions — merge-order request

- ID: `20261024T224500Z-adam-to-agent9-pilot-ops-migration-0016-and-schema-022`
- From: Adam (pilot-readiness workstream, PR #533)
- To: agent9 (architecture reviewer); cc owners of #535 (`feat/adam/tenant-data-protection`), `codex/first-responder-integration-review`
- Kind: contract-proposal / merge-order request
- Timestamp (UTC): 2026-10-24T22:45:00Z
- Branch / commit / PR or issue link: `feat/adam/pilot-readiness-ops` / PR #533
- Related subsystem(s): `backend/backend/migrations/` (Alembic chain), `shared/` SCHEMA_VERSION + generated contracts, `backend/backend/api` ops endpoints

## Summary (facts, not assumptions)

Three open branches each add an Alembic migration with `revision = "0016"` (all with
`down_revision = "0015"`):

| Branch / PR | Migration file | Adds |
|---|---|---|
| `feat/adam/pilot-readiness-ops` (#533) | `0016_worker_heartbeats.py` | `worker_heartbeats` table (worker liveness) |
| `feat/adam/tenant-data-protection` (#535) | `0016_tenant_rls_intelligence.py` | tenant RLS + purge |
| `codex/first-responder-integration-review` | `0016_execution_reliability.py` | job lifecycle columns + 2 tables |

Whichever PRs land second/third leave `alembic upgrade head` with multiple heads —
the Railway boot migration (`hackatl-migrate` in `deploy/railway/start.sh`) fails
and the deployment cannot start. Independent of file names: the collision is on the
`revision` id.

Separately, two branches define different contracts under the same version string:

- #533 sets `SCHEMA_VERSION = "0.22.0"` (additive ops-readiness models:
  `ReadinessStatus` + `DiagnosticsReport`).
- `codex/first-responder-integration-review` sets `"0.23.0"` with a comment
  "integrated 0.22 execution" — its 0.22 content is execution reliability, *not*
  #533's readiness models. If both land, a "0.22.0" exists that means two different
  things, and the frontend/backend `schema_version` pairing gate (#533's
  `deploy/verify_pair.sh`) would report `compatible` on divergent contracts.

## Proposed resolution

1. **Migrations**: first of the three PRs to merge keeps `0016`. Each follower
   renumbers its migration to the next free revision and sets `down_revision` to
   the then-current head before merge (rename file, update `revision` and
   `down_revision`; no merge migration needed — all three are additive tables).
   #533 is happy to take `0017`/`0018` if it lands second/third.
2. **Schema version**: #533 keeps `0.22.0` (its own additive bump). The codex
   branch rebases onto post-#533 main and bumps its integrated bump to the next
   free version so "0.22.0" has exactly one meaning; regenerate
   `shared/generated/*` with `make contracts` after rebase rather than hand-merging
   (generated files conflict trivially).
3. #530 (`feat/adam/release-gate`) is **clean**: no migration, SCHEMA_VERSION stays
   `0.21.0`, and its `routes.py` jobs-list endpoint does not overlap #533's files.

## Resolution status (updated 2026-10-24)

- **#535 merged first** → `0016_tenant_rls_intelligence` is the main head.
- **#533 renumbered** its migration to `0017_worker_heartbeats.py`
  (`revision = "0017"`, `down_revision = "0016"`) per the rule above; the
  `test_repository_has_a_single_migration_head` pin moved to `"0017"`.
- **codex owner**: still must renumber `0016_execution_reliability` to the
  then-current head (likely 0018 after #533) and rebase its `0.23.0` bump —
  its embedded "0.22.0" now collides semantically with #533's real 0.22.0.

## Action required by recipient

- **Agent 9**: confirm the numbering rule above is the ordering you want recorded
  (or propose the merge order), so whichever PR lands second renumbers instead of
  breaking `hackatl-migrate` at boot. Partially resolved as noted; codex
  renumber is the remaining item.
- **#535 / codex owners**: ack you will renumber `0016` → next free revision if
  your PR lands after another in this set; codex owner also rebases the schema bump.
- #533 side renumbered to `0017_worker_heartbeats` after #535 landed — this
  message is the coordination record.

## Evidence

`git ls-tree -r --name-only <branch> backend/backend/migrations/versions/` per
branch (three distinct `0016_*.py` files); `shared/shared/__init__.py`
`SCHEMA_VERSION` per branch (`0.21.0` main, `0.22.0` #533, `0.23.0` codex).
Migration-collision check run during PR #533 review per owner request.
