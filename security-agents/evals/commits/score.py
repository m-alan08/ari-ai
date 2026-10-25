"""Score labelled eval commits from the platform database (run where DATABASE_URL is set).

stdin: {"cases": [...cases.json entries...], "shas": {case_id: commit_sha}}
stdout: {"results": [{case_id, state, seconds, usd, flagged, validated, fixed, findings}], "pending": n}

A security finding counts for a case when one of its paths is a file the case changed.
flagged   = any security finding on a changed file (hypothesis or later)
validated = Probe proved it (validated or any later status)
fixed     = Verify accepted the fix (verified / hardened)
For benign cases a validated finding is a false positive.
"""

from __future__ import annotations

import json
import os
import sys

import sqlalchemy as sa

# UNSCORED is a case-verdict state (missing case fields), not an assessment state
# the API can emit — it only keeps malformed cases from counting as pending.
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


def _engine() -> sa.Engine:
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    return sa.create_engine(url)


def score_case(case: dict, assessment: dict | None, findings: list[dict]) -> dict:
    """A case is only *scored* when its assessment COMPLETED and the case is well
    formed. Anything else returns ``scored: False`` + ``unscored_reason`` and no
    verdict fields — a failed or pending run must never read as a clean miss."""
    out = {"case_id": case.get("id"), "kind": case.get("kind"), "cwe": case.get("cwe")}
    missing = [k for k in ("id", "kind", "files") if not case.get(k)]
    if missing:
        return {
            **out,
            "state": "UNSCORED",
            "scored": False,
            "unscored_reason": f"case missing fields: {', '.join(missing)}",
        }
    if assessment is None:
        return {
            **out,
            "state": "NOT_STARTED",
            "scored": False,
            "unscored_reason": "no assessment found for the pushed sha",
        }
    detail = {
        **out,
        "assessment_id": assessment["id"],
        "state": assessment["state"],
        "seconds": assessment.get("seconds"),
        "usd": assessment.get("usd"),
    }
    if assessment["state"] != "COMPLETED":
        return {**detail, "scored": False, "unscored_reason": f"assessment ended {assessment['state']}"}
    files = set(case["files"])
    on_files = [
        f for f in findings if f["kind"] == "security" and files & {p.split(":")[0] for p in f["paths"]}
    ]
    any_files = [f for f in findings if files & {p.split(":")[0] for p in f["paths"]}]
    return {
        **detail,
        "scored": True,
        "flagged": bool(on_files),
        "validated": any(f["status"] in VALIDATED for f in on_files),
        "fixed": any(f["status"] in FIXED for f in on_files),
        "validated_any": any(f["status"] in VALIDATED for f in any_files),
        "fixed_any": any(f["status"] in FIXED for f in any_files),
        "findings": [
            {"kind": f["kind"], "status": f["status"], "title": f["title"][:120], "cwe": f["cwe"]}
            for f in findings
        ],
    }


def main() -> int:
    request = json.load(sys.stdin)
    shas: dict[str, str] = request["shas"]
    results, pending = [], 0
    with _engine().connect() as conn:
        for case in request["cases"]:
            sha = shas.get(case["id"])
            row = (
                conn.execute(
                    sa.text(
                        "select id, state, extract(epoch from updated_at - created_at) as seconds, "
                        "doc->'usage'->>'usd' as usd from assessments where commit_sha = :sha "
                        "order by created_at desc limit 1"
                    ),
                    {"sha": sha},
                )
                .mappings()
                .first()
                if sha
                else None
            )
            assessment = (
                {
                    "id": row["id"],
                    "state": row["state"],
                    "seconds": round(float(row["seconds"] or 0)),
                    "usd": float(row["usd"] or 0),
                }
                if row
                else None
            )
            findings = []
            if assessment:
                for f in conn.execute(
                    sa.text("select kind, status, paths, doc from findings where assessment_id = :a"),
                    {"a": assessment["id"]},
                ).mappings():
                    std = (f["doc"] or {}).get("standards") or {}
                    # a NULL paths column falls back to source_span evidence,
                    # matching score_api.py
                    paths = f["paths"] or [
                        e.get("path")
                        for e in (f["doc"] or {}).get("evidence") or []
                        if e.get("kind") == "source_span" and e.get("path")
                    ]
                    findings.append(
                        {
                            "kind": f["kind"],
                            "status": f["status"],
                            "paths": [str(p) for p in paths],
                            "title": str((f["doc"] or {}).get("title") or ""),
                            "cwe": (std.get("cwe") or {}).get("id"),
                        }
                    )
            result = score_case(case, assessment, findings)
            if result["state"] not in TERMINAL:
                pending += 1
            results.append(result)
    json.dump({"results": results, "pending": pending}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
