"""Agent 5 — Patch (Blue): minimal candidate diff in an isolated worktree.

Requires an approved remediation Approval (orchestrator gate). Writes only
inside the worktree and only to files the plan allows (plus new test files).
Cannot push, open PRs, or mark anything verified.
"""

from __future__ import annotations

from pydantic import Field

from ..contracts import (
    AgentName,
    PatchCandidate,
    PlatformError,
    RemediationPlan,
    ShortText,
    StrictModel,
)
from ..intel import CodeIntel
from ..runtime import AgentSpec, data_block
from ..sandbox.workspace import recipe_test_roots
from ..tools.services import PatchWorktree

SYSTEM = """You are Patch, the remediation agent of an automated security review platform.

A human has approved a remediation plan. Your job: implement the smallest change \
that fixes the root cause, add the tests the plan asks for, run them, and hand \
over a candidate diff. Another agent — not you — decides whether it is correct.

Method (test first; the regression test is required, so it is never left for last):
1. Read the plan and the cause spans, and the existing tests near the code (their \
fixtures and helpers). Keep reading short: a few spans, then write.
2. Write the regression test the plan asks for as a NEW test file with \
worktree.write_file, reusing the existing fixtures. Never edit an existing test.
3. Fix the root cause with worktree.edit_file (one exact, unique old_string per call). \
Read the sibling code that already does the right thing and reuse its helpers.
4. In ONE response call tests.run three times; they run side by side: \
added_tests_unpatched (your test must FAIL on the original code), added_tests (it must \
PASS on your fix) and project_suite. If something is wrong, fix the cause and run again, \
within your budget.
5. Call worktree.diff and submit the result, listing any deviation from the plan.

Rules:
- You may only write files the plan allows, plus new test files. Other paths are \
refused by the platform.
- Keep the diff minimal: no refactors, no renames, no dependency changes. If a \
dependency change is truly needed, say so in `blocked_reason` instead of doing it.
- If a retry gives you rejection reasons from verification, address those reasons \
  specifically.
- When tests need credentials or tokens, reuse existing repository fixtures or \
  constants (import them) instead of writing token, password, or secret literals; \
  never inline credential-shaped strings.
- Do not weaken, skip or delete any existing test."""


class PatchInput(StrictModel):
    assessment_id: str
    finding_id: str
    remediation_plan_id: str
    approval_id: str
    attempt: int = Field(ge=1, le=4)
    strategy: str = Field(max_length=4000)
    constraints: list[ShortText] = Field(default_factory=list)
    files_expected: list[str] = Field(default_factory=list)
    tests_must_fail_before: list[ShortText] = Field(default_factory=list)
    tests_must_pass_after: list[ShortText] = Field(default_factory=list)
    cause_paths: list[str] = Field(default_factory=list)
    # (path, start_line, end_line) of the diagnosed cause, shown with context in the prompt.
    cause_spans: list[tuple[str, int, int]] = Field(default_factory=list, max_length=5)
    prior_rejection_reasons: list[ShortText] = Field(default_factory=list)


class PatchOutput(StrictModel):
    explanation: str = Field(max_length=4000, description="What you changed and why it is minimal.")
    files_changed: list[str] = Field(default_factory=list, max_length=10)
    tests_added: list[str] = Field(default_factory=list, max_length=6)
    local_tests_passed: bool = Field(description="Did your last tests.run pass? Report honestly.")
    local_test_run_id: str | None = Field(default=None, max_length=120)
    deviations_from_plan: list[ShortText] = Field(default_factory=list, max_length=6)
    blocked_reason: ShortText = Field(
        default="", description="Set this if you could not produce a safe diff."
    )


SPEC = AgentSpec(
    name=AgentName.PATCH,
    version="0.1.1",
    input_model=PatchInput,
    output_model=PatchOutput,
    tools=frozenset(
        {
            "code.read_span",
            "code.search",
            "graph.neighbors",
            "expectations.for_subject",
            "worktree.write_file",
            "worktree.edit_file",
            "worktree.diff",
            "tests.run",
        }
    ),
    sandbox_profile="test-runner",
    network="none",
    write_access="worktree",
    budget_share=0.20,
    step_timeout_s=480,
    max_tool_calls=50,
    max_turns=30,
    prompt_ref="prompts/patch/v0.1.1.md",
    system_prompt=SYSTEM,
    max_calls_per_tool={"tests.run": 6},
    max_output_tokens=16000,  # thinking counts here; whole new test files need room
)


