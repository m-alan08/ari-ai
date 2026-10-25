# Release Gate & Pilot Verification — 2026-10-24

Owner: Adam (release reliability / test infrastructure). Branch: `feat/adam/release-gate`.

## Release SHA under gate
- `main` HEAD at doc freeze: `d0131167` — **stale**. PRs #535 (tenant RLS, migration `0016`), #537, #544, #549, #551, #553, #557, #560 merged since; re-read `main` for the real release SHA (see the trigger section).
- Gate PR #530 head `8b6bf3bd` — **all 19 CI checks green** incl. `backend-tests`, `intel-tests`, `review-tests`, `secagents-tests`, `rl-tests`, `e2e`, `docker-reviewer`, `docker-sandbox`, `compose-stack`, `backend-image`, `gitleaks`, `inventory-lite`, `payments-lite`, `audit`, `links`, `runner-semantics`, `rl-integration`, `static-and-unit`.
- Required checks = the job names listed below.

## Problem fixed (from PR #516 triage)
- `02cea327` added `paths` to the findings list API response; the generated `Finding` contract has `additionalProperties: false`, so the frontend transport's AJV validation rejected every findings response → e2e stack and dependent checks failed.
- Fix merged: `a1a48f87` reverted the response shape; the eval scorer resolves evidence paths from `source_span` evidence instead.

## Merge gate — required checks (exact job names for `main`)
| Workflow | Required check (job name) |
|---|---|
| backend.yml | `backend-tests`, `rl-integration` |
| frontend.yml | `static-and-unit`, `e2e` |
| code-intelligence.yml | `intel-tests` |
| code-review.yml | `review-tests`, `docker-reviewer` |
| security-agents.yml | `secagents-tests`, `docker-sandbox` |
| rl.yml | `rl-tests` |
| containers.yml | `backend-image`, `compose-stack` |
| examples.yml | `payments-lite` |
| qa-tools.yml | `runner-semantics` |
| research-bounds.yml | `audit` |
| docs.yml | `links` |

Recommended `main` protection: require all checks above, 1 approving review, dismiss stale reviews, branches up to date, PRs only, block force pushes and deletions.

## Path gates — visible skips instead of absent checks
Trigger `paths:` filters removed: every job always runs so a required check never sits at "Expected". Each job's first step runs `scripts/ci/path_gate.py` (diffs base→HEAD against the original globs) and writes `**RUN**`/`**SKIP** — <reason>` to the step summary; real steps are conditional on `run == 'true'`. Unknown base fails safe to RUN.

## Pilot verification pipeline — `.github/workflows/pilot-verify.yml`
Manual `workflow_dispatch` on a ref; one job per environment category so modes can't blur: static-unit | real-db | real-sandbox | browser-e2e (scripted fake LLM, labelled) | live-model (opt-in, authorized) | hosted-smoke | load-recovery. `verify-summary` (`if: always()`) prints a mode-labelled table; fails unless all real-environment gates passed and live-model/hosted-smoke are success-or-explicitly-skipped.

## Job queue — verified
Leases/heartbeats, retry backoff, `SKIP LOCKED` claims, dedupe idempotency, cancellation, lease-expiry crash recovery, SSE cursor monotonicity, artifact integrity: `test_jobs_queue.py`, `test_assessment_retries.py`, `test_idempotent_mutations.py`, `test_events_log_cursor.py`, `test_list_cursors.py`, `test_artifact_concurrency.py`, `test_redeliver.py`, `test_job_status_api.py`.

**New:** `GET /projects/{project_id}/jobs?status=dead,failed` — dead-letter/failed-job visibility for members. Test: `test_project_jobs_list_surfaces_dead_and_failed`.

## Load/recovery results (local, embedded PG)
`scripts/ci/load_probe.py`, workers=4, jobs=100 (run twice): no duplicate completions, attempts==1, drain ~0.06s; lease-expiry re-claim attempts==2; dedupe race → one created; `max_attempts=1` failure → `dead` row visible via filter. Provider limits and sandbox concurrency are the real ceilings; the probe measures queue mechanics only.

