# Investigation quality evaluation — results report

- Run: `20261024T204625Z_fake` · provider mode: **fake** (FakeLLM scripted policy — simulated provider, **not** a real LLM)
- Repo commit: `c4c1aa880d5a237aefe430a3030f9cb8a1b1bddf`
- Cases: 93 (60 vulnerable, 24 hard-negative/benign, 9 ambiguous)
- Completed investigations: 93/93 (100%)

## Measured performance

| Level | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| flagged (Scout hypothesis) | 60 | 18 | 0 | 76.9% | 100% | 87% |
| resolved (confirmed/diagnosed finding) | 24 | 0 | 36 | 100% | 40% | 57.1% |

| Operational metric | Value |
|---|---|
| Hypotheses / investigation | 0.935 |
| Findings / investigation | 0.935 |
| Latency p50 / p95 (s) | 0.531 / 0.869 |
| Tool calls / investigation (median / p95) | 16 / 21 |
| Tokens / investigation (total) | 2614371 |
| USD / investigation (total, list-price estimate) | $0.306222 |
| Snapshot-sha correlation | 93/93 |
| Artifact integrity (report + sha) | 93/93 |
| Reproducibility (identical hypothesis sets across repeats) | 31/31 |

## Failure attribution (vulnerable cases not resolved)

| Attributed stage | Count | Meaning |
|---|---|---|
| probe_inconclusive | 27 | Probe could not model the vuln with an available template |
| probe_refuted | 9 | Probe executed its template and reported the true vuln REFUTED |

## Per-case outcomes

