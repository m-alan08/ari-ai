# Two-organization pilot rehearsal

Verifies the limited pilot end to end **before** any customer repo is connected:
two tenants on one control plane, real LLM provider, real Vercel sandbox, real
tenant isolation — no GitHub OAuth or owner credentials needed for the local run.

## Scope (agreed limited pilot)

- Python repositories only, at most two repositories.
- Controlled assessments (manually started), PR/manual fix delivery.
- Recommendation-only Defense Lab (shadow decisions; no enforcement).

## What this rehearsal proves

1. Two organizations coexist with tenant isolation on every read path.
2. An assessment runs to completion against the real provider in a real sandbox
   and produces genuine findings on a deliberately flawed target.
3. `owned_test` application registration + authenticated ingest path work.
4. Evidence is written to `scripts/rehearsal/evidence/` for the release record.

## One owner action remaining

The private rehearsal repository on GitHub must be created under the owner's
account and the Ari AI GitHub App installed on it — both are GitHub account
actions only the owner can perform:

```bash
gh repo create adamissac/ari-pilot-rehearsal --private
# push scripts/rehearsal/target-app as its contents, then install the App on it
```

Everything below that line works today without those steps.

## Local rehearsal (executable now)

From the repo root:

```bash
# 1. Boot the control plane + worker on embedded Postgres with the real provider
#    and the real Vercel sandbox driver.
APP_ENV=development AUTH_DEV_LOGIN_ENABLED=true \
SECURITY_SANDBOX=vercel VERCEL_TOKEN=$VERCEL_TOKEN \
VERCEL_PROJECT_ID=$VERCEL_PROJECT_ID VERCEL_TEAM_ID=$VERCEL_TEAM_ID \
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
REPOSITORY_BINDINGS_FILE=scripts/rehearsal/bindings.json \
make dev

# 2. In a second shell, run the driver.
.venv/bin/python scripts/rehearsal/run_rehearsal.py --base-url http://127.0.0.1:8000
```

`VERCEL_PROJECT_ID`/`VERCEL_TEAM_ID` are required by the Vercel Sandbox SDK
(alongside `VERCEL_TOKEN`) — resolve them read-only via `GET /v9/projects`
(`accountId` is the `team_*` id even on a personal account).

`scripts/rehearsal/bindings.json` maps the rehearsal repo id to the local
`target-app` checkout (trusted local binding — no clone needed):

```json
[{"repo_id": "repo_books_rehearsal", "local_path": "scripts/rehearsal/target-app", "subdir": null}]
```

Expected result: every step PASS; `evidence/rehearsal-run.json` written.

## Live-deployment rehearsal (after owner actions)

Once the private repo exists and the App is installed:

1. Owner logs in at `https://ari-ai-lake.vercel.app` (GitHub OAuth).
2. Connect `adamissac/ari-pilot-rehearsal`; the recipe PR lands on the repo.
3. Approve the AuthorizedScope; confirm automatic runs are ON.
4. Push a commit → the webhook fires an automatic assessment.
5. A second user/org (the "customer B") repeats the connect+scope flow on their
   own private repo; verify each sees only their own projects, findings,
   artifacts.
6. Post staging telemetry to `/api/v1/ingest/runtime/{project}` and confirm
   shadow `LivePolicyDecision` rows appear with `enforcement=NOT_EXECUTED`.

## Exit criteria for the release checklist

- [ ] Local rehearsal: all steps PASS, evidence committed.
- [ ] Live rehearsal: both orgs complete a controlled assessment; isolation holds.
- [ ] Cost logged (provider USD + sandbox minutes) — inside the approved cap.
- [ ] No enforcement attempted anywhere; Defense Lab stayed recommendation-only.
- [ ] Findings delivered via PR or manual report; nothing auto-merged.
