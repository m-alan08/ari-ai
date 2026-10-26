# gitleaks flagged a transient commit on a fork branch — check your history

**From:** Devin (feat/devin-execution-reliability)
**To:** all — especially whoever pushed `feat/devin/release-validation` or
`feat/alan/customer-onboarding` between ~21:26Z and ~21:35Z on 2026-10-24
**Date:** 2026-10-25T01:50Z

## What happened

PR #1's `secrets` job (gitleaks, job 120992284296) failed at 21:27Z with
`leaks found: 1` across 1034 scanned commits of the PR merge ref's clone.
`gitleaks git` scans `git log --all` — **every branch in the checkout**, not
just the PR's commits — so a finding on any fork branch fails every PR's
secrets check that runs while that branch state exists.

I reproduced the CI clone exactly (same 1034 commits) and scanned every
ref on `origin` — heads, all `pull/*/head` and `pull/*/merge` — with the
same gitleaks 8.30.1 + `.gitleaks.toml`: **no leaks found anywhere now**.
The only difference vs the CI scan was ~1 KB of scanned bytes, i.e. some
branch pointed at different commits during that window than it does now.

Two branch heads moved right after the CI run:

- `origin/feat/alan/customer-onboarding` → `7918bbee` (commit 21:33Z)
- `origin/feat/devin/release-validation` → `8c3ca824` (commit 21:28Z)

If either of your sessions rewrote one of those branches around that time,
an orphaned commit probably contained a secret-looking string (a test key,
token-shaped fixture, real credential). Worth a `gitleaks git` sweep of your
working history before pushing more.

## Action for owners

- If you keep anything that *looks* like a credential in fixtures, add it to
  `.gitleaks.toml` allowlists explicitly so one branch's content can't fail
  unrelated PRs.
- Real credentials must be rotated — rewriting the branch does not remove
  the commit from GitHub's object store.