| Case | Kind | Flagged | Resolved | Attribution / extras |
|---|---|---|---|---|
| vuln_order_detail_idor | vuln | TP | TP |  |
| vuln_order_detail_idor | vuln | TP | TP |  |
| vuln_order_detail_idor | vuln | TP | TP |  |
| vuln_invoice_detail_idor | vuln | TP | TP |  |
| vuln_invoice_detail_idor | vuln | TP | TP |  |
| vuln_invoice_detail_idor | vuln | TP | TP |  |
| vuln_customer_detail_idor | vuln | TP | TP |  |
| vuln_customer_detail_idor | vuln | TP | TP |  |
| vuln_customer_detail_idor | vuln | TP | TP |  |
| vuln_invoice_bound | vuln | TP | TP |  |
| vuln_invoice_bound | vuln | TP | TP |  |
| vuln_invoice_bound | vuln | TP | TP |  |
| vuln_search_sqli | vuln | TP | TP |  |
| vuln_search_sqli | vuln | TP | TP |  |
| vuln_search_sqli | vuln | TP | TP |  |
| vuln_session_expiry | vuln | TP | — | probe_refuted |
| vuln_session_expiry | vuln | TP | — | probe_refuted |
| vuln_session_expiry | vuln | TP | — | probe_refuted |
| vuln_idor_multi_commit | vuln | TP | TP |  |
| vuln_idor_multi_commit | vuln | TP | TP |  |
| vuln_idor_multi_commit | vuln | TP | TP |  |
| vuln_sqli_noise | vuln | TP | TP |  |
| vuln_sqli_noise | vuln | TP | TP |  |
| vuln_sqli_noise | vuln | TP | TP |  |
| vuln_file_traversal | vuln | TP | — | probe_inconclusive |
| vuln_file_traversal | vuln | TP | — | probe_inconclusive |
| vuln_file_traversal | vuln | TP | — | probe_inconclusive |
| vuln_fetch_ssrf | vuln | TP | — | probe_inconclusive |
| vuln_fetch_ssrf | vuln | TP | — | probe_inconclusive |
| vuln_fetch_ssrf | vuln | TP | — | probe_inconclusive |
| vuln_share_compare | vuln | TP | — | probe_inconclusive |
| vuln_share_compare | vuln | TP | — | probe_inconclusive |
| vuln_share_compare | vuln | TP | — | probe_inconclusive |
| vuln_extract_traversal | vuln | TP | — | probe_inconclusive |
| vuln_extract_traversal | vuln | TP | — | probe_inconclusive |
| vuln_extract_traversal | vuln | TP | — | probe_inconclusive |
| vuln_dispatch_shell | vuln | TP | — | probe_inconclusive |
| vuln_dispatch_shell | vuln | TP | — | probe_inconclusive |
| vuln_dispatch_shell | vuln | TP | — | probe_inconclusive |
| vuln_traversal_2commits | vuln | TP | — | probe_inconclusive |
| vuln_traversal_2commits | vuln | TP | — | probe_inconclusive |
| vuln_traversal_2commits | vuln | TP | — | probe_inconclusive |
| vuln_webhook_sig | vuln | TP | — | probe_refuted |
| vuln_webhook_sig | vuln | TP | — | probe_refuted |
| vuln_webhook_sig | vuln | TP | — | probe_refuted |
| vuln_tls_verify | vuln | TP | — | probe_refuted |
| vuln_tls_verify | vuln | TP | — | probe_refuted |
| vuln_tls_verify | vuln | TP | — | probe_refuted |
| vuln_cookie_flags | vuln | TP | — | probe_inconclusive |
| vuln_cookie_flags | vuln | TP | — | probe_inconclusive |
| vuln_cookie_flags | vuln | TP | — | probe_inconclusive |
| vuln_report_command | vuln | TP | — | probe_inconclusive; extra CWE-862@services/jobs.py |
| vuln_report_command | vuln | TP | — | probe_inconclusive; extra CWE-862@services/jobs.py |
| vuln_report_command | vuln | TP | — | probe_inconclusive; extra CWE-862@services/jobs.py |
| vuln_tenant_filter | vuln | TP | TP | extra CWE-78@services/jobs.py |
| vuln_tenant_filter | vuln | TP | TP | extra CWE-78@services/jobs.py |
| vuln_tenant_filter | vuln | TP | TP | extra CWE-78@services/jobs.py |
| vuln_unbounded_list | vuln | TP | — | probe_inconclusive; extra CWE-78@services/jobs.py |
| vuln_unbounded_list | vuln | TP | — | probe_inconclusive; extra CWE-78@services/jobs.py |
| vuln_unbounded_list | vuln | TP | — | probe_inconclusive; extra CWE-78@services/jobs.py |
| neg_search_format | hard_negative | — | — |  |
| neg_search_format | hard_negative | — | — |  |
| neg_search_format | hard_negative | — | — |  |
| neg_share_etag | hard_negative | — | — |  |
| neg_share_etag | hard_negative | — | — |  |
| neg_share_etag | hard_negative | — | — |  |
| neg_jobs_list_args | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| neg_jobs_list_args | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| neg_jobs_list_args | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| neg_verify_ctx | hard_negative | — | — |  |
| neg_verify_ctx | hard_negative | — | — |  |
| neg_verify_ctx | hard_negative | — | — |  |
| neg_parameterized_export | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| neg_parameterized_export | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| neg_parameterized_export | hard_negative | FP | — | fp CWE-78@services/jobs.py |
| benign_readme | benign | — | — |  |
| benign_readme | benign | — | — |  |
| benign_readme | benign | — | — |  |
| benign_cache | benign | — | — |  |
| benign_cache | benign | — | — |  |
| benign_cache | benign | — | — |  |
| benign_rename | benign | FP | — | fp CWE-78@services/jobs.py |
| benign_rename | benign | FP | — | fp CWE-78@services/jobs.py |
| benign_rename | benign | FP | — | fp CWE-78@services/jobs.py |
| amb_tls_env | ambiguous | CWE-295@worker.py | not confirmed |  |
| amb_tls_env | ambiguous | CWE-295@worker.py | not confirmed |  |
| amb_tls_env | ambiguous | CWE-295@worker.py | not confirmed |  |
| amb_external_authz | ambiguous | — | not confirmed |  |
| amb_external_authz | ambiguous | — | not confirmed |  |
| amb_external_authz | ambiguous | — | not confirmed |  |
| amb_missing_helper | ambiguous | CWE-78@services/jobs.py | not confirmed |  |
| amb_missing_helper | ambiguous | CWE-78@services/jobs.py | not confirmed |  |
| amb_missing_helper | ambiguous | CWE-78@services/jobs.py | not confirmed |  |

## Example investigations

### Successful: `vuln_order_detail_idor`

Scout flagged the planted defect, Probe executed the check template in the sandbox and confirmed it, Trace and Diagnose produced the finding (tool calls: 19, wall: 0.609s).

### Unsuccessful: `vuln_session_expiry`

Outcome: **probe_refuted**. Flagged keys: ['CWE-613@shop/app.py'].

### False positive: `vuln_report_command`

Hypotheses matching no ground-truth defect: CWE-862@services/jobs.py (allow-list ALLOWED_COMMANDS is defined but never enforced)

## Limitations of this measurement

- Provider is the **scripted FakeLLM policy** (`fake_policy.py`): deterministic pattern-matching over real tool results, not a language model. These numbers measure the *platform pipeline* (Scout contract, check-category gate, Probe templates, sandbox execution, Diagnose), not model reasoning quality.
- Live-provider results: **unavailable** — no authorized API key or cost budget exists in this environment, so no paid calls were run.
- Hypothesis `confidence` is a scripted constant, not a calibrated probability.
- Ground truth labels never enter the agent's input (leakage is checked by a regression test over the recorded `llm_calls.json`).
