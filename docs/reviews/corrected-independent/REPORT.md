# Independent corrected integration review

Verdict: **NO-GO for release merging and customer deployment.**

Reviewed product: `77897e76eb561f166a2b96c1792545075bc8f078` in fork PR #8.
Upstream comparison: `eea084bc87ac6ca94f578073251c478655b09366`.
Evidence captured 2026-10-25, through 06:55 UTC. No production edits, merges,
deployments, live provider requests, or GitHub approval submissions were made.
The audit branch adds only tests, evidence and documentation.

N1–N4 now pass the original strict adversarial probes and have real production
fixes. This closes the two identified P1 reproductions on this SHA. A new P2
artifact-integrity regression, unresolved transfer/merge gates and unverified
same-SHA CI prevent release signoff. No new P0/P1 was established in this review.

## Candidate changes during review

The initial pin was `7f520f3b5d25162a250506456401f6e31abbaf32`. It contained no
production changes since RC2 `3f16c99f`: five runtime probes still failed. Those
results are retained separately and are not attributed to the corrected code.

PR #8 then advanced to `69547d2f98fbaf6bacda37f4795bd6534b78ddc9`, delivering
N1–N4 production changes. Its 32 strict security assertions passed. Broader
checks found three failures caused by nested DSN replacement markers, alongside
the transfer ordering failure and upstream compatibility failures.

The next pin, `77897e76eb561f166a2b96c1792545075bc8f078`, reorders the generic
URI and DSN patterns. The full focused set was rerun on this SHA. The three
replacement-marker assertions now pass. The new unquoted-expression corruption
remains. The last observed PR #8 head still matched this final pin.

## N1–N6 resolution matrix

- **N1 — PASS, original P1 disconnect/finalization race.**
  `backend/backend/live_defense/first_responder.py:1185` now confirms revocation
  through `repository_revocation_locked` before persisting the final result.
  `backend/backend/services.py:346` takes the repository lock and then mapping
  locks, re-reading revocation in the same transaction. Disconnect uses the
  same repository-before-mapping order in `backend/backend/api/routes.py:491`.
  The unchanged original probe commits a real owner disconnect after the real
  unlocked read; final output is now cancelled. The hook does not fabricate an
  authorization result. A new independent control establishes actual competing
  PostgreSQL update lock contention (SQLSTATE 55P03), then successful disconnect
  after the finalizer releases its lock and a revoked subsequent locked read.
  The response-time cancellation/cost/transcript retention control also passes.
  The added owner test named “serializes” is sequential; it alone would not
  prove contention. The independent lock control supplies that missing evidence.

- **N2 — PASS, P2 target installation verification.**
  `backend/backend/api/routes.py:619` now always verifies the recorded target
  installation after refreshing personal access. The unchanged unrelated
  personal-installation/revoked-org-installation attack returns forbidden.
  The original stale-refresh failure probe and broader owner/tenant tests pass.
  GitHub responses are controlled fixtures; actual GitHub authorization outages
  and live customer installations were not exercised.

- **N3 — PASS for both original P2 recovery failures.**
  `backend/backend/jobs.py:279` supplies the shared unresolved-ledger predicate;
  claim checks it before returning work and parks unresolved work in
  recovery_required. Reconciliation uses the same predicate.
  `backend/backend/execution.py:510` closes the operator-disposition ledger and
  transitions the locked job in one transaction. Applied settles unresolved
  attempts; not_applied/retry cancel them and clear the uncertainty flag.
  Actual child-process os._exit(71), claim-before-reconcile, authorized retry,
  lease fencing, concurrent claim/reconcile, emergency-capacity and cancellation
  tests pass. The fast claimant does not send another provider request; explicit
  authorized retry can proceed. Existing recovery tests moved reservation setup
  after the first claim to model a real mid-run reservation; their safety
  assertions remain. The original independent probes are byte-identical.
  No live provider idempotency or billing guarantee is inferred from scripted
  provider execution. Human resolution still requires a trustworthy disposition.

- **N4 — PASS, original P1 job-history credential disclosure.**
  `backend/backend/jobs.py:207` scrubs event reasons/details on write;
  transitions scrub error/recovery/result payloads. `execution.history` scrubs
  reasons/details on read, protecting old rows as well. Job, investigation,
  stuck-job and audit reads now apply redaction while keeping role checks.
  The unchanged viewer-history probe passes. A separate independent test writes
  an actual legacy raw history row, reads it as a viewer, checks that the password
  is absent, verifies an unrelated user gets 404 and confirms the raw stored row
  really existed. This is stronger than the new owner test which only calls
  redact on a Python list. No live customer credentials were used.
  The original disclosure is fixed; the expanded pattern set causes N7 below.

