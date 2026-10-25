# Staging Rehearsal Report

- **Rehearsal date:** 2026-10-24
- **Code under test:** `adamissac/ari-ai` `main` @ `d0131167` (+ rehearsal test file on `docs/adam/pilot-launch` @ `c297b033` and later)
- **Environments:** (a) production — Railway API `ari-ai-production-76d4.up.railway.app` + Vercel frontend `ari-ai-lake.vercel.app` + real GitHub App + live Anthropic; (b) local real stack — FastAPI TestClient + real PostgreSQL + in-process worker + scripted `FakeTransport`.
- **Honesty rule (per Adam):** each stage is labelled `live Claude` / `scripted` / `sandbox` / `real I/O`. Nothing skipped is claimed as passed.

## Result summary

| # | Verification | Leg | Result |
|---|---|---|---|
| 1 | Tenant isolation (cross-org ID substitution) | Local real stack | **PASS** — 34 probes, uniform 404, no existence leak |
| 2 | Real execution / labelling | Local + prod | **PASS** — live leg executed: every agent run `provider_mode=live`; scripted stages labelled |
| 3 | GitHub safety (PR/manual only, scoped write tokens, no auto-merge) | Code audit + prod | **PASS (code-verified)**; prod fix-PR delivery pending |
| 4 | Monitoring: opt-in controls, limits, cancel, budget caps | Local + code | **PASS** |
| 5 | Defense Lab: recommendation-only | Code + tests | **PASS** |
| 6 | Data privacy: storage, provider processing, artifacts, deletion | Local + code | **PASS (documented)** — operator-run procedure verified |
| 7 | Reliability: failure recovery, persistence, restarts, UI errors | Local + suite | **PASS** |
| P1 | Prod journey: install → connect → index → live assessment → fix PR → verify | Prod | **BLOCKED — awaiting rehearsal repo + owner browser sign-in** |

## Leg 1 — Two-org isolation (real stack, scripted LLM)

**Test:** `backend/tests/test_pilot_rehearsal.py::test_two_org_isolation_by_id_substitution`.
Real FastAPI app + real PostgreSQL + real worker (`worker.drain`), two independent
personal orgs (alice+bob vs eve), full scripted seven-agent loop completed first so
every resource type genuinely exists.

**Probe matrix (eve, authenticated non-member, substituting org-A IDs):** project,
repo list/detail, commits, snapshots list+POST, scopes list, scope approve+revoke,
assessments list+POST+GET+runs+events+events/log+report+RoE+cancel, findings
list+detail+patches+accept-risk, artifacts (diff + LLM transcript), project events,
reassessments, approvals, defense-recommendations, defense-health, pilot-report,
ingest-tokens, applications, control-grants, context-pack POST, jobs, plus
fabricated `fnd_/asm_/art_` IDs.

**Result:** every probe → **404** (uniform not-found, no existence leak). Eve's own
API works (200) with empty lists — isolation, not broken auth. Context-pack POST on
a foreign project is denied. `provider_mode=fake` is asserted on every agent run —
scripted turns are identifiable in stored data, matching the "labelled" contract.

## Leg 2 — Monitoring controls, cancel, opt-out

`test_cancel_optout_and_reoptin_drills` + existing
`test_cancel_before_start_and_revoke_stops_assessments`:

- `POST /assessments/{id}/cancel` → terminal `CANCELLED`; second cancel → honest 409.
- `PATCH /repositories/{id} automatic_assessments=false` → subsequent webhook push
  yields **zero** reassessments; re-enabling restores the trigger.
- Scope `revoke` → outstanding assessment `CANCELLED`, new ones `SCOPE_DENIED`.
- Budgets (code-verified): 400k tokens / $10 / 1800s per manual assessment; 150k /
  $3 / 900s monitoring; 5 starts/hour; 60 req/min API; auto runs deferred at 5/h &
  20/d per project; $50/day global auto cap.

## Leg 3 — Operator deletion procedure

