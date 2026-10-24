"""Regression tests for the investigation-quality eval harness.

Covers: corpus materialization determinism, ground-truth leakage into the
provider transcript, scorer logic, a FakeLLM end-to-end smoke run, and
run-to-run reproducibility.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals" / "investigation"
sys.path.insert(0, str(EVAL_DIR))

import corpus  # noqa: E402
import score  # noqa: E402


def test_all_cases_materialize_and_compile():
    cases = corpus.load_cases()
    assert len(cases) >= 25, "dataset should cover vulns, negatives, benign, ambiguous"
    for case in cases:
        with tempfile.TemporaryDirectory() as td:
            mat = corpus.materialize_case(case, Path(td))
            assert mat.workspace.is_dir()
            assert mat.commit_shas, f"{case.id} produced no commit sha"
            # every python file in the materialized target must parse
            target = mat.workspace
            for py in target.rglob("*.py"):
                compile(py.read_text(), str(py), "exec")


def test_materialization_is_deterministic():
    """Same case materialized twice must give identical tree digests."""
    case = next(c for c in corpus.load_cases() if c.id == "vuln_idor_multi_commit")
    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        m1 = corpus.materialize_case(case, Path(td1))
        m2 = corpus.materialize_case(case, Path(td2))
    assert m1.commit_shas == m2.commit_shas
    assert len(m1.commit_shas) == 3  # the case defines three commit stages


def test_labels_never_enter_the_snapshot():
    """labels.yaml stays outside the materialized target tree."""
    case = corpus.load_cases()[0]
    with tempfile.TemporaryDirectory() as td:
        mat = corpus.materialize_case(case, Path(td))
        assert not (mat.workspace / "labels.yaml").exists()
        assert mat.labels_path.is_file()


def _scorer_fixture() -> dict:
    return {
        "provider_status": "fake",
        "final_state": "completed",
        "step_outputs": [
            {
                "agent": "scout",
                "output": {
                    "hypotheses": [
                        {
                            "title": "x",
                            "weakness_class": "CWE-639",
                            "cited_paths": ["shop/app.py"],
                            "suggested_check_category": "access_control.ownership",
                        }
                    ]
                },
            }
        ],
        "report": {"findings": [{"title": "x", "weakness_class": "CWE-639", "status": "diagnosed"}]},
        "runs": [{"agent": "scout", "status": "succeeded", "tool_calls": 3, "tokens": 10, "usd": 0.001}],
        "wall_clock_s": 0.5,
        "snapshot_sha_matches_manifest": True,
    }


def test_scorer_tp_on_matching_vuln():
    case = next(c for c in corpus.load_cases() if c.id == "vuln_order_detail_idor")
    s = score.score_result(case, _scorer_fixture())
    assert s["tp_flagged"] and s["tp_resolved"]
    assert s["attribution"] is None


def test_scorer_fp_on_negative_case():
    case = next(c for c in corpus.load_cases() if c.id == "neg_jobs_list_args")
    s = score.score_result(case, _scorer_fixture())
    assert len(s["fp_flagged"]) == 1 and not s.get("tp_flagged")


def test_scorer_probe_refuted_attribution():
    case = next(c for c in corpus.load_cases() if c.id == "vuln_session_expiry")
    fx = _scorer_fixture()
    fx["step_outputs"][0]["output"]["hypotheses"][0]["weakness_class"] = "CWE-613"
    fx["report"]["findings"] = [{"title": "x", "weakness_class": "CWE-613", "status": "refuted"}]
    s = score.score_result(case, fx)
    assert s["tp_flagged"] and not s["tp_resolved"] and s["attribution"] == "probe_refuted"


def test_scorer_rank_filtered_attribution():
    case = next(c for c in corpus.load_cases() if c.id == "vuln_order_detail_idor")
    fx = _scorer_fixture()
    fx["step_outputs"][0]["output"]["hypotheses"][0]["suggested_check_category"] = "data_flow.taint"
    fx["report"]["findings"] = []
    s = score.score_result(case, fx)
    assert s["tp_flagged"] and s["attribution"] == "rank_filtered"


def test_scorer_scout_miss():
    case = next(c for c in corpus.load_cases() if c.id == "vuln_order_detail_idor")
    fx = _scorer_fixture()
    fx["step_outputs"][0]["output"]["hypotheses"] = []
    fx["report"]["findings"] = []
    s = score.score_result(case, fx)
    assert not s["tp_flagged"] and s["attribution"] == "scout_miss"


def test_fake_e2e_two_cases(tmp_path):
    """End-to-end: real orchestrator + real tools + scripted policy."""
    import run as eval_run

    results_dir = tmp_path / "out"
    for cid in ("vuln_order_detail_idor", "neg_search_format"):
        case = next(c for c in corpus.load_cases() if c.id == cid)
        res = eval_run.run_case(case, provider="fake", out_dir=results_dir, work_root=tmp_path / "ws")
        assert res["final_state"] == "completed", res.get("error")

        # leakage audit: the recorded provider transcript must not contain
        # any ground-truth label strings from cases.json
        calls_path = results_dir / cid / "llm_calls.json"
        assert calls_path.is_file()
        blob = calls_path.read_text()
        for secret in ("ground_truth", "introduced_at_commit", "must_not_confirm", "planted"):
            assert secret not in blob, f"leakage: {secret!r} reached the provider"

        s = score.score_result(case, res)
        if cid == "vuln_order_detail_idor":
            assert s["tp_flagged"], s
        else:
            assert not s.get("fp_flagged"), s


def test_fake_reproducibility(tmp_path):
    """Two identical runs of one case must produce identical hypothesis sets."""
    import run as eval_run

    case = next(c for c in corpus.load_cases() if c.id == "vuln_order_detail_idor")
    keys = []
    for i in range(2):
        res = eval_run.run_case(
            case, provider="fake", out_dir=tmp_path / f"r{i}", work_root=tmp_path / f"w{i}"
        )
        hyps = res["step_outputs"][0]["output"]["hypotheses"]
        keys.append(sorted((h["weakness_class"], h["cited_paths"][0]) for h in hyps))
    assert keys[0] == keys[1] and keys[0]