- **N5 — PASS for the original transfer migration collision.**
  Reliability `ae414985d09796cc950b7e833157861d239abc29` retains revision 0018.
  Hardening `cfa06bf649c5ca608c56badc497f0ad69ce444f0` now uses
  0019_repository_disconnect, down_revision 0018. The original literal Git-object
  inequality assertion passes with only its SHA/path inputs updated.
  This does not certify full production merge equivalence.

- **N6 — FAIL for the full strict transfer ordering probe; partial repair.**
  The empty tuple defect is fixed: observability `6e6733a6` is string 0.24.0 and
  onboarding `db8b1fd7` is string 0.25.0. The unchanged monotonic assertion still
  fails on the declared order: 0.23 → 0.24 → 0.26 → 0.25. Only pinned Git-object
  inputs were updated; neither the type nor ordering assertion was weakened.
  The manually resolved sequential manifest actually ends at string 0.26.0.
  Therefore the failing branch-head probe establishes a transfer ordering/
  resolution gate, not an observed downgrade of that composed tree. Preserve
  the reviewed union/version resolution when producing the final landing tree.

## N7 — new P2: redaction corrupts non-secret code and diff artifacts

Owner: reliability/redaction integration, with artifact and repair owners.
Source: `backend/backend/redaction.py:35` and `:66`;
consumer: `backend/backend/artifacts.py:102`.

The new generic key=value pattern was inserted into _HIGH_CONFIDENCE_PATTERNS.
redact_code applies that list before its existing quoted-literal-only handling.
It now treats unquoted source expressions as credentials:

```
Input:  api_key=os.environ["API_KEY"]
Stored: api_key=[REDACTED]"API_KEY"]

Input:  password=os.environ.get("DB_PASSWORD")
Stored: password=[REDACTED]"DB_PASSWORD")
```

Neither input contains a credential. Both inputs parse as Python; both outputs
fail ast.parse. Two independent tests reproduce changed bytes through real
ArtifactStore.put_text(kind=diff) and get. A third directly demonstrates the
syntax failure. All three fail strict assertions on the final reviewed SHA.
A read-only load of the pre-fix `7f520f3b` redactor preserves both expressions.

Impact: user-visible patch/diff evidence can be corrupted by normal source
syntax. This contradicts the documented parse-safe redaction contract and can
make a reviewed artifact disagree with the intended source patch. This review
does not establish that a corrupted artifact bypasses sealed Verify delivery
or is deployed to customers.

Required correction: keep free-text credential scrubbing separate from
code-aware quoted-literal redaction, preserving non-secret expressions and
artifact bytes. Retain and pass the new regressions. No correction was made
by this reviewer.

## Tests executed on the final product SHA

- Focused combined run: **192 passed, 4 failed, 0 skipped** (22.61 s).
  The four failures are N6's branch-head ordering assertion and the three N7
  artifact/parse assertions. This set includes the original strict F1–F10,
  compatibility and N1–N4 probes, queue/recovery/emergency/OFF suites, project
  isolation, hardening, worker drain, investigations, text/code redaction and
  database migration checks. Counts are for this run, not old RC2 evidence.
