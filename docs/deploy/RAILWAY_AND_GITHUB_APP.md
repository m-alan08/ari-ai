# Hosting the real platform: Railway backend + GitHub App

The frontend runs on Vercel (`ari-ai`, https://ari-ai-lake.vercel.app). The control plane
(API, worker, migrations) runs as one Railway service built from `backend/Dockerfile` with
start command `railway-start` (`deploy/railway/start.sh`), a Postgres service and a volume
at `/data` (artifacts, repository checkouts). Secrets are entered in the dashboards by a
person, never committed and never pasted into chat.

## 1. Create the GitHub App (github.com → Settings → Developer settings → GitHub Apps → New)

| Field | Value |
|---|---|
| Name | e.g. `Ari AI` (must be unique on GitHub) |
| Homepage URL | `https://ari-ai-lake.vercel.app` |
| Callback URL | `https://ari-ai-lake.vercel.app/api/v1/auth/github/callback` |
| Request user authorization (OAuth) during installation | on |
| Setup URL | `https://ari-ai-lake.vercel.app/projects` (redirect on update: on) |
| Webhook URL | `https://ari-ai-production-76d4.up.railway.app/api/v1/webhooks/github` |
| Webhook secret | a long random string (also goes into Railway, below) |
| Repository permissions | Contents: **Read and write**, Pull requests: **Read and write**, Metadata: **Read-only** |
| Subscribe to events | Push, Pull requests, Repository |

Write access is the explicit, separately-established authorization: the App declares
read+write so a recipe/fix PR can be opened, but every routine read (fetch, compare,
commit lookup, recipe inspection) mints a **read-only** installation token, and a write
token is minted **only inside the delivery/recipe paths** and only scoped to the single
repository (`backend.github.GitHubApp.installation_token(write=True, repository=...)`).
Nothing else can push code. `installation` and `installation_repositories` events are
delivered to the App automatically (no subscription); `Repository` events let Ari follow
renames/transfers and repo deletion. Tell customers the write scope plainly during onboarding — see
`docs/pilot/CUSTOMER_ONBOARDING.md`.

| Where can it be installed | Any account (so other people can use it) |

After creating it: note the **App ID** and **Client ID**, generate a **client secret**, and
generate a **private key** (downloads a `.pem`).

## 2. Railway variables (project → `ari-ai` service → Variables)

| Variable | Value |
|---|---|
| `GITHUB_APP_ID` | App ID |
| `GITHUB_APP_PRIVATE_KEY` | contents of the `.pem` (multi-line paste is fine) |
| `GITHUB_OAUTH_CLIENT_ID` | the App's Client ID |
| `GITHUB_OAUTH_CLIENT_SECRET` | the App's client secret |
| `GITHUB_WEBHOOK_SECRET` | the webhook secret from step 1 |
| `ANTHROPIC_API_KEY` | your key (set a monthly spend limit in the Anthropic console) |
| `MIGRATION_DATABASE_URL` | Optional migration-only database role; defaults to `DATABASE_URL` |

Already set (non-secret): `DATABASE_URL` (reference to Postgres), `APP_ENV=production`,
`ARTIFACT_STORE_URI=file:///data/artifacts`, `SECURITY_WORKDIR=/data/work`,
`API_BASE_URL` and `FRONTEND_ORIGIN` = the Vercel URL, `RAILWAY_RUN_UID=0` (volume access).

## 3. Vercel (project `ari-ai`)

- `BACKEND_ORIGIN=https://ari-ai-production-76d4.up.railway.app`, and remove
  `NEXT_PUBLIC_DEMO_ONLY` so `/projects` is the real console again.
- Settings → Deployment Protection: turn Vercel Authentication **off** if people outside
  your Vercel team should be able to sign in.

## What runs where

Attacks never run on Railway. The worker fetches the selected repository at the exact
commit with a short-lived, read-only installation token, and executes it only inside a
throwaway Vercel Sandbox microVM (see `security-agents` Vercel Sandbox driver).

### Per-commit automatic assessments

The Railway entrypoint defaults `GITHUB_AUTO_COMMIT_AUTHORS` to empty, which disables
per-commit full runs. If a repository's `automatic_assessments` switch is ON, pushes
fall back to the legacy impact-gated, coalesced monitoring policy: project
`monitoring_policy`, active repository scope and the monitoring budget apply, with at
most one open monitoring assessment per repository. Set it to `*` to opt in every
pusher and commit author, including commits whose email maps to no GitHub account, or
use a comma-separated list of GitHub logins to limit eligibility. An installed App,
connected repository, enabled monitoring and approved repository scope remain
prerequisites for automatic runs. Approving a scope wakes blocked commits.

For strictly manual operation, switch `automatic_assessments` OFF per repository
(or disable project monitoring). Manual Run remains available when the repository
switch is OFF.

Eligible pushed commits are assessed subject to the shared project quota of 10 starts
per rolling hour and 50 per rolling day, plus the $50 global rolling-day spend cap.
Over-quota commits are recorded as skipped and are not deferred or retried.
`WORKER_PROCESSES` (default 12, ~80 MB each) runs that many commits at once; jobs are
claimed with `SKIP LOCKED`. Signed webhooks are never rate limited: GitHub does not
retry a 429, and the durable queue absorbs bursts.
Opening the commit page is unnecessary; it polls the persisted run when visited.
Check worker logs for `assess_github_commit` and the commit's monitoring assessment
before claiming live execution: `/readyz` only checks configuration and storage.

Full automatic assessments use the project’s normal assessment budget, clamped to
operator limits (`LLM_MAX_TOKENS_PER_ASSESSMENT`, `LLM_MAX_USD_PER_ASSESSMENT`; production
sets 4M tokens / $10). The legacy
monitoring budget remains for the impact-policy path when this rollout is disabled.


### Automatic/manual repository mode and Haiku 5.5 (#380)

Each connected repository has a persisted `automatic_assessments` switch, default
OFF (opt-in since #385). ON enables push-triggered processing: with a nonempty
author allowlist, every eligible new commit queues a full run; with an empty
allowlist, pushes use the legacy monitoring policy above. OFF prevents unstarted automatic work and
exposes manual Run on selected commits; active runs and history remain intact. GET/PATCH
`/api/v1/repositories/{repo_id}` returns Repository; PATCH requires a project owner
and is audited. Migration 0009 adds the flag; schema 0.15.0 adds RepositoryUpdate
and regenerates JSON/TypeScript. Deploy the backend and frontend together.

Per-commit full runs require a nonempty `GITHUB_AUTO_COMMIT_AUTHORS` allowlist; both
the signed push sender and GitHub-attributed commit author must match it. With the
empty Railway default, enabled repositories use the legacy monitoring policy above.
Attribution alone is email-derived, not authorization. All assessment triggers share a project
quota of 10 per rolling hour and 50 per rolling day, with a $50/day spend cap across
all projects and triggers. Over-quota automatic requests are recorded as skipped,
not deferred. Full runs use the normal project budget, clamped to operator caps.
Monitoring and approved sandbox scope remain prerequisites; repair approvals remain
required.

Production/staging gateway, Railway startup and frontend quick reads enforce
`claude-haiku-5-5` only, overriding stale production model choices. Errors are
reported instead of substituting another model. Historical recordings retain
the model that produced them. Live production run verification remains outstanding.

### Security agents against this repository

`build_recipe.json` makes the real backend the sandbox target: `deploy/sandbox_target.py`
starts PostgreSQL in the Vercel microVM, applies migrations and seeds two unrelated
fixture users, so Probe/Verify can call the real API as either user and Patch/Verify can
run `backend/tests/test_api_core.py`. The recipe's `sandbox` block lists the apt packages
and exact-pinned wheels installed while egress allows only PyPI and the HTTPS Ubuntu
mirrors, and the paths uploaded; Scout only surveys routes under those paths.

### RL defense policy

The image ships CPU-only torch and the retrained protocol-v1 policy
(`rl-defense/results/protocol-v1-retrain/`); `start.sh` pins its path, topology and sha256.
`POST /api/v1/projects/{id}/defense-recommendations` enqueues `rl_recommend`.
