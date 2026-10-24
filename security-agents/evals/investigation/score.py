"""Scorer for the investigation eval.

Joins runner results (results.json) back to the manifest's ground truth and
produces case-level outcomes plus aggregate metrics:

  - TP / FP / FN at two levels:
      * flagged  — Scout submitted a hypothesis matching a ground-truth defect
      * resolved — a finding for a matching defect reached CONFIRMED or DIAGNOSED
  - precision / recall / F1 at both levels (negative + benign cases count
    every emitted hypothesis/finding as a false positive)
  - per-FN failure attribution:
      scout_miss      — no matching hypothesis was ever submitted
      rank_filtered   — matching hypothesis submitted but dropped by the
                        check-category gate before Probe (structural limit)
      probe_refuted   — matching hypothesis reached Probe and was refuted
      probe_inconclusive — Probe could not validate it
      diagnose_disagreed — confirmed but Diagnose produced no report entry
      agent_error     — an agent step failed / the run did not complete
  - latency (p50 / p95 wall-clock per case), tokens + USD + tool calls per
    investigation, findings per investigation, completion rate,
    snapshot-sha correlation, artifact integrity, repeat reproducibility.

Ambiguous cases (`expected.must_not_confirm`) are scored separately: a case
passes when nothing was confirmed/diagnosed; flagged-only hypotheses are
recorded, not scored as TP or FP.

Usage: python -m evals.investigation.score <results.json> [--json]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus

RESOLVED_STATUSES = {"confirmed", "diagnosed"}
SCOUT_CATEGORIES = {"access_control.ownership", "input_validation.bounds", "access_control.authentication"}

# CWE family buckets for secondary matching (exact CWE match is primary).
CWE_FAMILY: dict[str, str] = {
    "CWE-639": "authz",
    "CWE-862": "authz",
    "CWE-863": "authz",
    "CWE-613": "authn",
    "CWE-347": "authn",
    "CWE-345": "authn",
    "CWE-614": "authn",
    "CWE-208": "authn",
    "CWE-306": "authn",
    "CWE-295": "authn",
    "CWE-287": "authn",
    "CWE-1284": "input",
    "CWE-770": "input",
    "CWE-89": "input",
    "CWE-78": "input",
    "CWE-22": "input",
    "CWE-918": "input",
    "CWE-95": "input",
    "CWE-489": "input",
    "CWE-942": "input",
    "CWE-352": "authn",
}


def _family(cwe: str) -> str:
    return CWE_FAMILY.get(cwe, cwe)


def _path_of(obj: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, list) and v:
            return str(v[0])
        if isinstance(v, str) and v:
            return v
    return ""


def _basename(path: str) -> str:
    return path.split("/", 1)[-1] if "/" in path else path


def _matches(path: str, cwe: str, defects: list[dict[str, Any]], *, family: bool = True) -> bool:
    """path + CWE match against ground truth. Paths are compared on basename:
    defects are labelled repo-relative (`shop/app.py`) while hypotheses cite
    snapshot-relative paths that may carry the target/ prefix stripped."""
    if not path:
        return False
    for d in defects:
        if _basename(path) != _basename(
            getattr(d, "path", str(d.get("path", "")) if isinstance(d, dict) else "")
        ):
            continue
        dcwe = getattr(d, "cwe", d.get("cwe", "") if isinstance(d, dict) else "")
        if cwe == dcwe or (family and _family(cwe) == _family(dcwe)):
            return True
    return False


def _scout_hypotheses(result: dict[str, Any]) -> list[dict[str, Any]]:
    for s in result.get("step_outputs") or []:
        if s.get("agent") == "scout" and s.get("output"):
            return s["output"].get("hypotheses") or []
    return []


def _hyp_cwe(h: dict[str, Any]) -> str:
    return str(h.get("weakness_class", ""))


def _hyp_path(h: dict[str, Any]) -> str:
    return _path_of(h, "cited_paths")


def _finding_path(f: dict[str, Any]) -> str:
    ev = f.get("evidence") or {}
    spans = f.get("affected_spans") or f.get("spans") or []
    if spans and isinstance(spans[0], dict):
        return str(spans[0].get("path", ""))
    return _path_of(f, "affected_paths", "paths", "cited_paths") or str(ev.get("path", ""))


def score_result(case: corpus.Case, result: dict[str, Any]) -> dict[str, Any]:
    """Outcome record for one case run."""
    defects = list(case.defects)
    expected = case.expected
    out: dict[str, Any] = {
        "case_id": case.id,
        "kind": case.kind,
        "provider_status": result.get("provider_status"),
        "final_state": result.get("final_state"),
        "completed": result.get("final_state") == "completed",
        "repeat_idx": result.get("repeat_idx", 0),
    }
    if result.get("provider_status") not in ("fake", "live", "replay"):
        out["skipped"] = result.get("provider_status")
        return out

    hyps = _scout_hypotheses(result)
    report = result.get("report") or {}
    findings = report.get("findings") or []
    out["n_hypotheses"] = len(hyps)
    out["n_findings"] = len(findings)
    out["hypothesis_keys"] = sorted(f"{_hyp_cwe(h)}@{_hyp_path(h)}" for h in hyps)

    runs = result.get("runs") or []
    out["tool_calls"] = sum(r.get("tool_calls") or 0 for r in runs)
    out["tokens"] = sum(r.get("tokens") or 0 for r in runs)
    out["usd"] = round(sum(r.get("usd") or 0.0 for r in runs), 6)
    out["wall_clock_s"] = result.get("wall_clock_s")
    out["snapshot_sha_ok"] = result.get("snapshot_sha_matches_manifest")
    out["artifact_integrity_ok"] = out["snapshot_sha_ok"] and (report is not None or findings == [])
    out["run_errors"] = [
        {"agent": r["agent"], "error_code": r["error_code"]} for r in runs if r.get("status") == "failed"
    ]

    matched_flagged = [h for h in hyps if _matches(_hyp_path(h), _hyp_cwe(h), defects)]
    resolved = [f for f in findings if str(f.get("status")) in RESOLVED_STATUSES]
    # Findings carry no path; join each to its source hypothesis by title+CWE.
    hyp_by_key = {(h.get("title", ""), _hyp_cwe(h)): h for h in hyps}

    def finding_path(f: dict[str, Any]) -> str:
        src = hyp_by_key.get((f.get("title", ""), str(f.get("weakness_class", ""))))
        if src is not None:
            return _hyp_path(src)
        # fall back: any hypothesis with the same CWE on a defect path
        same_cwe = [h for h in hyps if _hyp_cwe(h) == str(f.get("weakness_class", ""))]
        return _hyp_path(same_cwe[0]) if same_cwe else _finding_path(f)

    matched_resolved = [
        f for f in resolved if _matches(finding_path(f), str(f.get("weakness_class", "")), defects)
    ]

    if case.kind == "vuln":
        out["tp_flagged"] = bool(matched_flagged)
        out["tp_resolved"] = bool(matched_resolved)
        out["fn_defects"] = [
            {"cwe": d.cwe, "path": d.path, "symbol": d.symbol}
            for d in defects
            if not any(_matches(_hyp_path(h), _hyp_cwe(h), [d]) for h in hyps)
        ]
        if not matched_flagged:
            if not out["completed"]:
                out["attribution"] = "agent_error" if out["run_errors"] else "scout_miss"
            else:
                out["attribution"] = "scout_miss"
        elif not matched_resolved:
            gated = [
                h for h in matched_flagged if str(h.get("suggested_check_category")) not in SCOUT_CATEGORIES
            ]
            if gated:
                out["attribution"] = "rank_filtered"
            elif not out["completed"]:
                out["attribution"] = "agent_error"
            else:
                statuses = {str(f.get("status")) for f in findings}
                confirmed = {str(f.get("status")) for f in resolved}
                if "refuted" in statuses and not confirmed:
                    out["attribution"] = "probe_refuted"
                elif "inconclusive" in statuses:
                    out["attribution"] = "probe_inconclusive"
                else:
                    out["attribution"] = "diagnose_disagreed"
        else:
            out["attribution"] = None
        # extra (non-matching) hypotheses on a vuln case count as FPs too
        out["extra_flagged"] = [
            {"cwe": _hyp_cwe(h), "path": _hyp_path(h), "title": h.get("title", "")[:120]}
            for h in hyps
            if not _matches(_hyp_path(h), _hyp_cwe(h), defects)
        ]
        out["fp_findings"] = [
            {
                "cwe": str(f.get("weakness_class")),
                "path": finding_path(f),
                "status": f.get("status"),
                "title": (f.get("title") or "")[:120],
            }
            for f in resolved
            if not _matches(finding_path(f), str(f.get("weakness_class", "")), defects)
        ]
    elif case.kind == "ambiguous":
        # must_not_confirm: resolved findings are violations; flags are informational.
        out["must_not_confirm"] = expected.get("must_not_confirm", True)
        out["confirmed_anyway"] = bool(resolved)
        out["flagged"] = [(_hyp_cwe(h), _hyp_path(h)) for h in hyps]
        out["resolved"] = [
            {"cwe": str(f.get("weakness_class")), "path": finding_path(f), "status": f.get("status")}
            for f in resolved
        ]
    else:  # hard_negative / benign: everything emitted is a false positive
        out["fp_flagged"] = [
            {"cwe": _hyp_cwe(h), "path": _hyp_path(h), "title": h.get("title", "")[:120]} for h in hyps
        ]
        out["fp_findings"] = [
            {
                "cwe": str(f.get("weakness_class")),
                "path": finding_path(f),
                "status": f.get("status"),
                "title": (f.get("title") or "")[:120],
            }
            for f in resolved
        ]
    return out


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        (2 * precision * recall / (precision + recall))
        if precision and recall
        else (0.0 if precision == 0 or recall == 0 else None)
    )
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def aggregate(scored: list[dict[str, Any]], *, provider: str) -> dict[str, Any]:
    scored = [s for s in scored if not s.get("skipped")]
    vuln = [s for s in scored if s["kind"] == "vuln"]
    negb = [s for s in scored if s["kind"] in ("hard_negative", "benign")]
    amb = [s for s in scored if s["kind"] == "ambiguous"]

    tp_f = sum(1 for s in vuln if s.get("tp_flagged"))
    fn_f = sum(1 for s in vuln if not s.get("tp_flagged"))
    fp_f = sum(len(s.get("fp_flagged") or []) for s in negb) + sum(
        len(s.get("extra_flagged") or []) for s in vuln
    )
    tp_r = sum(1 for s in vuln if s.get("tp_resolved"))
    fn_r = sum(1 for s in vuln if not s.get("tp_resolved"))
    fp_r = sum(len(s.get("fp_findings") or []) for s in negb) + sum(
        len(s.get("fp_findings") or []) for s in vuln
    )

    walls = [s["wall_clock_s"] for s in scored if s.get("wall_clock_s") is not None]
    tools = [s["tool_calls"] for s in scored if s.get("tool_calls") is not None]
    tokens = [s["tokens"] for s in scored if s.get("tokens") is not None]
    usds = [s["usd"] for s in scored if s.get("usd") is not None]

    attribution: dict[str, int] = {}
    for s in vuln:
        a = s.get("attribution")
        if a:
            attribution[a] = attribution.get(a, 0) + 1

    # reproducibility across repeats: identical hypothesis key sets per case
    by_case: dict[str, list[set]] = {}
    for s in scored:
        by_case.setdefault(s["case_id"], []).append(set(s.get("hypothesis_keys") or []))
    reproducible = sum(1 for keys in by_case.values() if len(keys) > 1 and all(k == keys[0] for k in keys))
    repeated = sum(1 for keys in by_case.values() if len(keys) > 1)

    return {
        "provider": provider,
        "cases": len(scored),
        "completed": sum(1 for s in scored if s.get("completed")),
        "completion_rate": (sum(1 for s in scored if s.get("completed")) / len(scored)) if scored else None,
        "flagged_level": _prf(tp_f, fp_f, fn_f),
        "resolved_level": _prf(tp_r, fp_r, fn_r),
        "findings_per_investigation": (sum(s.get("n_findings") or 0 for s in scored) / len(scored))
        if scored
        else None,
        "hypotheses_per_investigation": (sum(s.get("n_hypotheses") or 0 for s in scored) / len(scored))
        if scored
        else None,
        "latency_s": {
            "p50": statistics.median(walls) if walls else None,
            "p95": _pct(walls, 0.95),
            "min": min(walls) if walls else None,
            "max": max(walls) if walls else None,
        },
        "cost_per_investigation": {
            "tokens_total": sum(tokens) if tokens else None,
            "usd_total": round(sum(usds), 6) if usds else None,
            "usd_median": statistics.median(usds) if usds else None,
        },
        "tool_calls_per_investigation": {
            "median": statistics.median(tools) if tools else None,
            "p95": _pct([float(t) for t in tools], 0.95),
        },
        "snapshot_sha_correlation": {
            "checked": sum(1 for s in scored if s.get("snapshot_sha_ok") is not None),
            "matched": sum(1 for s in scored if s.get("snapshot_sha_ok")),
        },
        "artifact_integrity": {
            "checked": sum(1 for s in scored if s.get("artifact_integrity_ok") is not None),
            "ok": sum(1 for s in scored if s.get("artifact_integrity_ok")),
        },
        "reproducibility": {"repeated_cases": repeated, "identical_runs": reproducible},
        "failure_attribution": attribution,
        "ambiguous": {
            "cases": len(amb),
            "confirmed_anyway": sum(1 for s in amb if s.get("confirmed_anyway")),
            "flagged_only": sum(1 for s in amb if s.get("flagged") and not s.get("confirmed_anyway")),
        },
        "vulnerable_cases": len(vuln),
        "negative_benign_cases": len(negb),
    }


def load_and_score(results_path: Path) -> dict[str, Any]:
    results = json.loads(results_path.read_text())
    cases = {c.id: c for c in corpus.load_cases()}
    scored = [score_result(cases[r["case_id"]], r) for r in results if r["case_id"] in cases]
    provider = results[0].get("provider", "?") if results else "?"
    return {"scored": scored, "aggregate": aggregate(scored, provider=provider)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--json", action="store_true", help="print full per-case JSON")
    args = ap.parse_args(argv)
    data = load_and_score(args.results)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(json.dumps(data["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