`test_operator_deletion_procedure` (direct-SQL teardown) + PR #535's own
`test_purge_project_deletes_every_surface` (all 17 adversarial tests pass —
verified by re-running them on the PR's code). **Finding:** foreign keys have
**no cascades** — there is no single-row delete. The canonical operator entry
point is `purge_project` (`backend/privacy.py`, PR #535 +
[TENANT_DATA_PROTECTION](https://github.com/adamissac/ari-ai/pull/535)),
which performs the org-scoped teardown below:

1. Delete org-scoped rows across every table having `organization_id` (enumerated
   via `information_schema`), child tables first (FK-safe order or a maintenance
   transaction).
2. Delete `repositories` and `projects` rows.
3. Delete disk objects: artifact files under `$ARTIFACT_STORE_URI` (**note:**
   content-addressed sharing means cross-tenant refcounting is required before
   unlinking — an open gap, R-11-adjacent) and the bare clone at
   `$SECURITY_WORKDIR/repos/{repo_id}.git`.
4. Verify: after the drill every endpoint (project, repo, assessment, finding,
   events) returned 404.

**30-day SLA enforcement (operational):** deletion requests are logged in
`coordination/DELETION_LOG.md` (to create) with timestamp + requestor; an operator
runs the procedure above, records completion, and the SLA clock is tracked by a
weekly automation checking open requests older than 25 days. There is no
self-serve deletion and no automated purge — this is disclosed in
`DATA_HANDLING.md`.

## Leg 4 — Reliability

- Worker-death job reclaim: `test_a_claim_held_by_a_job_that_stopped_running_can_be_reclaimed` — PASS (lease expiry lets another worker resume; no stranded assessment).
- Persistence: all assessment/agent/artifact/finding state is Postgres + artifact store; report served "from persisted records".
- UI error reporting: frontend `assessment-recovery.test.tsx` + error/retry states covered in the 135-case vitest suite; API returns structured `error.code` envelopes.
- `readyz` on production: database/queue/llm_mode=github_app/webhook_secret all healthy (checked 2026-10-24).

## Leg 5 — GitHub App permissions reconciliation

- Deploy doc previously claimed **Contents read-only** — wrong. Actual requirement:
  Contents **read+write** and Pull requests **read+write** at app level.
- Tokens are least-privilege per call: read tokens `{contents:read, metadata:read}`;
  write tokens `{contents:write, pull_requests:write, metadata:read}` pinned to a
  single repository; 1h GitHub TTL, ~50min in-memory cache, never persisted.
- Fix delivered via PR/manual delivery modes only; `auto` exists in the enum but
  is not enabled for pilot (pilot mode = manual/PR). Nothing merges or deploys.
- Doc fix committed to `docs/deploy/RAILWAY_AND_GITHUB_APP.md` (PR #527).

## Leg 6 — PR #441 (redaction parse fix) — reviewed, **merged**

Fixed a real defect: `redact()` broke Python syntax on 36 files, silently
dropping them from indexing. Merged to `main` (3d023d55). Residual gap
disclosed: unquoted values followed by more code on the same line are no
longer redacted; token-format patterns still catch real keys.

## Leg 7.5 — Live-provider + real-sandbox leg (executed 2026-10-24)

`backend/tests/test_pilot_rehearsal_live.py` (opt-in: `REHEARSAL_LIVE=1` +
`ANTHROPIC_API_KEY` + `VERCEL_TOKEN`). Same two-org real stack (Postgres,
API, worker), but `orchestrator_factory` live, `security_sandbox=vercel`
(prod driver), ledgerlite bound via `repository_bindings_file`.

**Result: PASS.** Assessment `asm_01M5QZY2E3Y0D2D44Q0PG6QBWX` on ledgerlite
`53851a4f` ran CREATED → SCOPE_CHECK → INDEXING → FUNCTIONAL_REVIEW → RECON →
… → **COMPLETED** in ~37s. Usage: **13 LLM calls, 30 tool calls,
$0.012649, 13.2s real Vercel Sandbox time**. Every `agent_run` reports
`provider_mode=live` (asserted — nothing scripted claimed).

Scout found the planted defect: `fnd_01M5QZYM2RB0QE96G3ZQQJ8J5G`
"GET /accounts/{account_id} may not enforce account ownership", CWE-639,
confidence 0.9, status **inconclusive** — honestly flagged, not presented
as sandbox-validated. Cross-tenant: eve → 404 on the finding and on every
transcript artifact of the live run. Known caveat: live runs need
`VERCEL_TOKEN` + `VERCEL_PROJECT_ID` + `VERCEL_TEAM_ID` env for the sandbox
driver (prod supplies these; the test documents the requirement).

## Leg 7 — Production journey (LIVE)

**Status: blocked pending owner setup** (requested 2026-10-24; session-cookie
sharing declined by owner — replaced with the authenticated-browser path below).

Prepared target (ready to push): `ledgerlite` FastAPI service with a planted
access-control defect (`GET /accounts/{id}` lacks the ownership check) so the
`access_control.ownership` template can validate end-to-end; 6 unit tests pass
locally at `/Users/devin/repos/pilot-rehearsal-target`.

**Owner setup actions (unchanged):**
1. Create GitHub repo `adamissac/pilot-rehearsal-target` — private, empty (no
   README/license).
2. Install the Ari GitHub App on it (or confirm the install already covers it).
   Minimum permissions for the full journey: **Contents read+write** (indexing +
   fix-PR delivery), **Pull requests read+write** (fix PR), **Metadata read**.
   A read-only install (Contents read + Metadata read) supports only the
   index/assess legs — the fix-PR leg would be skipped and reported untested.
3. Authenticate the rehearsal browser: open this session's **Desktop tab** and
   click SIGN IN → GitHub OAuth on the live VM browser. The session cookie
   stays inside the browser; it is never typed, extracted, or stored as text.
   (Alternative: an approved test-user GitHub login, same flow.)
4. Then the rehearsal drives the real UI journey in-browser — connect repo →
   index → live assessment → findings → approval → fix PR → verify → results.
   API-level probes (cancel, cross-ID checks) run via in-page `fetch` so the
   cookie never leaves the browser. Org-B probes on prod need a second GitHub
   login; without one, cross-tenant evidence remains the local leg's.

## Open findings for the launch decision

1. **No automated retention; deletion is operator-invoked** — `purge_project`
   performs the multi-table teardown (no FK cascades) + disk objects; artifact
   store needs refcounting before per-tenant unlink. Drill verified the data is
   gone afterward (uniform 404). No self-serve delete or TTL.
2. **4 executable Probe check templates** — `access_control.ownership`, `access_control.authentication`, `access_control.role` (PR #534, merged), `input_validation.bounds`; other CWEs are flagged-not-validated and must stay reflected in customer-facing claims (done in FEATURE_MATRIX).
3. **Isolation = membership checks + RLS on source-content tables** — PR #535
   adds FORCE RLS on `index_payloads`/`index_snapshots` via a transaction-local
   `ari.project_id` GUC (verified firsthand: 17/17 adversarial tests pass on the
   branch, including `test_rls_blocks_reads_without_tenant_context`). Membership
   checks remain the primary boundary elsewhere and are covered by the
   ID-substitution drill.
4. **Assistant-role turns are re-sent unredacted** to the provider (thinking-block
   signature constraint) — disclosed; mitigated by inbound redaction.
5. **Prod leg not yet executed** — this report is CONDITIONAL until Leg 7 lands.
6. **PR #535 (RLS + `purge_project`) MERGED to `main` (08114692)** — RLS +
   `purge_project` are now on `main`; verify the Railway deploy picked up
   migration 0016 (`readyz` + a tenant drill post-deploy) before relying on
   them in prod. Post-deploy drill note: RLS verification needs a
   **non-superuser** DB role — superusers bypass FORCE policies;
   `test_rls_blocks_reads_without_tenant_context` is the probe-role pattern.
7. **`jobs.last_error.message` leaks raw exception text to project members**
   (Agent-2 finding): `f"{type(exc).__name__}: {exc}"[:500]` is served via
   `GET /projects/{id}/jobs` — could carry secret fragments. The #530 agent is
   landing storage-boundary sanitization in `backend/redaction.py`; pilot
   condition until merged.
