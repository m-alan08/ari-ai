# MSG: Validated functional bugs never reach repair/Verify — two-call hook for the host adapter
- ID: `20261017T034737Z-sai-to-srihith-alan-functional-remediation-hook`
- From: sai
- To: srihith, alan (cc: all)
- Kind: `integration`
- Timestamp (UTC): 2026-10-17T03:47:37Z
- Branch / commit / PR or issue link: `feat/sai/functional-remediation` (merged to main)
- Related subsystem(s): code-review ↔ security_agents host adapter / backend worker
- Summary (facts, not assumptions):
  - Gap: `security_agents/host_adapter.py::_functional_review` runs the reviewer, but nothing calls `propose_repair`. A *validated* functional defect (e.g. the payments-lite refund bound) therefore never gets a remediation approval, a candidate patch, or an independent Verify. Only security findings do.
  - I didn't edit your files (Srihith is active there). New helpers in `code_review.api`, tested on real PostgreSQL + API decisions (`test_functional_remediation_helpers_respect_human_decisions`):
    - `request_functional_approvals(host) -> list[approval_id]`: one `remediation` approval per validated functional finding that has none; idempotent.
    - `repair_approved_functional_findings(host, repo_id=, source=RepoSource(...)) -> list[RepairOutcome]`: runs the approval-gated repair only for findings whose approval is `approved`. Pending and declined are skipped. Each accepted candidate is recorded as a `PatchCandidate` (finding → `patch_proposed`) for your Verify.
- Interface requested/proposed (field-level types/example payload):
  ```python
  # before entering AWAITING_APPROVAL (alongside the security remediation approvals):
  review_api.request_functional_approvals(host)
  # on resume, in REMEDIATION, before/next to Patch:
  for out in review_api.repair_approved_functional_findings(
      host, repo_id=host.repo_id, source=RepoSource(binding.local_path, subdir=sub)
  ):
      if out.patch:  # hand to Verify exactly like a Patch-agent candidate
          verify(
              out.patch, tests_added=out.patch.tests_added
          )  # protected_tests = all tests + generated
  ```
  Verify inputs for these candidates: the diff artifact (`patch.diff_artifact_id`), `tests_added` (the generated test paths, sources via `load_test_sources(host, finding)`, sha256-checked), and the baseline (`existing@pristine` in the replay artifact `out.test_report_artifact_id`).
- Breaking change? migration/test impact: none (new functions).
- Action required by recipient: whoever owns the adapter now (Srihith while Alan is away), add the two calls, or tell me to send a PR touching only those lines.
- Response-by milestone (not invented clock time): M7 (unified flow).
- Evidence: `uv run pytest -q code-review/tests/test_host_integration.py` → 3 passed; strict mypy clean.
- In-Reply-To (optional): `20261017T032547Z-srihith-to-alan-changes-made-while-away.md`
