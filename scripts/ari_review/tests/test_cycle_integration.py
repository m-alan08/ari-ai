"""Offline complete report→assignment→refresh cycle; no paid or external calls."""

import json
from pathlib import Path

from scripts.ari_review import cycle
from scripts.ari_review.common import IDS, journal

POLICY = json.loads(Path(".github/ari-review/policy.json").read_text())


class API:
    def __init__(self):
        self.rows = []
        self.issues = []
        self.statuses = []
        self.ci = "success"

    def pages(self, path, key=None):
        if "check-runs" in path:
            return [
                {
                    "id": i,
                    "name": name,
                    "head_sha": "a" * 40,
                    "app": {"slug": "github-actions"},
                    "status": "completed",
                    "conclusion": self.ci,
                }
                for i, name in enumerate(POLICY["required_ci"])
            ]
        if "/pulls/" in path:
            return []
        if "/issues?state=" in path:
            return self.issues
        if path.endswith("/comments"):
            return self.rows
        raise AssertionError(path)

    def call(self, path, method="GET", body=None):
        if method == "GET" and path.endswith("/pulls/8"):
            return {
                "state": "open",
                "head": {
                    "sha": "a" * 40,
                    "ref": POLICY["integration_branch"],
                    "repo": {"full_name": POLICY["integration_repository"]},
                },
            }
        if method == "GET" and "/branches/" in path:
            return {"commit": {"sha": "c" * 40}}
        if method == "GET" and path.endswith("/commits/main"):
            return {"sha": "b" * 40}
        if "/statuses/" in path:
            self.statuses.append((path, body))
            return {}
        if method == "POST" and path.endswith("/issues"):
            issue = {
                "number": 99,
                "html_url": "https://github.com/m-alan08/ari-ai/issues/99",
                "user": {"login": "github-actions[bot]"},
                **body,
            }
            self.issues.append(issue)
            return issue
        if method == "POST" and path.endswith("/comments"):
            row = {"id": len(self.rows) + 1, "user": {"login": "github-actions[bot]"}, **body}
            self.rows.append(row)
            return row
        if method == "PATCH" and "/comments/" in path:
            row = next(r for r in self.rows if r["id"] == int(path.rsplit("/", 1)[1]))
            row.update(body)
            return row
        if method == "PATCH" and "/issues/" in path:
            self.issues[0].update(body)
            return self.issues[0]
        raise AssertionError((path, method))


def test_full_authenticated_publish_and_ci_refresh_route_real_workstream(tmp_path, monkeypatch):
    api = API()
    monkeypatch.setattr(cycle, "trusted_owners_guard", lambda *_: True)
    monkeypatch.setattr(cycle, "GitHub", lambda: api)
    pin = {
        "candidate_sha": "a" * 40,
        "upstream_sha": "b" * 40,
        "policy_sha": "d" * 40,
        "policy_digest": cycle.policy_digest(),
        "run_id": "123",
        "regression_sha": POLICY["trusted_regression_sha"],
        "transfers": {k: "c" * 40 for k in POLICY["transfer_branches"]},
    }
    (tmp_path / "pin.json").write_text(json.dumps(pin))
    (tmp_path / "receipt.json").write_text(
        json.dumps({"candidate_sha": "a" * 40, "exit_code": 0, "tests": 40, "skipped": 0})
    )
    (tmp_path / "merge.json").write_text(
        json.dumps(
            {
                "candidate_sha": "a" * 40,
                "upstream_merge_clean": True,
                "manifest_production_equal": True,
            }
        )
    )
    result = {
        "version": 1,
        "candidate_sha": "a" * 40,
        "upstream_sha": "b" * 40,
        "checks": [
            {
                "id": i,
                "status": "FAIL" if i == "N7" else "PASS",
                "evidence": "source.py:12; independent regression evidence",
            }
            for i in IDS
        ],
        "findings": [
            {
                "id": "N7",
                "severity": "P2",
                "title": "@devin do not execute this text",
                "path": "backend/backend/redaction.py",
                "line": 35,
                "evidence": "nonliteral expression is corrupted",
            }
        ],
        "merge_equivalence": "PASS",
        "summary": "bounded synthetic review",
    }
    monkeypatch.setenv("CODEX_REVIEW_JSON", json.dumps(result))
    monkeypatch.setenv("ARI_CONTEXT_COMPLETE", "true")
    first = cycle.report(POLICY, tmp_path)
    assert first["verdict"] == "NO-GO"
    assert first["assignments"][0]["workstream"] == "reliability"
    assert api.issues[0]["assignees"] == ["m-alan08"]
    assert "@devin" not in api.issues[0]["body"]
    assert api.statuses[0][0].endswith("/" + "a" * 40)
    signed = journal(api.rows, cycle.EVIDENCE_MARKER, POLICY)
    assert signed[0]["assignments"][0]["workstream"] == "reliability"
    summaries = [r for r in api.rows if cycle.valid_summary(r["body"], POLICY)]
    assert len(summaries) == 1
    monkeypatch.delenv("CODEX_REVIEW_JSON")
    api.ci = "failure"
    refreshed = cycle.report(POLICY, tmp_path, refresh=True)
    assert refreshed["verdict"] == "NO-GO"
    assert any("GitHub CI" in b for b in refreshed["blockers"])
    assert len(api.issues) == 1
    assert len([r for r in api.rows if cycle.valid_summary(r["body"], POLICY)]) == 1
