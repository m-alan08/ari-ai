# Pilot Security Gate — Design-Partner Readiness Checklist

Scope: customer-facing security boundaries of the control plane (backend API, GitHub
integration, agent-tool and sandbox boundaries, artifact access, deployment config)
ahead of the first external design-partner pilot. Audited against the live code on
`upstream/main` plus the fixes on `feat/devin-pilot-security-hardening`.

Findings proven by code path + regression test are **confirmed**; items labelled
*theoretical* were inspected but not reproduced into an exploit path.

## Confirmed vulnerabilities fixed on this branch

| # | Severity | Finding | Fix | Proof |
|---|----------|---------|-----|-------|
| 1 | **P1 (was pilot-blocking)** | `upsert_user` matched identity by mutable `github_login` only. A victim renaming their GitHub account frees the login; an attacker registering it would inherit the victim's user row, memberships, and projects (the code even overwrote `github_id` on the victim row). | Identity is anchored on immutable `github_id`; the stale login is detached (`released:<id>`) on collision and the event is audit-logged; dev-login rows (NULL github_id) still link on first OAuth sign-in. | `TestAccountIdentity` in `backend/tests/test_customer_security_hardening.py` |
| 2 | **P1/P2 (was pilot-blocking)** | `POST /projects/{id}/repositories` with `installation_id: null` connected *any* `owner/repo` name with no proof of access; the global `uq(provider, full_name)` constraint then 409'd the legitimate owner forever (name squatting / availability attack) and let users attach repos they cannot access. | Name-only connects are rejected outside dev/test; App connects still require `github_repo_access` proof, now refreshed from GitHub at connect time (stale-access window). | `TestRepositoryConnect` |
| 3 | **P2 (pilot-blocking)** | No repository disconnect existed. Once connected there was no way to stop repo-driven work or revoke read access short of uninstalling the GitHub App. | Canonical model `repositories.disconnected_at` (migration `0017`, linear after execution-reliability `0016`): `POST /repositories/{id}/disconnect` + `/reconnect` (owner). Disconnect blocks snapshots, assessments, push automation, and live commit reads; revokes deploy→commit mappings; cancels queued repo jobs; marks pending/indexing snapshots failed; cooperatively cancels in-flight assessments. `installation_id` is kept as the access record, not the revocation signal. Reconnect re-verifies installation access. | `TestRepositoryDisconnect` |
| 4 | P2 | Rate limiting was applied only by `_user()` in `routes.py`; every session-authed route on the `runtime`, `incidents`, `github_repos`, `intelligence`, and `live_defense*` routers bypassed it — including endpoints that call the GitHub API per request (`/repositories/{id}/commits`, `/github/repositories`). | The per-user limit (60/min) moved into `require_user`, so it covers every authenticated route on every router; added dedicated budgets for expensive enqueues (snapshots 30/hr/user, RL recommend 20/hr/user, connects 20/hr/user, OAuth callback 30/5min/IP). | `TestRateLimits` |
| 5 | P2 | `github_repo_access` was only refreshed at sign-in — revoked repository access could be exercised until the user signed out/in. | Personal-installation access is refreshed from GitHub inside `connect_repository`/`reconnect_repository` before the `can_connect` check. Org-installation staleness remains (see residual risks). | covered by connect-path code + existing `test_github_repo_access.py` |
| 6 | P2 | SSE assessment stream checked authorization once at connect; a removed member kept receiving project events for the session TTL. | The stream re-runs the membership check every 15 s and closes on revocation. | code review of `stream_events`; membership revocation proven by `TestStaleAuthorization` |
| 7 | P3 | `ArtifactStore.put_bytes` defaulted `redacted=True` although it performs no redaction — the DB flag claimed redaction that never happened. | Default is `False`; `put_text`/`put_json` pass `True` only after `redact_*`. | `TestDataHandling.test_raw_bytes_artifact_is_not_flagged_redacted` |
| 8 | P3 | `/api/openapi.json` was exposed in production. | Gated to dev alongside `/api/docs`. | `test_openapi_schema_hidden_outside_dev` |
| 9 | P3 | Sign-ins produced no audit events (only first-time `user.created`). | `auth.login` audit events (method dev/github) under the user's personal org. | audited path exercised via dev login in suite |

## Verified protections (checked in code and/or existing tests)

- Webhook endpoint: constant-time HMAC-SHA256 over the raw body, bounded read, header
  format allowlist, delivery-id dedupe, payload stored redacted, `401` on bad signature
  (`test_invalid_webhook_signature_rejected`, `test_webhook_monitoring.py`).
