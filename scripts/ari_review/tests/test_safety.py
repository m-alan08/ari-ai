"""Adversarial controller tests. No paid APIs, GitHub writes or model calls."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.ari_review.budget_proxy import Budget
from scripts.ari_review.common import (
    IDS,
    collect_ci,
    journal,
    native_findings,
    sha,
    state_body,
    validate_review,
    verdict,
    workstream,
)

POLICY = json.loads(Path(".github/ari-review/policy.json").read_text())
PIN = {"candidate_sha": "a" * 40, "upstream_sha": "b" * 40}


def review():
    return {
        "version": 1,
        **PIN,
        "checks": [
            {"id": i, "status": "PASS", "evidence": "production.py:12; strict test receipt"}
            for i in IDS
        ],
        "findings": [],
        "merge_equivalence": "PASS",
        "summary": "Verified supplied evidence.",
    }


def gates():
    return [
        PIN,
        review(),
        {"test": {"status": "completed", "conclusion": "success"}},
        {"candidate_sha": PIN["candidate_sha"], "exit_code": 0, "tests": 30, "skipped": 0},
        {
            "candidate_sha": PIN["candidate_sha"],
            "upstream_merge_clean": True,
            "manifest_production_equal": True,
        },
        PIN["candidate_sha"],
        PIN["upstream_sha"],
    ]


def test_all_gates_are_required_for_go():
    assert verdict(*gates())[0] == "GO"


@pytest.mark.parametrize(
    "index,value", [(1, None), (2, {}), (3, None), (4, None), (5, "c" * 40), (6, "d" * 40)]
)
def test_missing_stale_or_incomplete_gate_cannot_approve(index, value):
    args = gates()
    args[index] = value
    assert verdict(*args)[0] == "NO-GO"


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "neutral", "cancelled", None])
def test_nonpassing_ci_is_never_green(conclusion):
    args = gates()
    args[2]["test"]["conclusion"] = conclusion
    assert verdict(*args)[0] == "NO-GO"


@pytest.mark.parametrize("status", ["FAIL", "INCONCLUSIVE"])
def test_critical_check_cannot_be_omitted_by_model_verdict(status):
    args = gates()
    args[1]["checks"][0]["status"] = status
    assert verdict(*args)[0] == "NO-GO"


def test_even_p2_finding_blocks_release():
    args = gates()
    args[1]["findings"] = [{"id": "N7", "title": "diff corruption"}]
    assert verdict(*args)[0] == "NO-GO"


@pytest.mark.parametrize("bad", ["main", "a" * 7, "a" * 40 + "\n", "$(id)", "../head"])
def test_mutable_or_injected_ref_is_not_a_sha(bad):
    with pytest.raises(ValueError):
        sha(bad)


def test_stale_and_incomplete_review_rejected():
    data = review()
    data["candidate_sha"] = "e" * 40
    with pytest.raises(ValueError):
        validate_review(data, PIN, POLICY)
    data = review()
    data["checks"].pop()
    with pytest.raises(ValueError):
        validate_review(data, PIN, POLICY)


def test_injected_owner_and_path_are_not_trusted():
    finding = {
        "id": "NEW-1",
        "severity": "P1",
        "title": "bad",
        "path": "../secrets",
        "line": 1,
        "evidence": "x",
    }
    data = review()
    data["findings"] = [finding]
    with pytest.raises(ValueError):
        validate_review(data, PIN, POLICY)
    assert workstream({"id": "N7", "path": "elsewhere"}, POLICY) == "reliability"


def reservation(day="2026-10-25", candidate="a" * 40, author="github-actions[bot]"):
    return {
        "user": {"login": author},
        "body": state_body(
            "<!-- ari-review-reservation-v1\n", {"day": day, "candidate_sha": candidate}, POLICY
        ),
    }


def test_untrusted_comment_cannot_forge_budget_state():
    assert (
        journal([reservation(author="attacker")], "<!-- ari-review-reservation-v1\n", POLICY) == []
    )


class FakeAPI:
    def pages(self, path, key=None):
        if path.endswith("/reviews"):
            return [
                {"id": 1, "commit_id": "c" * 40, "user": {"login": POLICY["native_reviewer"]}},
                {"id": 2, "commit_id": PIN["candidate_sha"], "user": {"login": "fake-codex"}},
            ]
        if path.endswith("/comments"):
            return [{"id": 1, "pull_request_review_id": 1, "body": "[P1] stale"}]
        return [
            {
                "id": 1,
                "name": "backend-tests",
                "head_sha": PIN["candidate_sha"],
                "app": {"slug": "github-actions"},
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 2,
                "name": "backend-tests",
                "head_sha": PIN["candidate_sha"],
                "app": {"slug": "github-actions"},
                "status": "completed",
                "conclusion": "failure",
            },
            {
                "id": 3,
                "name": "backend-tests",
                "head_sha": "c" * 40,
                "app": {"slug": "github-actions"},
                "status": "completed",
                "conclusion": "success",
            },
        ]


def test_latest_failed_check_beats_old_success_and_wrong_sha():
    ci = collect_ci(FakeAPI(), PIN, POLICY)
    assert ci["backend-tests"]["conclusion"] == "failure"
    assert ci["intel-tests"]["status"] == "missing"


def test_native_review_requires_real_bot_and_exact_commit():
    assert native_findings(FakeAPI(), PIN, POLICY) == []


def budget():
    p = copy.deepcopy(POLICY)
    p["pricing_valid_through"] = "2099-01-01"
    return Budget(p)


def test_concurrent_requests_cannot_exceed_cost_reservations():
    gate = budget()

    def send(_):
        try:
            gate.admit({"model": POLICY["model"]})
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(send, range(30))) == 10
    assert gate.calls == 10


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "unbounded-model"},
        {"service_tier": "priority"},
        {"background": True},
        {"tools": [{"type": "web_search"}]},
        {"input": [{"type": "input_image"}]},
        {"prompt_cache_retention": "24h"},
    ],
)
def test_paid_tools_unknown_model_and_nonstandard_billing_rejected(extra):
    gate = budget()
    with pytest.raises(ValueError):
        gate.admit({"model": POLICY["model"], **extra})
    assert gate.calls == 0


def test_output_cap_cannot_be_widened_by_client():
    result = budget().admit({"model": POLICY["model"], "max_output_tokens": 1000000})
    assert result["max_output_tokens"] == 8192
    assert result["service_tier"] == "default"


def test_expired_price_policy_does_not_spend():
    gate = budget()
    gate.policy["pricing_valid_through"] = "2000-01-01"
    with pytest.raises(ValueError):
        gate.admit({"model": POLICY["model"]})
    assert gate.calls == 0