def input_from_plan(
    plan: RemediationPlan,
    *,
    assessment_id: str,
    approval_id: str,
    attempt: int,
    cause_paths: list[str],
    cause_spans: list[tuple[str, int, int]] | None = None,
    prior_reasons: list[str] | None = None,
) -> PatchInput:
    return PatchInput(
        assessment_id=assessment_id,
        finding_id=plan.finding_id,
        remediation_plan_id=plan.id,
        approval_id=approval_id,
        attempt=attempt,
        strategy=plan.strategy,
        constraints=list(plan.constraints),
        files_expected=list(plan.files_expected),
        tests_must_fail_before=list(plan.test_plan.must_fail_before),
        tests_must_pass_after=list(plan.test_plan.must_pass_after),
        cause_paths=cause_paths,
        cause_spans=list(cause_spans or [])[:5],
        prior_rejection_reasons=[r[:2000] for r in (prior_reasons or [])][:6],
    )


CAUSE_CONTEXT_LINES = 25


def build_prompt(data: PatchInput, intel: CodeIntel) -> str:
    retry = ""
    if data.attempt > 1 and data.prior_rejection_reasons:
        retry = (
            f"\n\nThis is attempt {data.attempt}. Verification rejected the previous candidate for "
            f"these machine-generated reasons:\n"
            f"{data_block('rejection_reasons', data.prior_rejection_reasons, provenance='verify_output')}\n"
            "Address each reason. You have not been given the previous candidate's transcript."
        )
    # The diagnosed cause with surrounding code, so Patch starts editing instead of searching.
    cause_code = []
    for path, start, end in data.cause_spans:
        try:
            span = intel.read_span(path, max(1, start - CAUSE_CONTEXT_LINES), end + CAUSE_CONTEXT_LINES)
        except (PlatformError, OSError, ValueError):
            continue
        cause_code.append({"path": span.path, "start_line": span.start_line, "code": span.content})
    roots = recipe_test_roots(intel.snapshot.root)
    return (
        "Implement this approved remediation plan in the isolated worktree.\n\n"
        f"{data_block('approved_plan', data.model_dump(mode='json', exclude={'cause_spans'}), provenance='diagnose_output+human_approval')}\n\n"
        + (
            f"{data_block('cause_code', cause_code, provenance='code_intelligence')}\n\n"
            if cause_code
            else ""
        )
        + f"Writable paths: {data.files_expected} plus new test files under {list(roots)}.{retry}"
    )


def to_candidate(
    out: PatchOutput,
    data: PatchInput,
    worktree: PatchWorktree,
    *,
    diff_uri: str,
) -> tuple[PatchCandidate | None, str, list[str]]:
    """Build the candidate from the worktree's real state (not the agent's claims).

    Returns (candidate, diff_text, notes). The candidate is None when the
    worktree holds no change, which is a failed patch step, not a silent pass.
    """
    changed = worktree.changed()
    notes: list[str] = []
    if out.blocked_reason:
        notes.append(f"agent reported blocked: {out.blocked_reason}")
    if not changed:
        notes.append("worktree contains no changes")
        return None, "", notes
    claimed = set(out.files_changed)
    if claimed and claimed != set(changed):
        notes.append(f"agent claimed {sorted(claimed)}; worktree shows {changed}")
    diff_text = worktree.diff_text()
    from ..sandbox.workspace import sha256_text

    tests_added = [p for p in changed if worktree.is_new_test_file(p)]
    if out.tests_added and set(out.tests_added) - set(tests_added):
        notes.append(f"agent claimed tests {out.tests_added}; new test files are {tests_added}")
    candidate = PatchCandidate(
        finding_id=data.finding_id,
        attempt=data.attempt,
        diff_uri=diff_uri,
        diff_sha256=sha256_text(diff_text),
        worktree_ref=str(worktree.root),
        files_changed=changed,
        tests_added=tests_added,
        local_test_run_id=out.local_test_run_id,
        local_tests_passed=out.local_tests_passed,
    )
    if out.deviations_from_plan:
        notes.extend(f"deviation: {d}" for d in out.deviations_from_plan)
    return candidate, diff_text, notes