## Migrations
At doc freeze: 16 version files, single head `0015`; `test_parallel_migrations.py` asserted `["0015"]`. **Post-#535 merge**: `0016_tenant_rls_intelligence` is now main's head; **#533 must renumber `0016_worker_heartbeats` → `0017`** and update the head assertion (commented on the PR). Any PR adding a version file must update that test; multiple heads fail it.

## Eval harness (45 cases)
`score.py`/`score_api.py`: every case emits `scored: bool`; non-COMPLETED assessments emit `scored:false` + `unscored_reason`, excluded from all rates (previously counted as misses). `run.py`: `cases.json` validated at load; `summarize()` rates over scored cases only + `scored_cases: N/45` + explicit `unscored` list.

## Tracing (inventory)
delivery→assessment via `assessments.trigger`; assessment→agent run via `agent_runs.assessment_id`; run→finding→patch→verification via `finding_records.agent_run_id` → `patch_candidates` → `verification_results`; cost/latency via `agent_runs.doc.usage.usd` + timestamps. Gap: no unified trace view — correlate by `assessment_id`/`delivery_id`.

## Launch blockers
1. **`main` has no branch protection** (403 configuring — needs repo admin): set required checks + review rules above. Until then any push can break the pilot build.
2. live-model and hosted-smoke are opt-in; their absence reports `skipped`, never `success`.
3. CI run logs not accessible from this environment (API 401); gate verified locally.

## Evidence
- `pytest backend/tests/test_job_status_api.py backend/tests/test_jobs_queue.py backend/tests/test_parallel_migrations.py` → 12 passed.
- `ruff check` / `format --check` clean on all touched Python.
- `load_probe.py` report: all invariants true.

---

## Security review — `GET /projects/{id}/jobs` (2026-10-24)

