# Launch decision — first five pilot customers

**Decision: NO-GO**

## Blocking conditions (2026-10-25)

- Leg 9 met the production delivery evidence condition: Ari-delivered PR #5 is open and unmerged, and its commit tree matches the Verify-tested diff. C9 remains open for Adam's personal review of #5. The independent review of #587 was posted after merge (comment `6395574613`).
- The Leg 7 production assessments were started by the synthetic account `rehearsal-alice`, not a real customer identity.
- The application now connects as non-superuser, non-`BYPASSRLS` role `ari_app`; the hosted cross-org drill passed after cutover.
- No genuine user has completed the intended end-to-end flow through the website.

Evidence: `docs/pilot/PILOT_FINAL_READINESS.md` on `docs/adam/pilot-final-readiness`.

Signed: Adam Issac, pilot launch owner — 2026-10-24; re-affirmed after the
production rehearsal leg 2026-10-25.
Code basis: Leg 8 ran production `497372ff` (#587); Run 2 used `95e90861`
(including #599). Leg 9 followed the successful `f2733519` deploy. Final
observed `/readyz`: ready, 24 workers, 0 queued.

**Customer 1 is not cleared.** Leg 9 proved the production delivery evidence
condition, but C9 remains open for Adam's personal review of PR #5. C6 still
requires the hosted browser journey with real GitHub sign-in and unaided
onboarding. Launch remains **NO-GO** until:

- C6's hosted browser journey with real GitHub sign-in and unaided onboarding
  passes.
- The Anthropic key and GitHub App private key are rotated by the owner from
  their consoles. The `postgres` admin password was rotated at about 09:32Z;
  the old password is rejected, and deploy `f5ce137b` uses the updated
  `MIGRATION_DATABASE_URL` reference.
- Public TCP proxy access is removed last. Rehearsal cleanup is **DONE**
  (approved and executed); see `DB_ROLE_MIGRATION.md` § Rehearsal cleanup.
- Adam signs off on C9 after personally reviewing PR #5.

## Rehearsal evidence retained under this NO-GO decision

- Onboarding, push→assessment, monitoring, opt-outs, scope approval/revoke,
  cancel, budgets, audit and tenant membership checks are all implemented
  and the hosted path is verified live (45-case baseline + two connected
  production repos). The app-role cutover and hosted cross-org drill are
  recorded in `DB_ROLE_MIGRATION.md`.
- The platform is honest under failure: transient failures stay FAILED,
  deferred work is queued-not-dropped, inconclusive findings are labelled.
- No claim in this package exceeds executed evidence.

## Outstanding launch conditions

Binding conditions — each maps to a documented gap. Customer 1 may not
start until every row marked Yes is done; C7–C8 are standing rules for the whole pilot.

| # | Condition | Blocking |
|---|---|---|
| C1 | Pilot scope held exactly as PILOT_SCOPE.md (Python, ≤2 repos, ingest caps, quota table) | Yes — scope discipline |
| C2 | Fix delivery restricted to `pull_request`/`manual`; `auto` never enabled for pilot orgs (watch audit log) | Yes |
| C3 | Defense Lab offered as recommendation-only/shadow; no control grants, no enforcement, simulation labelled | Yes |
| C4 | Deletion handled by an operator runbook with a ≤30-day SLA — `purge_project` merged (#535) and on prod — **DONE** | Yes |
| C4b | **DONE:** app connects as dedicated `ari_app` (`NOSUPERUSER NOBYPASSRLS`); the hosted cross-org drill passed after cutover. See `DB_ROLE_MIGRATION.md` | Yes |
| C5 | Customer consent record (FIRST_FIVE_PLAN.md) signed before any repo access, covering: data storage, Anthropic processing, write-scope token, flagged-vs-validated, no auto-commit, no enforcement | Yes |
| C6 | FIRST_FIVE_PLAN.md part 1 is open until the hosted browser journey passes with real GitHub sign-in and unaided onboarding; the API-only rehearsal with server-created accounts does not close it | Yes |
| C7 | Pilot claims track `main` only — anything not on the production `git_sha` is branch-only until merged and re-verified (#441 redaction-parse and #530 `jobs.last_error` redaction are merged and deployed) | Standing |
| C8 | Flagged ≠ validated is drilled at onboarding and in every report; Probe's four-template limit is always disclosed | Standing |
| C9 | **Delivery evidence met:** Ari-delivered PR #5 is open and unmerged, with a commit tree matching the Verify-tested diff. Independent review of #587 was posted after merge (comment `6395574613`). C9 remains open for Adam's personal review of PR #5. During the pilot, no agent may merge or close an Ari fix PR on a customer repo; only the customer's human may (no-touch directive posted to #559, comment `6395894348`) | Yes |
| C10 | **DONE with deviation:** Automatic starts count all project triggers toward 10/hour and 50/day; manual starts count only manual-trigger assessments toward 10/hour and 50/day, so the combined total could reach up to 20/hour and 100/day. The $50 rolling-day spend cap applies to all starts. Automatic runs are opt-in. Final verified Railway values are `PROJECT_ASSESSMENTS_PER_HOUR=10`, `PROJECT_ASSESSMENTS_PER_DAY=50`, and `ASSESSMENTS_USD_PER_DAY=50`. This differs from the owner's stated target of one pool shared by manual and automatic runs; owner decision pending on whether manual runs should count automatic ones too. | Yes |
| C10b | Pilot projects must use `delivery_mode=pull_request`; the rehearsal project was restored from `auto` to `pull_request` through the settings API before Leg 9 delivery. This does not close C9's personal-review gate | Yes |
| C11 | Sandbox capacity confirmed for the pilot. 19 assessments failed `HTTP 402 Hobby plan usage limit` between 10-20 and 10-23; none since, with about 9,800 sandbox-seconds on 10-24. Confirm which plan the sandbox team is on (D5) | Yes |
| C12 | **OPEN:** the `postgres` admin password rotation is complete. Still open: owner rotation of the Anthropic key and GitHub App private key from their consoles; remove public TCP proxy access last. `pg_monitor` was revoked from `ari_app` after #610 deployed; the storage report is healthy with WAL size unreported. Rehearsal cleanup is **DONE**; see `DB_ROLE_MIGRATION.md` § Rehearsal cleanup | Yes |
| C13 | Railway runtime documents and checks the code-intelligence variables (`INTELLIGENCE_REPOSITORIES_FILE` + one persistence option) so a redeploy cannot reproduce the D3 snapshot failure | Before customer 2 |
| C14 | **DONE:** #605 and #610 merged and deployed (`a85885aa`) | Yes |

## Documented exceptions (accepted for the pilot)

- No self-serve deletion or retention automation (R-03) — compensated by
  disclosure + operator SLA.
- RLS covers source-content tables only (`index_payloads`, `index_snapshots`);
  membership checks remain the boundary elsewhere (R-02). The `ari_app`
  cutover and hosted cross-org drill are verified in `DB_ROLE_MIGRATION.md`.
- No repo-disconnect endpoint (R-04) — GitHub-side uninstall is the real
  cutoff; ops cleanup follows.
- No billing/metering — pilot is free; pricing remains a proposal.
- Strict all-seven-roles live success remains partially proven — IDOR
  chains complete end-to-end; broad CWE validation does not. Disclosed.
- Support is founder-led with response *targets*, not SLAs.

## Additional criteria for reconsidering the NO-GO decision

Rehearsal step failures; any cross-tenant access observed; deletion runbook
not delivered; the live eval materially regressing (re-run the 45-case set
if any of the open PRs merge first).