- Exact newer-upstream compatibility probes: **3 failed, 0 passed** (0.88 s).
  Two assert actual quota-reset times (#596); one asserts propagation of
  repository-declared protected_tests (#599). These are absent post-base main
  features, not newly introduced N1–N4 vulnerabilities.
- Separate transfer-manifest migration run at
  `308f45e4ae68956d837844e436fcf0fa4e6414d1`: **2 passed** (0.67 s).
  Do not combine these into the candidate's count.

The three original security test files are byte- and AST-identical to audit
`46143f9f`, with no skip/skipif/xfail attributes. See strict-provenance.json.
No original assertion was removed or weakened. The new independent legacy
control initially used Bob as an outsider, but setup_project deliberately makes
Bob a co-owner. That invalid fixture expectation is retained in the initial
69547d2f log; the final control uses an actual unrelated outsider with the same
404 assertion. It is not a production isolation defect or a selective rerun.

These are real local PostgreSQL/API/queue/artifact tests with owned fixtures and
scripted providers. There were no live Anthropic calls or customer enforcement.
Full backend/frontend/browser/release-readiness suites were not rerun for this
new SHA after the focused blockers were established; old RC2 totals do not
certify it. CI is a separate unverified gate.

## Migration and latest-main compatibility

Candidate migration chain remains 0015 → 0016 tenant_rls_intelligence → 0017
worker_heartbeats → 0018 execution_reliability → 0019 repository_disconnect.
Fresh upgrade, rollback/re-upgrade, single-head and populated 0017→0019→0017→0019
checks pass. Populated jobs, heartbeat data and RLS boundaries survive. The
actual migration scripts from upstream eea084bc can upgrade to the candidate.
No alembic stamp or fabricated migration state was used.

Read-only git merge-tree of current main and this candidate reports production
conflict in backend/backend/api/routes.py plus CHANGELOG.md. No resolution or
merge was performed. The imports conflict is small, but its correct union must
preserve current main's blank-SHA/default-HEAD behavior (#591), disconnect,
reconnect and authorization code. Preserve #596 quota timing, #597 storage
alerts, #599 protected-test propagation and sealed Verify delivery during the
actual integration. Three exact post-base regressions currently fail on the
candidate because those changes are not included.

Open overlapping upstream PRs observed: #601 protected existing tests, #598
installation-token/live-analysis/settings/deploy overlap, #593 reviewer settings
and PR automation, #592 delivery defaults, #600 E2E ordering and frontend #602/
#594. They are not presumed merged or reviewed by this audit.

## Per-PR merge approval matrix (no approvals issued)

Exact observed PR and transfer SHAs are in evidence/pr-matrix.json and the
transfer pins above. A green individual PR cannot approve the combined release.

- Upstream #412 `4ecd7558c20d676985294073d5a818a538bdf78c`: **HOLD**.
  Prerequisite; currently conflicting. Rebase/reconcile before composition.
- Upstream #458 `8396e836866e33ebc34a402a7c8bd0eb6bc769d8`: **HOLD**.
  Currently conflicting and not identical to this integrated implementation.
  Preserve provider ledger/finalization and its shared contract union.
- Fork #1 reliability `4f4f3f15d8498c074c6732a434b38377f077d83b`: **NO-GO for
  current release landing**. N3/N4 probes pass integrated, but N7 is introduced
  by this change and its transfer still pins older ae414985 without these fixes.
- Fork #2 E2E `247c23124ab167e8edf8cafac2d959f1d94b5ca0`: already **MERGED in
  fork**. Retain strict tests; no standalone production approval or new merge.
- Fork #3 observability `c30c631d99b192e528345baf2aba8e97b047bd94`: **HOLD**.
  Read-side behavior passes integrated. Transfer 6e6733a6 predates those fixes;
  test final API/contract union, including old-row history.
- Fork #4 hardening `65e33986fda0f0cf73b2f64ee1f7a4d7fa93338b`: **HOLD**.
  Shared lock helper/disconnect fix is real; finalizer glue is in integration.
  Transfer cfa06bf6 fixes migration numbering but predates that runtime fix.
- Fork #5 deployment/ops `c7c5336f79a9172b27e03d9570e7ae708b1f78b1`: **HOLD**.
  Preserve latest main's storage/readiness/auth union and validate the eventual
  deployment pairing; no deployment certification on this SHA.
- Fork #6 onboarding `9317b339cc006be88a4396a363f674151c9f8e47`: **HOLD**.
  N2 passes integrated. Transfer db8b1fd7 predates it; settle version union.
- Fork #7 validation `16fa812228e04e52e59d773a207075f781f51a17`: **HOLD**.
  Last gate after exact final composition. Prior local readiness totals do not
  cover N7 or certify current CI.
- Fork #8 integrated `77897e76eb561f166a2b96c1792545075bc8f078`: **NO-GO**.

Safe proposed sequence after corrections: current main → reconciled #412 →
reconciled #458 and reliability/provider-ledger contract union → observability →
hardening/finalizer lock union and 0019 → onboarding with preserved schema union
→ ops → validation/test bundle. Pin and test each resulting composition; this
is a dependency plan, not authorization to merge any current transfer.

The sequential manifest 308f45e4 is based on f8ab19e7 and deliberately omits
#412/#458 and integration-only finalization glue. Its migration proof is useful;
it cannot prove behavioral equivalence to this candidate or to latest main.
It also does not include the newly delivered N1–N4 changes. Exact final merged
production-tree equivalence remains unverified.

## CI and final release gates

At the final capture, PR #8 points to the reviewed SHA, mergeable=CONFLICTING,
statusCheckRollup is empty. The exact commit has zero check runs, zero commit
statuses and zero workflow runs visible through the GitHub API/CLI. Combined
status says pending because no status exists; that is not evidence of a running
or successful job. CI is **UNVERIFIED**, not green.

Required before merge signoff: fix N7; reconcile the transfer/version union and
latest-main conflict; preserve newer upstream semantics; pin the resulting
production composition; rerun original strict and new regressions plus combined
contract/integration checks; obtain passing required CI on that identical SHA.
Customer release additionally needs the approved staging/operational gates on
the final deployment. This review authorizes no deployment or customer use.
