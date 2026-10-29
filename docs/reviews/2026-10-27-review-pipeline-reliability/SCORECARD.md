# GitHub push-to-review reliability scorecard — 2026-10-27

Base: `adamissac/ari-ai` main @ `c03a5501`. Scope: the existing per-commit automatic review
path (`api/webhooks.py` → `process_github_delivery` → `auto_commits.schedule` →
`assess_github_commit` → assessment → `post_pr_review`). No new agents or review features.

"Verified" means an automated test ran against the real Postgres queue/DB with GitHub stubbed
at the `GitHubApp` boundary. Real GitHub was not called.

## Defects found and fixed

| # | Defect (reproduced on main by a failing test) | Fix | Test |
|---|---|---|---|
| D1 | A PR opened/synchronized after its head commit's push was deduped onto the push's commit job, so the PR never got a review comment. Push normally arrives first, so most per-commit PR comments were lost. | `schedule` enqueues a PR-scoped linking job when the commit job already exists; `assess_commit` attaches `pr_number` to the commit's event and enqueues the review if the assessment is already done. Locks the assessment row against the host's completion transition. Still one assessment per commit. | `test_pull_request_after_push_of_same_commit_still_gets_review`, `test_pull_request_for_already_completed_commit_posts_review` |
| D2 | `post_pr_review` posted to repositories disconnected after the assessment finished. Disconnect cancels jobs by `repo_id`, and the review job carries only `assessment_id`. | Checks `disconnected_at` and no-ops; audited as `monitoring.pr_review_skipped`. | `test_pr_review_is_not_posted_after_repository_disconnect` |
| D3 | PR review posts treated GitHub 429 / secondary rate-limit 403 as permanent 4xx failures, so the review was dropped. | Rate limits defer (`JobDeferred`, Retry-After / reset, ≤1h, ≤24 defers) and then use the normal retry budget. | `test_pr_review_rate_limit_is_retried_not_dropped[429,403]` |
| D4 | GitHub rate limits on the commit lookup used up the 5-attempt retry budget (about 31 s total) against resets of up to 1 h, then dead-lettered the commit. | Same bounded deferral, which does not spend retry attempts. | `test_github_rate_limit_defers_commit_without_burning_attempts[403,429]` |
| D5 | When the commit lookup kept failing (5xx/network), the commit ended `dead` in the queue with no event or reason visible to the user. | The last attempt records a skipped event/request with the cause ("start a manual review to retry") and an error log. | `test_exhausted_transient_github_failure_is_recorded_for_the_user` |
| D6 | Redelivering a webhook whose processing job was dead, failed, timed out or cancelled returned "duplicate", so the push could never be recovered. | `_persist` locks the delivery row and re-enqueues `gh:{delivery}:after:{old_job}`. Live or succeeded jobs still return a duplicate. | `test_redelivery_after_dead_processing_job_is_processed_again`, `test_redelivery_of_successful_delivery_stays_duplicate` |
| D7 | The default `exclude_authors=["*[bot]"]` was read by fnmatch as a character class, so every login ending in b, o or t was skipped (found by the release-journey session). | Author patterns treat `[` literally. Branch patterns are unchanged. | `test_default_bot_exclusion_does_not_skip_human_logins[bob,octo,matt,scott]`, `..._still_skips_bots` |
| D8 | Revocation race: `post_pr_review` checked `disconnected_at` in one transaction and called GitHub afterwards, so a disconnect committed in between still got a review; review jobs carried no `repo_id`, so disconnect could not cancel them (Codex independent review). | Review jobs carry `repo_id` (disconnect cancels queued/running ones); right before the POST the handler locks the repository row with the same `FOR UPDATE` as `/disconnect` and re-reads `disconnected_at`/`installation_id`, so a disconnect that committed, or is committing, is seen. The lock is released before the network call. `assess_commit` re-checks `disconnected_at` under its existing repository lock. | `test_disconnect_committed_after_precheck_still_blocks_the_review_post[False,True]`, `test_disconnect_cancels_queued_pr_review_jobs` |
| D9 | A lost GitHub response (timeout after the POST was sent) was retried, so GitHub could receive the review twice (Codex independent review). | The POST runs inside the queue's effect guard (`JobContext.effect`): an HTTP response means nothing was created and the job keeps its retry/deferral path; a transport error leaves the job `recovery_required` for an operator, never replayed. | `test_lost_review_response_is_never_replayed`; existing rate-limit and 4xx tests still pass |
| D10 | Several pull requests sharing one head commit shared the single `pr_number` on the commit's event and one `prreview:{assessment}` job, so only the first PR got a review (Codex independent review). | Every further PR gets its own event under the commit's request; one `post_pr_review` job per PR (`prreview:{assessment}:pr:{n}`, PR number in the payload). | `test_two_pull_requests_sharing_a_commit_both_get_reviews` |
| D11 | Duplicate review delivery: a `post_pr_review` job queued before per-PR keys (`prreview:{assessment}`) and the new `prreview:{assessment}:pr:{n}` job both posted to the same PR; nothing stopped two jobs that reach the same review under different keys. | `_enqueue_pr_review` skips the first linked PR when a legacy job exists; `post_pr_review` checks, under the assessment row lock right before the POST, for a `pr_review_posted` audit or a sibling job whose effect for the same review is settled (skip, audited) or in flight (defer 15 s, oldest job wins). | `test_legacy_review_job_and_per_pr_job_post_once` |
| D12 | Concurrent identical webhook deliveries: the race losers were acknowledged as duplicates with `job_id: null`, so GitHub's redelivery view could not show which job owns the delivery (processing itself happened once). | The loser re-reads the winner's `job_id` after the `ON CONFLICT` wait and returns it. | `test_concurrent_identical_deliveries_process_once` (4 simultaneous deliveries → 1 row, 1 job, 1 assessment) |

