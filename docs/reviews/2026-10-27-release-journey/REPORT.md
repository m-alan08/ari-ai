# Release journey validation — 2026-10-27

Base: `adamissac/ari-ai` `main` @ `c03a5501`. Branch: `devin/1793143652-release-journey-validation` (fork `m-alan08/ari-ai`).
Scope of this session: new release-journey tests and this report. No product code changed.

## Verdict: NO-GO until the review-pipeline fix for the default `*[bot]` exclusion lands

One P1 on `main` silently drops automatic reviews for ordinary users. Its fix exists on
[m-alan08/ari-ai#12](https://github.com/m-alan08/ari-ai/pull/12) and was validated here, but it is unmerged.
With #12 merged, no remaining P0/P1 was found on the paths exercised below. Live GitHub
OAuth, a live GitHub App installation, and a live model were **not** exercised.

## Findings

| Sev | Finding | Status | Owner |
|---|---|---|---|
| P1 | The default reviewer setting `exclude_authors=["*[bot]"]` is passed to `fnmatch`, which reads `[bot]` as a character class. Any login ending in `b`, `o` or `t` (`bob`, `octo`, `scott`) is skipped with `skipped: author excluded by reviewer settings` while the automation status still reads `ready`. | Reproduced on `main`. Fix on #12 @ `6223f419` turns the strict-xfail regression into XPASS(strict). | Review-pipeline session (`backend/backend/auto_commits.py`) |
| P2 | After connecting an additional repository from Settings, where the user lands is unpredictable: the project shell selects the first live row of `GET /projects/{id}/repositories`, which has no `ORDER BY` (`backend/backend/api/routes.py::list_repositories`), so with no `?repo=` the shell sometimes stays on Settings and sometimes redirects to the new repository's Setup. Observed both ways in the real stack: Settings in an isolated run, Setup in the full-suite run (locally and in CI). Opening the project with an explicit `?repo=<id>` is deterministic and is what the spec now does. | Reproduced in the real-stack Playwright run (full suite). Not fixed: default selection/navigation is UX lane; the missing ORDER BY is backend and additive. | First-customer UX session (navigation); backend owner (list order) |

Findings from sibling sessions are in their own reports (security audit: disconnect-during-delivery,
fixed on #11).

## Journey coverage

| # | Area | Evidence | Kind |
|---|---|---|---|
| 1 | GitHub sign-in and installation | `test_sign_in_lists_installed_repositories_and_connects_them_into_one_project` — OAuth state cookie, code exchange, `/me`, installed-repository picker | Backend, GitHub HTTP mocked |
| 2 | Connect / reconnect | Same test + `test_uninstall_blocks_reconnect_until_the_app_is_reinstalled_and_user_reauthorizes`; idempotent re-connect; reconnect leaves automatic reviews off | Backend + real-stack Playwright |
| 3 | Onboarding and completed setup | `release-journey.spec.ts` (setup per repository, survives reload); existing `onboarding.spec.ts`, `pages.spec.ts` | Real-stack Playwright |
| 4–6 | Manual review, findings/evidence/history, patch → Verify → approval | Existing `journey.spec.ts` in the full E2E run | Real-stack Playwright, scripted model |
| 7 | Automatic reviews on new commits and branches | `test_reauthorized_repository_reviews_new_branch_commits_and_disconnected_sibling_does_not` (non-default branch push schedules `assess_github_commit` with the new installation id); bot-exclusion regression; signed push in `journey.spec.ts` | Backend (real webhook HMAC, queue, worker) |
| 8 | Disconnect and reauthorization | Uninstall detaches access and blocks reconnect until App reinstall + fresh sign-in; disconnected repo refuses setup writes (409) | Backend + real-stack Playwright |
| 9 | Multi-repository project | `test_each_repository_keeps_its_own_setup_and_lifecycle`; `release-journey.spec.ts` | Backend + real-stack Playwright |
| 10 | Frontend/backend contracts | `shared.export --check`, `shared.examples --check`, transport build, `tsc`; every `/api/v1` path the frontend calls (`frontend/lib`) matches an OpenAPI route (remaining unmatched strings are page routes) | Static + generated-contract checks |

## Commands and results (this VM)

- `uv run pytest -q -p no:cacheprovider backend/tests` — 879 passed, 9 skipped (base, before new tests).
- `uv run pytest -q backend/tests/test_release_journey.py` — 4 passed, 3 xfailed (strict) on this branch; 7 passed in a tree that also contains the review-pipeline fix (adamissac/ari-ai#693 / m-alan08#12 @ `6223f419`).
- `ruff check`, `ruff format --check`, `mypy`, `shared.export --check`, `shared.examples --check` — clean.
- Frontend: lint, typecheck, unit (109 files / 472 tests), production build — pass.
- `tests/e2e/stack.py` full suite (base) — 56 passed, exit 0. `release-journey.spec.ts` — 1 passed.

## Not executed

- Real GitHub OAuth against github.com, real App installation/uninstall webhooks, real PR review delivery.
- Live Claude provider; Docker sandbox execution (no daemon on this VM).
- `code-review/`, `security-agents/`, `code-intelligence/` package suites (not in this journey's scope).

## Merge-order note

No action needed in either order. `test_default_bot_exclusion_does_not_skip_human_logins` is a
strict xfail only while `backend.auto_commits._matches` lacks the `literal_brackets` option
introduced by #693; once that lands the same test is a hard assertion (verified: 7 passed on
canonical main + #693 + #694 + this branch). A plain strict xfail failed with XPASS(strict) in
that combined tree, which is why the marker is conditional.
