# Transfer branch / merge-equivalence manifest

Date: 2026-10-25 · Author: Devin (integration) · Supersedes per-PR notes for upstream planning

## Proved artifacts

| Branch | Tip | Base | Role |
|---|---|---|---|
| `transfer/execution-reliability` | `ae414985` | upstream/main `f8ab19e7` | 0018 exec-reliability + R5 |
| `transfer/investigation-observability` | `6e6733a6` | upstream/main | contracts 0.24 |
| `transfer/pilot-security-hardening` | `cfa06bf6` | upstream/main | 0019 disconnect (down 0018) + R2 |
| `transfer/customer-onboarding` | `db8b1fd7` | upstream/main | contracts 0.25 + R3/R4 |
| `feat/alan/pilot-deployment-ops` | upstream head | upstream/main | ops surface (#5) |
| `transfer/release-validation` | `7ba1ab77` | upstream/main | #7 |
| `transfer/sequential-manifest` | `5539a9ca`+pin | upstream/main | merge order proof |

## Sequential composition proof

`transfer/sequential-manifest` = upstream/main `f8ab19e7` + merges in order:
execution-reliability → investigation-observability → pilot-security-hardening →
customer-onboarding → pilot-deployment-ops → release-validation.

- `test_parallel_migrations`: **2/2** on the composed tree; single Alembic head `0019`.
- Migration chain: upstream `0016`/`0017` → `0018_execution_reliability` → `0019_repository_disconnect`.
- Contract generation clean (`shared.export` wrote 169 files); SCHEMA_VERSION ends `0.26.0`.
- Production-conflict resolutions are the SAME unions verified on RC2 `3f16c99f`:
  `api/ops.py` (both auth schemes + both endpoint sets, `_require_ops_header` + `_require_ops`),
  `main.py` (`ops as ops_api` alias + `onboarding` + `live_defense_investigations` routers),
  `worker.py`, `settings.py`, `routes.py` (hardened disconnect + strict R3 reconnect),
  `.env.example`, `.gitignore`.

## Deliberately absent from the manifest (lands via its own PR)

- upstream **#458 Phase 7C** (`first_responder.py`, `provider_ledger.py`, contracts 0.23) —
  conflicts resolved on `merge-proposal/pr458-onto-main-91b234d2` (fork); pending #458 merge.
- Integration-only glue: R2 provider-turn revocation checks in `first_responder.py`
  (`c44fea5f` on the integration branch) — belongs with the integration review PR.
- #2 e2e tests — folded into the integration review PR, not a standalone PR.

## Equivalence claim and limits

Sequential upstream composition reproduces RC2's per-workstream diffs on a real
merge tree; it cannot contain #458's content (not on upstream/main), so
production behavior equality with RC2 holds modulo #458 and the R2 glue. Full
behavioral equivalence is proven by the RC2 strict matrix, not by the manifest —
the manifest proves *mergeability and migration-chain correctness on the actual
proposed merge tree*, which is what Codex N5/N6 required.

## N5/N6 verification status

- duplicate-0018 fixed: #4's migration is now `0019` (down `0018`) — no two `0018` files.
- SCHEMA_VERSION restored: obs `0.24.0` (`6e6733a6`), onboarding `0.25.0` (`db8b1fd7`).
- Migrations tested on the actual merge tree: `test_parallel_migrations` 2/2 on `5539a9ca`.
