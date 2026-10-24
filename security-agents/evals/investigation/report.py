"""Markdown report generator for the investigation eval.

Renders a scored run (score.py output) into REPORT.md with the measured
metrics table, per-case outcomes, failure attribution, example successful and
unsuccessful investigations, and an explicit limitations section.

Usage: python -m evals.investigation.report <run_dir> [--out REPORT.md]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score


def _f(v: Any, pct: bool = True, digits: int = 3) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.{digits}g}%" if pct else f"{v:.{digits}g}"


def render(results_path: Path) -> str:
    data = score.load_and_score(results_path)
    agg = data["aggregate"]
    scored = data["scored"]
    run_meta = (
        json.loads((results_path.parent / "run_meta.json").read_text())
        if (results_path.parent / "run_meta.json").exists()
        else {}
    )

    L: list[str] = []
    a = L.append
    a("# Investigation quality evaluation — results report")
    a("")
    a(
        f"- Run: `{results_path.parent.name}` · provider mode: **{agg['provider']}** "
        "(FakeLLM scripted policy — simulated provider, **not** a real LLM)"
    )
    a(f"- Repo commit: `{run_meta.get('repo_commit', 'unknown')}`")
    a(
        f"- Cases: {agg['cases']} ({agg['vulnerable_cases']} vulnerable, "
        f"{agg['negative_benign_cases']} hard-negative/benign, {agg['ambiguous']['cases']} ambiguous)"
    )
    a(f"- Completed investigations: {agg['completed']}/{agg['cases']} ({_f(agg['completion_rate'])})")
    a("")

    a("## Measured performance")
    a("")
    a("| Level | TP | FP | FN | Precision | Recall | F1 |")
    a("|---|---|---|---|---|---|---|")
    for lvl, key in (
        ("flagged (Scout hypothesis)", "flagged_level"),
        ("resolved (confirmed/diagnosed finding)", "resolved_level"),
    ):
        m = agg[key]
        a(
            f"| {lvl} | {m['tp']} | {m['fp']} | {m['fn']} | {_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} |"
        )
    a("")
    a("| Operational metric | Value |")
    a("|---|---|")
    a(f"| Hypotheses / investigation | {_f(agg['hypotheses_per_investigation'], pct=False)} |")
    a(f"| Findings / investigation | {_f(agg['findings_per_investigation'], pct=False)} |")
    a(
        f"| Latency p50 / p95 (s) | {_f(agg['latency_s']['p50'], pct=False)} / {_f(agg['latency_s']['p95'], pct=False)} |"
    )
    a(
        f"| Tool calls / investigation (median / p95) | {_f(agg['tool_calls_per_investigation']['median'], pct=False)} / {_f(agg['tool_calls_per_investigation']['p95'], pct=False)} |"
    )
    a(f"| Tokens / investigation (total) | {agg['cost_per_investigation']['tokens_total']} |")
    a(f"| USD / investigation (total, list-price estimate) | ${agg['cost_per_investigation']['usd_total']} |")
    a(
        f"| Snapshot-sha correlation | {agg['snapshot_sha_correlation']['matched']}/{agg['snapshot_sha_correlation']['checked']} |"
    )
    a(
        f"| Artifact integrity (report + sha) | {agg['artifact_integrity']['ok']}/{agg['artifact_integrity']['checked']} |"
    )
    rep = agg["reproducibility"]
    if rep["repeated_cases"]:
        a(
            f"| Reproducibility (identical hypothesis sets across repeats) | {rep['identical_runs']}/{rep['repeated_cases']} |"
        )
    else:
        a("| Reproducibility | single run — see `--repeat` regression run |")
    a("")

    a("## Failure attribution (vulnerable cases not resolved)")
    a("")
    attr = agg["failure_attribution"]
    if attr:
        a("| Attributed stage | Count | Meaning |")
        a("|---|---|---|")
        meaning = {
            "scout_miss": "no matching hypothesis was ever submitted",
            "rank_filtered": "hypothesis dropped by the check-category gate before Probe",
            "probe_refuted": "Probe executed its template and reported the true vuln REFUTED",
            "probe_inconclusive": "Probe could not model the vuln with an available template",
            "diagnose_disagreed": "confirmed but Diagnose produced no finding entry",
            "agent_error": "an agent step failed or the run did not complete",
        }
        for k, v in sorted(attr.items(), key=lambda x: -x[1]):
            a(f"| {k} | {v} | {meaning.get(k, '')} |")
    a("")

    a("## Per-case outcomes")
    a("")
    a("| Case | Kind | Flagged | Resolved | Attribution / extras |")
    a("|---|---|---|---|---|")
    for s in scored:
        if s.get("skipped"):
            a(f"| {s['case_id']} | {s['kind']} | — | — | skipped: {s['skipped']} |")
            continue
        flag = "TP" if s.get("tp_flagged") else ("FP" if (s.get("fp_flagged")) else "—")
        res = "TP" if s.get("tp_resolved") else ("FP" if s.get("fp_findings") else "—")
        extras = []
        if s.get("attribution"):
            extras.append(s["attribution"])
        for x in s.get("extra_flagged") or []:
            extras.append(f"extra {x['cwe']}@{x['path']}")
        for x in s.get("fp_flagged") or []:
            extras.append(f"fp {x['cwe']}@{x['path']}")
        if s["kind"] == "ambiguous":
            flag = ", ".join(f"{c}@{p}" for c, p in (s.get("flagged") or [])) or "—"
            res = "VIOLATION" if s.get("confirmed_anyway") else "not confirmed"
        a(f"| {s['case_id']} | {s['kind']} | {flag} | {res} | {'; '.join(extras) or ''} |")
    a("")

    # ---- examples
    a("## Example investigations")
    a("")
    tp_examples = [s for s in scored if s.get("tp_resolved")]
    fn_examples = [s for s in scored if s["kind"] == "vuln" and s.get("attribution")]
    fp_examples = [s for s in scored if s.get("fp_flagged") or s.get("extra_flagged")]
    if tp_examples:
        s = tp_examples[0]
        a(f"### Successful: `{s['case_id']}`")
        a("")
        a(
            f"Scout flagged the planted defect, Probe executed the check template in the "
            f"sandbox and confirmed it, Trace and Diagnose produced the finding "
            f"(tool calls: {s.get('tool_calls')}, wall: {s.get('wall_clock_s')}s)."
        )
        a("")
    if fn_examples:
        s = fn_examples[0]
        a(f"### Unsuccessful: `{s['case_id']}`")
        a("")
        a(f"Outcome: **{s.get('attribution')}**. Flagged keys: {s.get('hypothesis_keys')}.")
        a("")
    if fp_examples:
        s = fp_examples[0]
        a(f"### False positive: `{s['case_id']}`")
        a("")
        fps = (s.get("fp_flagged") or []) + (s.get("extra_flagged") or [])
        a(
            "Hypotheses matching no ground-truth defect: "
            + "; ".join(f"{x['cwe']}@{x['path']} ({x['title'][:80]})" for x in fps)
        )
        a("")

    a("## Limitations of this measurement")
    a("")
    a(
        "- Provider is the **scripted FakeLLM policy** (`fake_policy.py`): deterministic "
        "pattern-matching over real tool results, not a language model. These numbers "
        "measure the *platform pipeline* (Scout contract, check-category gate, Probe "
        "templates, sandbox execution, Diagnose), not model reasoning quality."
    )
    a(
        "- Live-provider results: **unavailable** — no authorized API key or cost budget "
        "exists in this environment, so no paid calls were run."
    )
    a("- Hypothesis `confidence` is a scripted constant, not a calibrated probability.")
    a(
        "- Ground truth labels never enter the agent's input (leakage is checked by a "
        "regression test over the recorded `llm_calls.json`)."
    )
    a("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path, help="directory containing results.json")
    ap.add_argument("--out", type=Path, help="output markdown path (default: <run_dir>/REPORT.md)")
    args = ap.parse_args(argv)
    out = args.out or args.run_dir / "REPORT.md"
    out.write_text(render(args.run_dir / "results.json"))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