## Path scorecard

| Path | Status | Evidence |
|---|---|---|
| HMAC over raw body, wrong secret, tampered body → 401, nothing persisted | Verified | new `test_tampered_body_...`; existing `test_webhook_monitoring.py` |
| Pushes to default and non-default branches reviewed; tag pushes ignored | Verified | `test_non_default_branch_push_is_reviewed_and_tag_push_is_not`; existing branch-filter tests |
| Duplicate delivery (sequential) → 200 duplicate, one assessment | Verified | existing `test_auto_commits.py`, new redelivery tests |
| Duplicate delivery concurrent HTTP race (`ON CONFLICT DO NOTHING` branch) | Verified (D12) | `test_concurrent_identical_deliveries_process_once` |
| Redelivery after a dead or failed processing job | Verified (D6) | above |
| Worker transient retry, then a user-visible record after exhaustion | Verified (D5) | above; existing `test_transient_commit_lookup_still_retries` |
| Lease loss / worker crash mid-job | Verified by existing suite | `test_jobs_queue.py`, `test_execution_reliability.py` (not re-scoped here) |
| GitHub rate limits (commit lookup, PR review) | Verified with stubbed headers (D3/D4) | header shapes follow GitHub docs and were not observed live |
| LLM provider timeouts/rate limits during the assessment | Verified by existing suite only | `llm.py` retry tests; not re-tested here |
| Assessment ↔ commit SHA ↔ event ↔ PR association, both event orders | Verified (D1) | above; existing PR-first `test_pull_request_commit_still_uses_authenticated_automatic_path` |
| Hourly quota under 6 concurrent workers (cap 2 → exactly 2 assessments, 4 skipped) | Verified | `test_concurrent_workers_respect_hourly_quota` |
| Global spend cap with in-flight usage | Unverified / known-stale | documented in `quota.py`; spend lands after runs |
| Disconnect before scheduling, during the GitHub lookup, before the PR review, and between the review pre-check and the POST | Verified (D2, D8) | `test_disconnect_while_commit_lookup_is_in_flight_creates_no_assessment`, `test_disconnect_committed_after_precheck_still_blocks_the_review_post`; existing disconnect tests |
| Disconnect committing while the review POST is already on the wire | Unverified / by design | the lock is released before the network call (repository rule: never hold repository locks across a provider request); the posted review is audited |
| Review only to the assessment's own connected repo/installation | Verified | PR number from the assessment's own event; repo from `assessment.repo_id`; installation-mismatch tests in `test_auto_commits.py` |
| Duplicate PR comment after a lost response or a worker crash mid-POST | Verified (D9) | `test_lost_review_response_is_never_replayed`; the job ends `recovery_required` (ops surface), an operator resolves it |
| Two review jobs for the same PR (legacy and per-PR key, or concurrent) | Verified (D11) | `test_legacy_review_job_and_per_pr_job_post_once`; concurrent in-flight tie-break by job id is code-reviewed, not raced in a test |
| Several pull requests sharing one head commit | Verified (D10) | `test_two_pull_requests_sharing_a_commit_both_get_reviews` (PR arriving before and after completion) |
| Pushes while automatic reviews are OFF leave no event | Unchanged (by design) | `process_github_delivery` returns early |
| `redeliver.recover` startup sweep re-requesting dead GUIDs | Not covered | it skips persisted GUIDs. D6 makes manual "Redeliver" work |
| Frontend status rendering of the new skip reasons | Unverified | API `status_reason` asserted; UI not driven |
| Real GitHub / Railway production behavior | Unverified | no live calls made |

## Observability added

- `log.warning` on every rate-limit deferral (repo, sha/assessment, delay).
- `log.error` when the commit lookup runs out of retries (job, repo, sha, attempts, cause).
- `log.info` when a PR is linked to an existing commit event.
- `log.warning` when a webhook redelivery reprocesses a dead/failed job.
- Audit `monitoring.pr_review_skipped` when the repository was disconnected.

## Codex round 2 (2026-10-29)

| Finding | Fix | Regression (fails on 184b8c62) |
|---|---|---|
| Older legacy retry preempts a newer in-flight per-PR review (id tie-break) | Arbitration and `begin_effect` share one locked transaction (`JobContext.effect(reserve=...)`). Any running in-flight sibling defers the job; an uncertain sibling yields an audited skip. | `test_due_legacy_retry_cannot_preempt_a_newer_in_flight_review`, `test_uncertain_review_on_a_newer_job_blocks_the_older_job` |
| HTTP 5xx after GitHub accepted the review is retried into a duplicate | Hidden marker plus read-back reconciliation. Found = posted, absent = retry, unknown = `recovery_required`. | `test_review_accepted_then_http_500_is_reconciled_not_reposted`, `test_http_500_with_unknown_outcome_requires_recovery`, `test_review_marker_lookup_pages_through_reviews` |
| (preserved) a confirmed-absent 5xx still retries | n/a | `test_http_500_confirmed_absent_on_github_is_retried` (passes before and after) |

Verified: local PostgreSQL with threaded workers and a fake GitHub client. Unverified: the live GitHub reviews API (real pagination and read-after-write consistency). Dependency: #694's write lease is needed to close the disconnect window during the POST.
