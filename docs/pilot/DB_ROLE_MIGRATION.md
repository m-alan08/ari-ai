# Production DB: least-privilege app role, credential rotation, rehearsal cleanup

Status: **EXECUTED 2026-10-25 (cutover); admin password rotated; remaining key rotations and public-access removal tracked below.**

## Backup and restore validation

Railway manual backup `pre-ari_app-role-migration` (ID
`23a37c7c-e429-4282-9d2e-7399335bd91e`) was created at 08:47:42Z. A custom-format
`pg_dump` was restored to a local PostgreSQL 18 cluster. Both copies were at
Alembic head `0017`. The restored copy matched every production count from
before the snapshot: orgs 7, users 7, projects 8, `index_payloads` 621,
assessments 636, `audit_events` 20,280, and 13,224 artifacts created before
08:48. The only difference was 34+ artifacts written after the snapshot
boundary. The dump was not restored into a Railway scratch service; that
remains a backup-validation gap.

## Role and pre-cutover verification

`ari_app` was created with `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB
NOCREATEROLE NOREPLICATION`. It has CONNECT on the database, USAGE on the
schema, SELECT/INSERT/UPDATE/DELETE on all tables, USAGE/SELECT on all
sequences, and matching default privileges for `postgres` on future tables
and sequences. It initially had `pg_monitor` for storage-report WAL metrics.
At about 11:30Z on 2026-10-25, after PR #610 deployed, the admin connection ran
`REVOKE pg_monitor FROM ari_app`. The role now has no role memberships; its
attributes remain `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION`.
`pgaudit.log=none`.

The `ari_app` password was set as a locally computed SCRAM-SHA-256 verifier;
plaintext never reached the server or its logs. No password or verifier is
recorded here.

Pre-cutover probes as `ari_app` through the public proxy showed:

- With no tenant GUC, `index_payloads` and `index_snapshots` returned 0 rows.
- With a valid project GUC, 619 rows from one project were visible, matching
  the admin's 619 rows. A fake project GUC returned 0 rows.
- A cross-tenant insert without a GUC was denied by RLS.
- `CREATE TABLE`, `ALTER TABLE`, `TRUNCATE`, and
  `SET session_replication_role` were denied.

## Cutover and hosted verification

On the `ari-ai` service, `DATABASE_URL` now uses the private-host `ari_app`
connection and `MIGRATION_DATABASE_URL` is the `${{Postgres.DATABASE_URL}}`
reference. Deploy `f2733519` succeeded at 09:09Z; later deployments also
succeeded. `/readyz` was ready with 24 workers. `pg_stat_activity` showed
every app client backend as `ari_app` (14–15 connections) and no `postgres`
app connections.

