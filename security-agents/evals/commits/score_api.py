"""Score labelled eval commits through the public API (no DATABASE_URL / SSH needed).

Env: ARI_API_BASE (e.g. https://ari-ai-lake.vercel.app/api/v1), ARI_SESSION
     (the hackatl_session cookie value), ARI_EVAL_PROJECT (project id).
stdin: {"cases": [...cases.json entries...], "shas": {case_id: commit_sha}}
stdout: {"results": [{case_id, state, seconds, usd, flagged, validated, fixed,
validated_any, fixed_any, findings}], "pending": n}

Same verdict rules as score.py: flagged = any security finding on a changed file;
validated = Probe proved it; fixed = Verify accepted the fix. validated_any /
fixed_any are the same verdicts counted across every finding kind (security +
functional), so the functional reviewer's proofs still show up in the report.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED", "UNSCORED"}
VALIDATED = {
    "validated",
    "diagnosed",
    "patch_proposed",
    "patch_rejected",
    "verified",
    "hardened",
    "unresolved",
}
FIXED = {"verified", "hardened"}


def _get(path: str) -> dict:
    base = os.environ["ARI_API_BASE"].rstrip("/")
    req = urllib.request.Request(
        f"{base}{path}",
        headers={"Cookie": f"hackatl_session={os.environ['ARI_SESSION']}"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp()


def _finding_paths(f: dict) -> set[str]:
    paths = f.get("paths")
    if paths is None:
        # the findings list endpoint returns the finding doc without the
        # denormalized `paths` column; fall back to its source_span evidence
        paths = [
            e.get("path")
            for e in (f.get("evidence") or [])
            if e.get("kind") == "source_span" and e.get("path")
        ]
    return {p.split(":")[0] for p in paths}


def main() -> int:
    request = json.load(sys.stdin)
    project = os.environ["ARI_EVAL_PROJECT"]
    shas: dict[str, str] = request["shas"]

    assessments: dict[str, dict] = {}
    cursor = None
    for _ in range(10):
        q = f"?limit=200{f'&cursor={cursor}' if cursor else ''}"
        page = _get(f"/projects/{project}/assessments{q}")
        items = page["items"] if isinstance(page, dict) else page
        for a in items:
            assessments[a["id"]] = a
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor:
            break
    by_sha: dict[str, dict] = {}
    for a in assessments.values():
        sha = a.get("commit_sha")
        if sha and (sha not in by_sha or a["created_at"] > by_sha[sha]["created_at"]):
            by_sha[sha] = a

    findings = _get(f"/projects/{project}/findings").get("items", [])
    by_assessment: dict[str, list[dict]] = {}
    for f in findings:
        by_assessment.setdefault(f.get("assessment_id") or "", []).append(f)

    results, pending = [], 0
    for case in request["cases"]:
        # A case is only *scored* when its assessment COMPLETED and the case is
        # well formed; anything else is scored: False + unscored_reason so a
        # failed or pending run can never read as a clean miss.
        out = {"case_id": case.get("id"), "kind": case.get("kind"), "cwe": case.get("cwe")}
        missing = [k for k in ("id", "kind", "files") if not case.get(k)]
        if missing:
            results.append(
                {
                    **out,
                    "state": "UNSCORED",
                    "scored": False,
                    "unscored_reason": f"case missing fields: {', '.join(missing)}",
                }
            )
            continue
        a = by_sha.get(shas.get(case["id"], ""))
        if a is None:
            results.append(
                {
                    **out,
                    "state": "NOT_STARTED",
                    "scored": False,
                    "unscored_reason": "no assessment found for the pushed sha",
                }
            )
            pending += 1
            continue
        files = set(case["files"])
        sec_on_files = [
            f for f in by_assessment.get(a["id"], []) if f["kind"] == "security" and files & _finding_paths(f)
        ]
        any_on_files = [f for f in by_assessment.get(a["id"], []) if files & _finding_paths(f)]
        seconds = round(_ts(a.get("completed_at") or a.get("updated_at")) - _ts(a["created_at"]))
        base = {
            **out,
            "assessment_id": a["id"],
            "state": a["state"],
            "seconds": seconds,
            "usd": float((a.get("usage") or {}).get("usd") or 0),
        }
        if a["state"] != "COMPLETED":
            results.append({**base, "scored": False, "unscored_reason": f"assessment ended {a['state']}"})
            if a["state"] not in TERMINAL:
                pending += 1
            continue
        results.append(
            {
                **base,
                "scored": True,
                "flagged": bool(sec_on_files),
                "validated": any(f["status"] in VALIDATED for f in sec_on_files),
                "fixed": any(f["status"] in FIXED for f in sec_on_files),
                "validated_any": any(f["status"] in VALIDATED for f in any_on_files),
                "fixed_any": any(f["status"] in FIXED for f in any_on_files),
                "findings": [
                    {
                        "kind": f["kind"],
                        "status": f["status"],
                        "title": str(f.get("title") or "")[:120],
                        "cwe": ((f.get("standards") or {}).get("cwe") or {}).get("id"),
                    }
                    for f in by_assessment.get(a["id"], [])
                ],
            }
        )
        if a["state"] not in TERMINAL:
            pending += 1
    json.dump({"results": results, "pending": pending}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
