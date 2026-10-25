"""Regression tests for the API-based eval scorer (evals/commits/score_api.py).

The /projects/{id}/findings list endpoint serializes each finding's JSONB doc,
which does not carry the denormalized `paths` column. The scorer must derive
touched paths from `evidence` source_span entries instead of silently scoring
`flagged=False` for every finding (the defect fixed by PR #516).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCORE_API = Path(__file__).resolve().parents[1] / "evals" / "commits" / "score_api.py"

_spec = importlib.util.spec_from_file_location("eval_score_api", SCORE_API)
assert _spec is not None and _spec.loader is not None
score_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_api)


def _finding(**kw) -> dict:
    f = {"kind": "security", "status": "validated"}
    f.update(kw)
    return f


def test_paths_derived_from_source_span_when_column_absent() -> None:
    finding = _finding(
        evidence=[
            {
                "kind": "source_span",
                "path": "backend/api/routes.py",
                "commit_sha": "d0131167",
                "span": [10, 20],
            }
        ]
    )
    assert score_api._finding_paths(finding) == {"backend/api/routes.py"}


def test_paths_column_used_when_present() -> None:
    finding = _finding(paths=["backend/api/routes.py:10-20"], evidence=[])
    assert score_api._finding_paths(finding) == {"backend/api/routes.py"}


def test_empty_paths_column_not_confused_with_missing() -> None:
    # An explicitly empty column is authoritative; evidence is not consulted.
    finding = _finding(
        paths=[],
        evidence=[
            {
                "kind": "source_span",
                "path": "backend/api/routes.py",
                "commit_sha": "d0131167",
                "span": [10, 20],
            }
        ],
    )
    assert score_api._finding_paths(finding) == set()


def test_non_source_span_evidence_ignored() -> None:
    finding = _finding(
        evidence=[
            {"kind": "test_run", "path": "tests/test_x.py"},
            {"kind": "note", "text": "see backend/api/routes.py"},
        ]
    )
    assert score_api._finding_paths(finding) == set()


def test_finding_without_paths_or_evidence_scores_nothing() -> None:
    assert score_api._finding_paths(_finding()) == set()