Rollback is to set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`.

Hosted verification after cutover:

- Assessment `asm_01M5S5QGNPAPXKYCW72G1H0W5N` completed in about 90 seconds for
  $0.0288 while running as `ari_app`.
- In the cross-org drill, synthetic `rehearsal-eve` received 404 on all 14
  GETs against the owner org's project, repository, snapshots, scopes,
  assessments, findings, events, jobs, assessment runs and report, and finding
  patches. The owner session received 200 on the same 14 GETs. Eve's POST
  assessment start and POST cancel both returned 404, and her project list
  was empty.

After PRs #605 and #610 merged, the Railway production deploy of `a85885aa`
succeeded and `/healthz` reported `git_sha` `a85885aa`. After the revocation,
`pg_stat_activity` showed 14 app connections as `ari_app`. The ops endpoint
`GET /api/v1/ops/storage` returned 200 with `wal_bytes: null` and `alerts: []`;
WAL bytes are no longer reported for the app role because #610's savepoint
skips the denied WAL probe. Revoking `pg_monitor` also means disk-use alerts
treat missing WAL as zero, so WAL growth alone will not trip a warning until
an admin-role probe or external monitor covers it; keep that gap in ops
runbooks (`docs/deploy/POSTGRES_STORAGE.md` still describes WAL-inclusive
disk use for admin probes). `/readyz` was ready with 24 workers and 0 queued.

In the least-privilege test harness, tests that fail only as `ari_app` are
test- or operator-path cases: DDL in `test_automation_opt_in`;
`session_replication_role` in the operator deletion procedure, which must run
as admin; and `SET ROLE` in the RLS probe test. The storage-report fix is
[PR #610](https://github.com/adamissac/ari-ai/pull/610).

## Completed admin password rotation

At about 09:32Z on 2026-10-25, the `postgres` admin password was rotated using
a locally computed SCRAM-SHA-256 verifier. The Postgres service's
`POSTGRES_PASSWORD`, `PGPASSWORD`, `DATABASE_URL`, and `DATABASE_PUBLIC_URL`
all resolve to the new value, and login with the old password is rejected.
`ari-ai`'s `MIGRATION_DATABASE_URL` reference resolves to the new value; the
service was redeployed as deploy `f5ce137b`. Other agents were told on #559
(comment `6395942484`) that stored admin URLs are dead. No password or
credential-bearing URL is recorded here.

## Still open: API key rotation and public access

The Anthropic key and GitHub App private key still need to be rotated by the
owner from their respective consoles. Disable public TCP proxy access last.
Do not put passwords, credential-bearing URLs, or session tokens in this
document.

## Rehearsal cleanup (approved and executed 2026-10-25)

Adam approved the cleanup plan on 2026-10-25 by replying “approve cleanup”.
At about 10:10Z, `backend.privacy.purge_project` was run with the admin
connection on project `prj_01M5RASMAA4GT4EDKWJMCQGAWF` (“Pilot Rehearsal (prod
leg)”) in org `org_4893E29D9B9FDCD265ACD26992`. Before the purge there were 0
repositories, 0 assessments, 0 index payloads, 0 artifacts, and 0 scopes. The
result was `{"projects": 1}`; the project's one `audit_events` row is retained
because the audit chain is append-only.

The earlier removal by another lane of rehearsal-alice's owner seat in Adam's
org and both rehearsal sessions by 05:01Z was not owner-approved. The two
rehearsal users now have 0 sessions. Each holds only the owner seat of their
own personal org, and neither can access any other org. Under the approved
plan, both users, both personal orgs, the four owner-project assessments, and
the revoked scope are retained as audit evidence. Cleanup is complete.

| Object | IDs | Disposition |
|---|---|---|
| Users | `usr_A40D640D9695D19372BACD36EB` (rehearsal-alice), `usr_7C13AB87FA6E020B936382C9C7` (rehearsal-eve) | Retained as audit evidence; each has 0 sessions and access only to their own personal org |
| Sessions | Both rehearsal sessions were removed by another lane by 05:01Z | No sessions remain; that earlier removal was not owner-approved |
| Membership in owner org | rehearsal-alice's owner seat in Adam's org was removed by another lane by 05:01Z | No such seat remains; that earlier removal was not owner-approved |
| Personal orgs | `org_4893E29D9B9FDCD265ACD26992`, `org_C992A0C6A94705736F4C7E61AC` | Retained as audit evidence; each user holds only the owner seat in their own personal org |
| Project | `prj_01M5RASMAA4GT4EDKWJMCQGAWF` (“Pilot Rehearsal (prod leg)”), org `org_4893E29D9B9FDCD265ACD26992` | Purged at about 10:10Z through `backend.privacy.purge_project` using the admin connection; result `{"projects": 1}`; one append-only audit row retained |
| Assessments | `asm_01M5RCNXY6CTM8S8S3MDQ6ZFBD`, `asm_01M5RCVC20ZMYH8EKTZQP40W73`, `asm_01M5RD308T0RER91FNMQ0JGP0Q`, `asm_01M5RD406NQC4GRRT48N64ANNY` (owner project) | Retain as audit-referenced rehearsal evidence |
| Scope | `scp_01M5RD3YN9JH9V652ATKS93GKV` (revoked) | Retained as audit evidence |

Evidence was kept on the operator machine (report JSON, patch and verification
artifacts, probe logs) before the cleanup.