- **Authentication**: `_user()` → `require_user()` → session cookie validated against `sessions.token_hash`; unauthenticated → 401. Verified by test (`client_factory()` bare → 401).
- **Tenant isolation**: `require_project(conn, user, project_id)` resolves project→org, non-members → 404 (no existence leak), viewer+ required. Verified by test (non-member → 404). Job rows are additionally filtered to `payload.project_id == :id` OR `payload.assessment_id` in the project's assessments — a job cannot appear under a project it does not name (verified: `probe_other` excluded).
- **Authorization scope**: read-only GET; no CSRF needed (safe method). Same access level as `GET /jobs/{job_id}` (viewer).
- **Pagination**: `limit` bounded `1..200`, newest-first, `next_cursor: None` — no unbounded scan, consistent with other list endpoints' shape.
- **`status` filter**: comma-split → parameterized `IN` clause; no injection surface.
- **`last_error` exposure — fixed at both boundaries (Adam's call, in this PR)**:
  - *Write time*: `jobs.fail()` and the lease-exhaustion dead-letter write persist `redact(error)` — secrets never reach `jobs.last_error` at all. `redaction.py` gained DSN credentials (`postgres://u:p@h`) and generic `password=`/`token=`/`secret=` key-value patterns; absolute paths and internal hostnames are deliberately kept for debuggability (documented in `redaction.py`).
  - *Read time*: non-owner members receive only `{code, category}` (ErrorCode prefix); owners receive the `redact()`-scrubbed full error with `trace` stripped.
  - *Tests*: `test_project_jobs_list_scrubs_secret_values_in_last_error` (API), `test_fail_scrubs_secrets_at_write_time` (persisted row + API), `test_jobs_list_member_sees_coarse_error_owner_sees_sanitized` (role split).
- **Compatibility**: additive route, no schema/contract change; does not collide with open routes.py PRs #524/#525 (distinct hunks, both reported mergeable).

## Branch protection — exact settings for the repo admin

GitHub → `adamissac/ari-ai` → **Settings → Branches → Add branch ruleset** (or classic "Add rule") for `main`:

1. **Target**: branch name pattern `main` (or default branch).
2. **Require status checks to pass before merging** — add these exact check names (job names, not workflow names):
   `gitleaks`, `links`, `backend-tests`, `rl-integration`, `intel-tests`, `review-tests`, `docker-reviewer`, `secagents-tests`, `docker-sandbox`, `rl-tests`, `backend-image`, `compose-stack`, `static-and-unit`, `e2e`, `payments-lite`, `runner-semantics`, `audit`
   — enable **"Require branches to be up to date before merging"**.
3. **Require a pull request before merging**: require **1 approving review**, enable **"Dismiss stale pull request approvals when new commits are pushed"** and **"Require review from Code Owners"** (if CODEOWNERS is in place).
4. **Block force pushes** and **block deletions** for `main`.
5. Optional: **Require conversation resolution**; do NOT enable "Allow specified actors to bypass" unless a break-glass lane is wanted — if used, restrict to admins only.

CLI equivalent (needs admin token):
```sh
gh api -X PUT repos/adamissac/ari-ai/branches/main/protection --input - <<'JSON'
{"required_status_checks":{"strict":true,"contexts":["gitleaks","links","backend-tests","rl-integration","intel-tests","review-tests","docker-reviewer","secagents-tests","docker-sandbox","rl-tests","backend-image","compose-stack","static-and-unit","e2e","payments-lite","runner-semantics","audit","inventory-lite"]},
 "enforce_admins":true,
 "required_pull_request_reviews":{"dismiss_stale_reviews":true,"require_code_owner_reviews":false,"required_approving_review_count":1},
 "restrictions":null,
 "required_linear_history":false,
 "allow_force_pushes":false,
 "allow_deletions":false}
JSON
```

## Triggering `pilot-verify.yml` on the final release SHA

After all merges, get the real final SHA and dispatch the pipeline — **never assume `d0131167`; re-read `main` at release time**:

```sh
SHA=$(gh api repos/adamissac/ari-ai/commits/main --jq .sha)
echo "release SHA: $SHA"
gh workflow run pilot-verify.yml -R adamissac/ari-ai \
  -f ref="$SHA" -f live_model=false -f hosted_url="" -f load_workers=4
# live-model gate (authorized, costs money):
gh workflow run pilot-verify.yml -R adamissac/ari-ai \
  -f ref="$SHA" -f live_model=true -f hosted_url="https://<pilot-host>" -f load_workers=8
```

Or GitHub UI: **Actions → pilot-verify → Run workflow** → set `ref` to the merge SHA, `hosted_url` to the pilot deployment, `live_model` only when Anthropic spend is approved. The `verify-summary` job then prints the mode-labelled verdict table and fails unless all real-environment gates pass.

## Cross-branch coordination (checked 2026-10-24, before merge)

- **PR #533 (pilot-readiness-ops)** adds migration `0016_worker_heartbeats` — since #535 merged first and claimed `0016`, #533 must renumber to `0017` + update `test_parallel_migrations.py` to `["0017"]`. Notified via PR comment.
- **PR #529** also edits `security-agents/evals/commits/score_api.py` (moves `_finding_paths` to module scope) — disjoint hunks from #530's results-loop rewrite; both coexist.
- **PRs #524/#525/#526/#523** (ari-security authz fixes) touch `routes.py` in-place at distant hunks — GitHub reports mergeable; no conflict with the appended jobs endpoint.
- Migration conflicts: the predicted #533/#535 `0016` collision was resolved by ordering — **#535 merged first**; #533 renumbers to `0017` on rebase.

## Evidence honesty

Everything under "Job queue — verified" and "Load/recovery results" was **executed locally** in this session against embedded PostgreSQL (pytest + `load_probe.py` output). The `hosted-smoke` and `live-model` gates are **not yet executed** — they exist as pipeline jobs and will report `skipped` until run with inputs. No hosted/production evidence is claimed anywhere in this document; CI results on PR #530 are the only remote execution evidence.
