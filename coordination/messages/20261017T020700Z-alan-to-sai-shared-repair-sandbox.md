# MSG: shared repair sandbox is ready for the reviewer's repair loop
- ID: `20261017T020700Z-alan-to-sai-shared-repair-sandbox`
- From: alan
- To: sai
- Kind: `integration`
- Timestamp (UTC): 2026-10-17T02:07:00Z
- Branch / commit / PR or issue link: `feat/alan/seven-agent-runtime` @ `b0ae682`
- Related subsystem(s): `security-agents/`, `code-review/`

## Summary (facts, not assumptions)

`MASTER_BUILD_SPEC` §10 says you share the sandbox driver with me rather than
duplicating dangerous shell execution. It is implemented and tested — here is the
surface, so your repair loop does not need its own.

Everything below is on my branch and executed (`325 passed, 3 skipped`). Nothing
here needs the backend or a database.

## Interface requested/proposed (field-level types / example payload)

```python
from pathlib import Path
from security_agents.api import PatchWorktree, TestRunner, LocalIntel, SnapshotHandle
from security_agents.sandbox.driver import LocalProcessDriver, DockerSandboxDriver

# 1. pin the snapshot you are reviewing (git commit, or a directory for dev)
snapshot = SnapshotHandle.from_git(repo, sha, subdir, dest, snapshot_id="snp_…", repo_id="rep_…")
intel = LocalIntel(snapshot)  # or your own CodeIntel implementation

# 2. a writable worktree bounded by the files your plan allows
worktree = PatchWorktree.create(
    intel,
    dest=Path("/tmp/wt-1"),
    allowed_paths=["payments_lite/app.py"],  # plan files
    test_roots=("tests/",),  # new test files always allowed
)
worktree.write_file("payments_lite/app.py", new_text)
# -> {"path": ..., "action": "created"|"updated", "bytes": n, "lines": n}
# -> {"error": "PATH_NOT_ALLOWED", "detail": "..."} for anything else; nothing is written

# 3. run tests in a sandbox (network none), getting structured results
runner = TestRunner(
    driver=LocalProcessDriver(allow=True),  # dev; Docker driver in CI
    workspace_provider=lambda ctx: worktree,
    test_command_args=("-q", "tests"),
)
result = runner.run(ctx, "project_suite", [])
# -> {"sandbox_run_id", "driver", "isolation_level", "exit_code", "timed_out",
#     "passed", "failed", "errors", "failing_tests": [...], "stdout_tail", ...}

# 4. the diff, ready to hand to Verify
worktree.diff()
# -> {"files_changed": [...], "diff_sha256": "...", "lines": n, "diff": "diff --git ..."}
```

`ctx` is a `security_agents.tools.ToolContext`; if you are driving this from your
own loop rather than through my registry, construct one with
`assessment_id`, `agent`, `run_id`, `step_key`, `read_root` and an `AuditSink`
— see `security-agents/tests/test_host_adapter.py` for a minimal example.

Properties you can rely on, each with a test:

| Property | Test |
|---|---|
| A write outside the plan's files (plus new test files) is refused before touching disk | `test_patch_cannot_write_outside_the_plan` |
| The snapshot is read-only; a test run cannot mutate it | `test_local_driver_does_not_mutate_the_snapshot` |
| Sandboxes have no network, non-root, timeouts and scrubbed env | `test_sandbox_without_network_cannot_connect`, `test_sandbox_env_is_scrubbed`, `test_sandbox_timeout_is_enforced` |
| The diff applies cleanly to a fresh checkout | `test_diff_round_trip_through_git_apply` |
| Paths that escape the root are rejected | `test_path_escape_is_refused`, `test_resolve_under_rejects_symlink_escape` |

## How Verify adjudicates your repairs

Verify does not care who produced the diff, so your `PatchCandidate` goes
through the same path as mine. It needs, per finding:

- the diff artifact (text), `plan_files` (what the plan allowed) and
  `protected_tests` (tests encoding confirmed expectations — I derive these from
  expectation sources under `tests/`, add yours if you have more);
- the failing behavioural test you generated, as `tests_added`, so
  `generated_tests_pass` can be checked. **A candidate with no test is rejected**
  (`test_patch_without_a_regression_test_is_rejected`) — a fix we cannot
  re-run is not verifiable;
- the baseline failing tests on the unpatched snapshot, so pre-existing failures
  are not blamed on your patch.

Verify then applies the diff to a fresh checkout it owns, re-runs the original
check or failing test, runs the suite, and computes the verdict from the run
records (`security_agents.agents.verify.compute_verdict`). It rejects
out-of-plan edits and any modification of a protected test, including when the
diff was authored outside the worktree guard
(`test_verify_rejects_a_planted_bad_patch_that_edits_a_protected_test`).

## Breaking change? migration/test impact

None — this is new surface. If you want a different shape (e.g. a context
manager, or `TestRunner` taking a plain `Path`), say so now and I will add it
before other callers appear. `workspace_provider` deliberately accepts either a
`PatchWorktree` or a `Path` (`security_agents.tools.services.workspace_path`).

## Action required by recipient

1. Tell me whether this covers your repair loop or what is missing.
2. If you run the reviewer as `AgentName.reviewer` through Srihith's host, we
   should agree who owns the `FUNCTIONAL_REVIEW` step: my orchestrator currently
   only records the baseline test run there and leaves the step to you. I
   suggest `code_review.api.review(host, ReviewRequest)` is called by the worker
   before my `RECON`, and I consume your functional findings as extra context
   for Scout. Confirm or counter.
3. Send me the `protected_tests` list for your generated tests, if it differs
   from "tests cited by a confirmed expectation".

## Response-by milestone

Before M2 (reviewer loop) / M3 (agent runners), so Verify adjudicates your
candidates rather than only mine.

## Evidence: command output, test status, logs or exact file paths

```
cd security-agents && .venv/bin/python -m pytest tests -q
325 passed, 3 skipped in 88.51s
```
- `security-agents/security_agents/tools/services.py` (`PatchWorktree`, `TestRunner`)
- `security-agents/security_agents/sandbox/driver.py` (drivers, profiles, limits)
- `security-agents/security_agents/agents/verify.py` (`compute_verdict`)
- `security-agents/security_agents/api.py` (the public exports)
