# Automated release-review pipeline (Devin → GitHub CI → Codex)

Date: 2026-10-25 · From: Alan (integration) · Replaces manual prompt relaying between agents.

## What runs where

`push` to `integration/2026-11-02-release` triggers `.github/workflows/codex-release-review.yml`:

| Job | Does |
|---|---|
| `gate` | Dedup by exact SHA (marker comments on PR #8 + `[release-review report]` commit prefix), resolves the integration PR number and the upstream-mirror base SHA (`ci/upstream-main`) |
| `wait-ci` | Polls the commit's check-runs until every non-pipeline check settles. Zero checks → not green. Any failure → `ci_ok=false` |
| `strict-regressions` | Real Postgres; runs `live_defense_e2e` (F1–F10, N1–N7, R2–R5), `test_independent_corrected_review.py`, `test_rc2_transfer_manifest.py` (upstream merge equivalence) |
| `codex-audit` | `openai/codex-action@main`, exact-SHA prompt `.github/release-review/codex-audit-prompt.md`, structured output `.github/release-review/audit-schema.json`, `:workspace` profile + `drop-sudo`. Runs only if CI is green AND `OPENAI_API_KEY` is set |
| `report` | `route_findings.py`: posts one comment per actionable finding on the owner PR, fires the owner's Devin webhook automation (if `ARI_FIX_WEBHOOKS` set), posts one GO/NO-GO comment on PR #8, commits the report into `coordination/messages/` |

## Owner routing

`.github/release-review/routing.json` maps a finding's `component` → owner fork PR + owner Devin session. Unknown → `integration` (PR #8).

## Devin side

- Automation `Ari AI: wake integration lead on PR8 synchronize` (auto-11b0d0cd…) messages the lead session when PR #8 gets a new candidate.
- Seven `Ari AI findings router: <owner>` webhook automations wake each owner session. Their URLs/secrets go into the `ARI_FIX_WEBHOOKS` secret.

## Cost/retry limits

- One Codex call per SHA, ever (dedup marker + `cancel-in-progress` concurrency).
- Job timeouts: gate 5m, wait-ci 95m, strict 45m, audit 45m, report 10m.
- One webhook POST + one PR comment per finding.
- GITHUB_TOKEN pushes don't retrigger workflows; report commits are also prefix-gated — no circular reviews.
- No merge, deploy, or customer-enforcement action anywhere in the pipeline.
- Cap OpenAI spend at the provider (API key usage limits); recommend a project-scoped key.

## One-time setup

1. **Repo secret `OPENAI_API_KEY`** (fork repo Settings → Secrets → Actions). Without it the audit job skips and the report says CI-only NO-GO.
2. **Repo secret `ARI_FIX_WEBHOOKS`** — JSON `{owner_key: {url, secret}}` where owner_key ∈ execution-reliability, e2e-tests, investigation-observability, security-hardening, deployment-ops, customer-onboarding, release-validation. Optional; without it findings route via PR comments only.
3. **Adam**: `ci/upstream-main` mirror should track upstream main; integration lead refreshes it when upstream advances (the pipeline uses it as BASE_SHA).
4. Optional: add `pilot-verify.yml` ref dispatch for the candidate once Adam approves a hosted pipeline run.

## Verified live

Run on head `402b4888`: gate ✓, wait-ci waited correctly (e2e flake resolved on rerun), strict-regressions green, codex-audit skipped (no key), report posted `NO-GO` comment on PR #8 and committed `2026-10-25-codex-audit-402b4888….md`.
