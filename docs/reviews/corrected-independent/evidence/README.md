# Evidence and reproduction

Product SHA: 77897e76eb561f166a2b96c1792545075bc8f078.
Audit-added tests do not change product code. Scripted providers/owned fixtures,
real local PostgreSQL, API authorization and filesystem artifacts. No live keys.

Runtime:
```
PY=/Users/alanmozhoor/Documents/hackatl-agentic-security/.venv/bin/python
export PYTHONPATH=/tmp/ari-review-runtime:backend:shared:standards:monitoring:security-agents:code-intelligence:code-review:specialists
unset REHEARSAL_LIVE
export RUN_LIVE_LLM_EVAL=0 ANTHROPIC_API_KEY= HACKATL_LIVE_ANTHROPIC_API_KEY=
```

Focused command (exit 1; 192 passed, 4 failed):
```
$PY -m pytest \
 backend/tests/live_defense_e2e/test_rc2_independent.py \
 backend/tests/live_defense_e2e/test_independent_integration.py \
 backend/tests/live_defense_e2e/test_exec_reliability_compat.py \
 backend/tests/test_release_rereview.py backend/tests/test_rc2_independent.py \
 backend/tests/test_rc2_upstream_migrations.py backend/tests/test_rc2_populated_migration.py \
 backend/tests/test_rc2_transfer_manifest.py backend/tests/test_parallel_migrations.py \
 backend/tests/test_execution_reliability.py backend/tests/test_jobs_queue.py \
 backend/tests/test_customer_security_hardening.py backend/tests/test_tenant_isolation_adversarial.py \
 backend/tests/test_worker_drain.py backend/tests/test_live_defense_investigations.py \
 backend/tests/test_redact_code.py backend/tests/test_redact_text_gaps.py \
 code-intelligence/tests/test_privacy_redact.py backend/tests/test_independent_corrected_review.py -q
```

Post-base compatibility command (exit 1; 3 failed):
```
$PY -m pytest \
 backend/tests/test_rc2_latest_quota.py::test_over_limit_hourly_assessments_report_actual_reset \
 backend/tests/test_rc2_latest_quota.py::test_over_limit_daily_assessments_report_actual_reset \
 security-agents/tests/test_rc2_latest_protected.py::test_verify_rejects_a_planted_bad_patch_that_edits_a_protected_test -q
```

In a separate checkout pinned to 308f45e4ae68956d837844e436fcf0fa4e6414d1:
```
$PY -m pytest backend/tests/test_parallel_migrations.py -q
```
Exit 0; 2 passed. This is transfer-manifest evidence, not candidate runtime evidence.

Read-only compatibility:
```
git merge-tree --write-tree --messages eea084bc87ac6ca94f578073251c478655b09366 77897e76eb561f166a2b96c1792545075bc8f078
```
Exit 1. Writes only unreferenced Git tree objects; no HEAD/ref/worktree merge.

CI:
```
gh pr view 8 --repo m-alan08/ari-ai --json headRefOid,statusCheckRollup,mergeable
gh api repos/m-alan08/ari-ai/commits/77897e76eb561f166a2b96c1792545075bc8f078/check-runs
gh api repos/m-alan08/ari-ai/commits/77897e76eb561f166a2b96c1792545075bc8f078/status
gh run list --repo m-alan08/ari-ai --commit 77897e76eb561f166a2b96c1792545075bc8f078 --json databaseId,headSha,status,conclusion,url
```

The migration and transfer tests were copied from prior independent audit
46143f9f. Only upstream SHA and corrected transfer SHA/migration-path inputs
changed; assertions did not. Quota/protected-test files are copied upstream
regressions, not implementation-mirroring replacements. strict-provenance.json
verifies byte/AST equality of the three original security files. Test logs from
superseded pins are retained separately, not counted in the final verdict.

69547d2f-new-initial-fixture.log contains an invalid outsider expectation: Bob is
intentionally a project co-owner in setup_project. The final independent test
uses unrelated-outsider and retains the 404 assertion. Its other failures were
not dropped; all three new artifact/parse assertions still fail on the final SHA.

Logs are scrubbed for high-confidence synthetic token formats before storage.
Plain synthetic DSN markers remain to make redaction reproductions readable.
No real customer credentials or data are included. HASHES.json records file
content hashes; the manifest excludes itself.