- Sessions: opaque tokens stored sha256-only, expiry enforced, HttpOnly + SameSite=lax
  + Secure (non-dev), revoked on logout. CSRF required on every non-GET route via
  `X-CSRF-Token` (`test_csrf_required_for_mutations`).
- Authorization: `require_project` resolves project→org membership per request with
  viewer < member < owner; non-members get 404 (no existence leak). Membership
  removal takes effect immediately (`test_removed_member_loses_access_immediately`).
- Artifacts: content-addressed sha256 with integrity re-check on read, project-scoped
  authorization, nosniff + attachment download, cross-project access 404
  (`test_cross_project_artifact_access_denied`).
- Scopes/approvals: owner-only approval, requester ≠ approver, scope expiry, and the
  approval gate cannot be bypassed by drill/assessment APIs.
- Ingest tokens: stored sha256, revocable, optionally bound to a connected
  application, quarantine of secret-like payloads, per-token 60/min.
- Connected applications: disconnect endpoint exists and revokes bound ingest tokens;
  first-responder control paths check `disconnected_at`.
- Delivery write path: installation tokens are short-lived and repository-scoped;
  commits signed pushes are gated by sender allowlist + installation-id match +
  `automatic_assessments` opt-in + monitoring policy + per-hour/day/global ceilings.
- Checkout pipeline: `full_name`/SHA format validation, token passed via
  `GIT_CONFIG` env (never argv/logs), atomic binding with flock.
- Audit log: per-org hash-chained, append-only, server-side `verify_chain`.
- Secret redaction: regex set for `sk-ant`, `gh[pousr]_`, `github_pat_`, `AKIA`,
  `xox`, PEM keys, `Bearer …` applied to artifact text/JSON and webhook payloads.
- LLM/model pinning: `model_for` pins a fixed model in prod/staging (no caller choice).
- Frontend: same-origin `/api/v1` proxy with header allowlists, SSE streaming, CSRF
  token held in memory only.

## Unverified protections (need evidence before pilot sign-off)

- **In-process rate limiter** is per-API-process: under a multi-replica deployment
  the effective limit multiplies by replica count. Acceptable for a single-process
  pilot; move to Redis/Postgres before horizontal scaling.
- **Agent sandbox isolation** (`deploy/sandbox_target.py`, security-agents drivers):
  code was reviewed for the scope/egress gates but no adversarial breakout test was
  executed this pass. Run the existing sandbox e2e + a hostile-tool test before pilot.
- **Org-type GitHub App installation refresh**: `github_repo_access` for org
  installations is still refreshed only at sign-in (see residual risks).
- **E2E of OAuth + App install flow**: the OAuth callback, state cookie, and
  install-bounce paths were reviewed but not exercised end-to-end in this pass
  (no GitHub credentials in CI).

## Blocking vulnerabilities

None outstanding on this branch. The three pilot-blocking items (identity takeover,
unauthenticated repo connect, missing disconnect) are fixed and regression-tested.

## Required operator procedures for the pilot

- `APP_ENV` must be `staging` or `production` (never `development`/`test`): this
  disables dev login, the docs/OpenAPI surface, and name-only repo connects.
- `AUTH_DEV_LOGIN_ENABLED` must remain unset/false in the pilot environment.
- Set `GITHUB_WEBHOOK_SECRET`, OAuth client id/secret, and the App private key via
  the secret store; never commit `.env`.
- To fully revoke a partner's repo access, have them (or the operator) uninstall /
  restrict the GitHub App installation — server-side disconnect stops our reads
  and jobs but cannot revoke the installation token GitHub-side.
- Run the API as a single process until a shared rate limiter exists.
- Review `released:<id>` placeholder logins and `user.login_released` audit events
  if a customer reports a GitHub rename.

## Residual risks

- `control_channels.hmac_secret` is stored plaintext (required for HMAC signing of
  control commands). DB exfiltration exposes live channel secrets — mitigate via DB
  encryption at rest / strict DB access; flagging for the platform roadmap.
- Org-installation repo access refreshes only at sign-in; revoked access can be
  exercised until the next sign-in for org-installed apps (personal installs are
  now refreshed at connect/reconnect).
- Project-scoped SSE recheck runs every 15 s — a revoked member can receive up to
  one extra poll interval of events.
- In-flight assessment jobs are cancel-requested cooperatively (worker exits at
  the next step boundary); they may run a bounded amount of extra work first.
- Disconnect is Ari-side only: it does not uninstall the GitHub App on GitHub —
  full revocation is the customer's App-side action.
- `disconnected_at`/`installation_id` semantics differ from fork PR #6's draft
  (`installation_id IS NULL`); PR #6 must adopt the canonical column (see handoff).
- Rate limiting is best-effort under multiple API processes (see above).
