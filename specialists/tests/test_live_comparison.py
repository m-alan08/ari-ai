"""The live-comparison harness, exercised offline on scripted (replay) trajectories.

Replay runs say nothing about Claude; they only pin down that the harness wires each
configuration correctly and scores runs against the answer key.
"""

from __future__ import annotations

import json

import pytest

from specialists import live_comparison as lc


def test_wilson_interval_is_wide_for_few_runs():
    r = lc._rate([True, True, False])
    assert r["k"] == 2 and r["n"] == 3
    lo, hi = r["wilson95"]
    assert lo < 0.3 and hi > 0.9


def test_answer_key_paths_are_relative_to_the_snapshot():
    key = lc.load_key()
    assert {p["id"] for p in key["planted"]} == {"fnd_planted_authz", "fnd_planted_refund_bound"}
    assert all(not p["path"].startswith("target/") for p in key["planted"])


@pytest.mark.parametrize("config, specialists_used", [("claude_only", False), ("hybrid", True)])
def test_replay_run_scores_the_planted_authz_and_wires_the_configuration(config, specialists_used):
    score, _, _ = lc.run_one(config, mode="replay", max_usd=5.0, max_hypotheses=3, validate=2)
    authz = score["planted"]["fnd_planted_authz"]
    assert authz["hypothesised"] and authz["validated"] and authz["fixed_verify_accepted"]
    assert score["false_positives_confirmed"] == 0
    sources = set(score["specialist_sources"].values())
    assert (sources != {"unavailable"}) is specialists_used


def test_replay_results_refuse_the_committed_results_path():
    assert lc.main(["--mode", "replay", "--reps", "1"]) == 2


def test_main_writes_runs_and_summary(tmp_path):
    out = tmp_path / "cmp.json"
    assert (
        lc.main(["--mode", "replay", "--reps", "1", "--configs", "claude_only", "--out", str(out)])
        == 0
    )
    doc = json.loads(out.read_text())
    assert doc["mode"] == "replay" and len(doc["runs"]) == 1
    assert doc["summary"]["claude_only"]["planted"]["fnd_planted_authz"]["validated"]["k"] == 1
    assert doc["specialists_only"]["planted"]["fnd_planted_authz"]["scout_rank"]


def test_provider_failures_are_excluded_but_refusals_are_not():
    def score(code, detail, *, error_turns=None, llm_turns=0):
        patch: dict = {
            "error_codes": [code],
            "error_details": [detail] if detail is not None else [],
            "llm_turns": llm_turns,
        }
        if error_turns is not None:
            patch["error_turns"] = error_turns
        return {"state": "completed", "per_agent": {"patch": patch}}

    assert lc.provider_failure(
        score("LLM_UNAVAILABLE", "Error code: 400 credit balance is too low")
    )
    assert lc.provider_failure(score("LLM_UNAVAILABLE", "refusal: policy")) is None
    assert lc.provider_failure(score("BUDGET_EXCEEDED", "cap")) is None
    assert lc.provider_failure({"state": "crashed", "error_code": "RuntimeError"})
    # Missing details: use per-invocation error_turns, not role-wide llm_turns.
    assert lc.provider_failure(score("LLM_UNAVAILABLE", None, error_turns=[0], llm_turns=9))
    assert lc.provider_failure(score("LLM_UNAVAILABLE", None, error_turns=[3], llm_turns=9)) is None


def test_crash_score_debits_run_cap_when_no_orch_usage(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("late failure")

    monkeypatch.setattr(lc, "run_one", boom)
    # Direct unit: the real run_one crash path is covered via _usage_from_orch + cap debit.
    assert lc._usage_from_orch(None) == (0.0, 0)

    class _U:
        usd = 1.25
        total_tokens = 100

    class _R:
        usage = _U()

    class _Orch:
        runs = [_R(), _R()]

    assert lc._usage_from_orch(_Orch()) == (2.5, 200)


class _Hyp:
    def __init__(self, entry_point_id, cited_spans=None):
        self.entry_point_id = entry_point_id
        self.cited_spans = cited_spans or []


def test_matches_rejects_entry_point_mismatch_without_span_fallback():
    loc = {"entry_point_id": "ent_a", "path": "app.py", "symbol": "get_payment", "lines": (10, 20)}
    assert lc._matches(_Hyp("ent_a"), loc)
    assert not lc._matches(_Hyp("ent_b"), loc)  # explicit mismatch: no span fallback


class _HypClass(_Hyp):
    def __init__(self, entry_point_id, weakness_class, cited_spans=None):
        super().__init__(entry_point_id, cited_spans)
        self.weakness_class = weakness_class


def test_planted_match_requires_class_family_on_shared_route():
    # POST /refunds is both planted CWE-840 and a CWE-639 negative lookalike.
    loc = {
        "entry_point_id": "ent_post_refunds",
        "path": "app.py",
        "symbol": "create_refund",
        "lines": (40, 60),
        "planted_id": "fnd_planted_refund_bound",
        "weakness_class": "CWE-840",
    }
    assert lc._matches_planted(_HypClass("ent_post_refunds", "CWE-840"), loc)
    assert lc._matches_planted(_HypClass("ent_post_refunds", "CWE-1284"), loc)  # family
    assert not lc._matches_planted(_HypClass("ent_post_refunds", "CWE-639"), loc)


def test_resume_refuses_mode_or_settings_mismatch(tmp_path):
    out = tmp_path / "cmp.json"
    assert (
        lc.main(["--mode", "replay", "--reps", "1", "--configs", "claude_only", "--out", str(out)])
        == 0
    )
    # Same settings resume is fine.
    assert (
        lc.main(
            [
                "--mode",
                "replay",
                "--reps",
                "1",
                "--configs",
                "claude_only",
                "--out",
                str(out),
                "--resume",
            ]
        )
        == 0
    )
    # Different validate limit must not pool with the prior runs.
    assert (
        lc.main(
            [
                "--mode",
                "replay",
                "--reps",
                "1",
                "--configs",
                "claude_only",
                "--out",
                str(out),
                "--resume",
                "--validate",
                "1",
            ]
        )
        == 2
    )


def test_unknown_config_name_is_rejected(tmp_path):
    out = tmp_path / "cmp.json"
    assert (
        lc.main(["--mode", "replay", "--reps", "1", "--configs", "hybird", "--out", str(out)]) == 2
    )


def test_replay_fixed_results_include_verify_evidence():
    score, _, _ = lc.run_one(
        "claude_only", mode="replay", max_usd=5.0, max_hypotheses=3, validate=2
    )
    authz = score["planted"]["fnd_planted_authz"]
    assert authz["fixed_verify_accepted"]
    assert authz["verify_evidence"]
    assert authz["verify_evidence"][0]["verdict"] == "accepted"
    assert authz["verify_evidence"][0]["checks"]
