# Pilot scope — what Ari covers for the first five customers

Defines the supported envelope for the pilot. Anything outside this list is an
explicit exclusion (bottom of document), not a "maybe".

## Supported repositories

| Dimension | Supported | Verified how |
|---|---|---|
| Primary language | **Python** (symbol/class/import/call-edge/route extraction, behavioral expectations, build recipes) | `code-intelligence/code_intelligence/extract.py` (tree-sitter-python); non-Python files ingest as `text` only (`ingest.py:295`) |
| Framework depth | FastAPI first-class (declared route extraction feeds security check targets and recipe drafting). Other frameworks get text-level indexing only. | `extract.py` route extraction; `backend/backend/recipes.py` drafts FastAPI recipes |
| Non-Python code | Searchable text index; no symbols, routes, or expectations. Findings may still be *flagged* by model analysis but cannot be *validated* by executed checks. | `ingest.py` language tagging |
| Repo size | Hard cap: **10,000 files and 32 MB** total ingested bytes; larger repos fail indexing (`IntelligenceError("Repository exceeds max_files")`) | `code-intelligence/code_intelligence/ingest.py:43-45,199` |
| Hosting | GitHub only, via the Ari GitHub App installation | `backend/backend/github.py` |
| Repos per customer | **≤ 2 repositories** for the pilot (operational cap, not enforced in code — enforce via onboarding and the ops report) | decision |

## Assessment quotas and limits

| Limit | Value | Mechanism (verified) |
|---|---|---|
| Manual assessment budget | 400k tokens / **$10** / 1,800 s wall clock per run | `shared/shared/tenancy.py` `Budget` defaults |
| Monitoring-run budget | 150k tokens / **$3** / 900 s per push-triggered run | `TriggerPolicy.monitoring_budget` default |
| Shared project review quota | 10 per rolling hour and 50 per rolling day across all triggers | `project_assessments_per_hour`, `project_assessments_per_day` |
| API rate limit | 60 requests/min per key (in-process limiter) | `backend/backend/api/deps.py` |
| Automatic (push-triggered) runs | Share the project quota; over-quota runs are recorded as skipped and not retried | `backend/backend/quota.py`, `auto_commits.py` |
| Global daily assessment spend | **$50/day across all projects and triggers**; over-cap starts are blocked | `settings.assessments_usd_per_day`, `backend/backend/quota.py` |
| Worker concurrency | 12 commits at once (`WORKER_PROCESSES`), Postgres `SKIP LOCKED` queue | `deploy/railway/start.sh`, `jobs.py` |

## Expected response times (measured, not promised SLAs)

- Manual seven-pillar assessment: **median 153 s** across the 45-case live
  baseline (2026-10-24); slow runs hit ~259 s when batched 4-up.
- Push → monitoring assessment complete: **~1–2 min**, verified live on two
  production-connected repos (`adamissac/mypypath`, `adamissac/ari-ai`).
- Typical run cost: **~$0.03/run** observed; the hard per-run cap is the
  budget above.
- Webhook → queue acknowledgment: immediate (202); a pushed commit is never
  lost because a run is deferred.

## What a pilot customer gets

1. Functional code review per commit (intent vs. behavior), with generated
   behavioral tests for defects it reports.
2. Seven-agent security pipeline on an approved AuthorizedScope: Scout →
   Probe → Trace → Diagnose → Patch → Fortify → Verify. Probe *executes*
   checks only inside the approved sandbox scope.
3. Signed-webhook push monitoring with opt-in automatic reassessments.
4. Verified fixes delivered as pull requests (or manual approval inside Ari —
   see exclusions: no auto-commit in the pilot).
5. Findings, approvals, evidence artifacts, agent transcripts, SARIF and
   rules-of-engagement reports per assessment.
6. Defense Lab in **recommendation-only / shadow mode**: anomaly evidence and
   defensive recommendations on the customer's own staging telemetry. No
   automated enforcement.

## Explicit exclusions (customer-visible)

- **Languages other than Python** for deep indexing (JavaScript/TypeScript
  symbol extraction is not implemented).
- **Executable security checks beyond three categories** —
  `access_control.ownership`, `input_validation.bounds`,
  `access_control.authentication`. Findings outside these are flagged with
  evidence but end `inconclusive` — they are not validated exploits.
- **Auto-commit fix delivery** (`DeliveryMode.auto`): exists in code but is
  excluded from the pilot; customers choose `pull_request` or `manual`.
- **RL-driven or automated enforcement**: the latest RL promotion candidate
  was rejected by the promotion gate; Defense Lab recommendations are never
  executed on customer infrastructure in the pilot.
- **Production telemetry enforcement / containment**: no network blocking,
  account disabling, or infrastructure mutation.
- **Self-serve deletion**: no DELETE API exists; deletion is an operator-run
  procedure (see DATA_HANDLING.md and OPERATIONS.md).
- **Paid plans / subscriptions**: pricing is a proposal page; nothing is
  purchasable or metered.
- **Third-party targets**: Ari only ever tests the customer's own
  repositories inside sandbox builds. It never touches third-party systems.
- **Multi-user orgs/invites**: each login gets a personal org (`{login}
  (personal)`) — verified `api/auth.py`. Pilot customers are one login =
  one org; team invitations are not implemented.
- **Repo sizes above 10k files / 32 MB**, and repositories that cannot be
  built in a Linux sandbox.
