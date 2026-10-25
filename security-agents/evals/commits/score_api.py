"""Score labelled eval commits through the public API (no DATABASE_URL / SSH needed).

Env: ARI_API_BASE (e.g. https://ari-ai-lake.vercel.app/api/v1), ARI_SESSION
     (the hackatl_session cookie value), ARI_EVAL_PROJECT (project id).
stdin: {"cases": [...cases.json entries...], "shas": {case_id: commit_sha}}
stdout: {"results": [{case_id, state, seconds, usd, flagged, validated, fixed, findings}], "pending": n}

Same verdict rules as score.py: flagged = any security finding on a changed file;
validated = Probe proved it; fixed = Verify accepted the fix.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}
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
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


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
        out = {"case_id": case["id"], "kind": case["kind"], "cwe": case.get("cwe")}
        a = by_sha.get(shas.get(case["id"], ""))
        if a is None:
            results.append({**out, "state": "NOT_STARTED"})
            pending += 1
            continue
        files = set(case["files"])
        on_files = [
            f
            for f in by_assessment.get(a["id"], [])
            if f["kind"] == "security" and files & {p.split(":")[0] for p in (f.get("paths") or [])}
        ]
        seconds = round(_ts(a.get("completed_at") or a.get("updated_at")) - _ts(a["created_at"]))
        results.append(
            {
                **out,
                "assessment_id": a["id"],
                "state": a["state"],
                "seconds": seconds,
                "usd": float((a.get("usage") or {}).get("usd") or 0),
                "flagged": bool(on_files),
                "validated": any(f["status"] in VALIDATED for f in on_files),
                "fixed": any(f["status"] in FIXED for f in on_files),
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
