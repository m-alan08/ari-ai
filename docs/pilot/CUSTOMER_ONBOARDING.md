# Customer onboarding checklist — pilot

Purpose: a new authorized customer goes from zero to a completed, explainable
assessment with only the access they approved. Owner runs each step with the
customer the first time; afterwards the checklist is self-serve.

Before starting, the customer receives [PILOT_SCOPE.md](PILOT_SCOPE.md) and
[DATA_HANDLING.md](DATA_HANDLING.md) and signs the pilot consent items in
[FIRST_FIVE_PLAN.md](FIRST_FIVE_PLAN.md).

## Step 0 — eligibility check (owner, ~10 min)

- [ ] Repository is Python-primary, ≤ 10,000 files, ≤ 32 MB.
- [ ] Customer owns the repository and can install a GitHub App on it.
- [ ] Customer accepts: Anthropic processes their code; fixes arrive as PRs
      or in-app proposals only; no auto-commit; Defense Lab is
      recommendation-only.
- [ ] Record consent in the customer record (METRICS.md).

## Step 1 — sign in (customer, 2 min)

- [ ] Open the console → "Sign in with GitHub" (OAuth via the Ari GitHub App).
- [ ] First login creates a personal organization `{login} (personal)` with
      the customer as owner — verified `api/auth.py`. This is their tenant
      boundary; no other customer can see it.

## Step 2 — install the GitHub App (customer, 5 min)

- [ ] Install Ari on the selected repositories (≤ 2 in pilot).
- [ ] Walk the permission request with the customer, verbatim:
      - **Contents: read & write** — read the exact commit for assessment;
        write only to deliver approved fixes as commits/PRs.
      - **Pull requests: read & write** — open fix PRs.
      - **Metadata: read-only**.
      - Tokens are least-privilege per call: read tokens get
        `{contents: read, metadata: read}`; write tokens get
        `{contents: write, pull_requests: write, metadata: read}` **pinned
        to a single repository**, GitHub-issued 1-hour TTL, cached ~50 min
        in memory, never persisted (`backend/github.py`).
- [ ] Webhook subscription: Push events only, HMAC-signed.

## Step 3 — connect the repository (customer, 3 min)

- [ ] Console → connect the installed repo. Connection queues a build-recipe
      PR (`ari/build-recipe`) that proposes how Ari builds/tests the repo in
      a sandbox. Customer reviews and merges it like any PR.
- [ ] If the recipe PR is `unsupported`/`invalid`, the repo needs human setup
      — owner assists; do not force it.

## Step 4 — approve the AuthorizedScope (customer + owner, 5 min)

- [ ] Create the scope record naming the allowed targets (sandbox builds of
      the customer's repos; category set = the three executable categories).
- [ ] Approval rules (verified `routes.py`): owners may self-approve
      **sandbox-only** scopes; anything broader requires a *different* owner
      — self-approval is rejected. Pilot scopes are always sandbox-only.
- [ ] Scope has an expiry date; note it in the customer record.

## Step 5 — monitoring opt-in (customer, 3 min; default OFF)

- [ ] Repo → "Automatic runs" switch (per repository).
- [ ] Project settings → "24/7 defense" switch.
- [ ] Optional: min-interval coalescing, record-only path globs, monitoring
      budget (default 150k tokens / $3 / 15 min per push).
- [ ] Fix delivery mode: **Pull request** (recommended) or **Manual**.
      Never select Auto-commit in the pilot.
- [ ] Explain: all four gates (App installed, repo switch on, project
      monitoring on, active scope) must hold before any push triggers work.

## Step 6 — first assessment (customer + owner, ~5 min)

- [ ] Pick a recent commit → start a manual assessment.
- [ ] Expected duration ~2–4 min. Watch the pillar loop live if desired.
- [ ] If the run fails, it stays visibly failed — we do not mark it green;
      owner re-runs or investigates.

## Step 7 — results walkthrough (owner, 20–30 min)

- [ ] Distinguish for the customer: *flagged* (model suspects) vs.
      *validated* (an executed check proved it) vs. *inconclusive*.
- [ ] Show evidence for each finding: source spans, check output, agent
      transcripts, SARIF/rules-of-engagement exports.
- [ ] Review any fix PR together; merge decision is always the customer's.
- [ ] Agree on remaining findings: fix, accept-risk (records an
      `AcceptRisk`), or dispute (feeds the false-positive metric).

## Revocation and control — what the customer can always do

Verified against code; tell the customer exactly this:

| Customer wants… | How | Effective when |
|---|---|---|
| Revoke Ari's access to the repo | Uninstall or suspend the Ari GitHub App, or remove the repo from the installation — on **GitHub's** side | Immediate: installation tokens stop working; fetches fail. Caveat: Ari's repo row is not auto-marked disconnected today — owner cleans up within 1 business day (RISK_REGISTER R-04) |
| Stop automatic assessments | "Automatic runs" switch off, or "24/7 defense" switch off, or revoke the scope | Next push: `auto_commits.schedule` / `assess_github_commit` re-checks every gate and declines |
| Cancel running work | Assessment → Cancel (`POST /assessments/{id}/cancel`) | Immediate; run ends cancelled, not green |
| Revoke the approved scope | Scopes → Revoke | Immediate; agent execution gates on active scope |
| Request deletion of their data | Email/support request (no self-serve exists) | Operator-run job over DB rows, artifact files and checkouts; target ≤ 30 days, see DATA_HANDLING.md §deletion |
| Turn off telemetry ingest (Defense Lab) | Revoke the project's `hkt_ing_` ingest token | Immediate; unsigned/unsigned-token payloads are rejected |
