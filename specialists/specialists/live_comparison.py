"""Does the hybrid (trained specialists + Claude) behave better than Claude alone? A live comparison.

Runs the full seven-agent assessment on the authorized planted-defect target
``examples/payments-lite`` in three configurations and scores every run against the answer key
(``labels.yaml``: ``planted_findings`` and ``negatives``). Agents never see that answer key.
They do receive behavioral expectations (routes + invariants) loaded from the ``expectations:``
section of the same file — the same statements appear in the target README / docstrings and are
what a production assessment would get from code intelligence. Discovery numbers are therefore
"with intended-behavior context", not a fully blind hunt.

* ``claude_only``       Orchestrator(specialists=Specialists(enabled=False)): no specialist advice.
* ``hybrid``            the default trained specialists (checkpoints in ``specialists/checkpoints``).
* ``specialists_only``  the specialists' own advice for the planted spans, without Claude. It
                        cannot validate, patch or verify anything, so only the parts that have a
                        label to compare with are scored (Scout rank, Probe category, Diagnose class).

The two Claude configurations are run ``--reps`` times each, interleaved (A B A B ...), because model
output varies run to run; results are means with spread. Per agent and per assessment it records
whether each planted finding was hypothesised, selected for validation, validated (confirmed by an
executed check), correctly diagnosed and fixed with an accepted Verify verdict; false positives;
tokens, USD and latency.

    # free pipeline check (scripted trajectories; NOT evidence about Claude):
    uv run python -m specialists.live_comparison --mode replay --reps 1 --out /tmp/replay.json
    # live (costs money; per-run cap LIVE_SEVEN_MAX_USD, total cap --total-max-usd):
    RUN_LIVE_LLM_EVAL=1 LIVE_SEVEN_MAX_USD=4 uv run python -m specialists.live_comparison \\
        --mode live --i-have-authorization --reps 3 --total-max-usd 20

Every finished run (including crashes) is written to ``--out`` immediately and the summary is
recomputed, so an interrupted live evaluation keeps what it paid for. ``--resume`` continues only
when mode and settings match the file on disk; otherwise it refuses so incomparable runs are not
pooled.

This module imports ``security_agents`` lazily: it is an evaluation harness for the workspace, not
part of the specialists' runtime (which stays dependency-free).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .runtime import Specialists

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "examples" / "payments-lite"
OUT = Path(__file__).resolve().parent.parent / "results" / "live_comparison.json"
AGENTS = ("scout", "probe", "trace", "diagnose", "patch", "verify", "fortify")
CONFIGS = ("claude_only", "hybrid")

# Which reported CWE counts as "the right class" for a planted finding. Exact = the label's class;
# family = closely related CWEs a reviewer would accept for the same defect (documented, not tuned).
CLASS_FAMILY = {
    "fnd_planted_authz": {
        "CWE-639",
        "CWE-284",
        "CWE-285",
        "CWE-862",
        "CWE-863",
        "CWE-566",
        "CWE-200",
    },
    "fnd_planted_refund_bound": {
        "CWE-840",
        "CWE-841",
        "CWE-1284",
        "CWE-20",
        "CWE-682",
        "CWE-837",
        "CWE-799",
    },
}
# Expected Diagnose cause class, read from the label's root_cause_span note (our interpretation:
# "no require_owner call" = a missing check; "compares only the new refund" = a wrong check).
EXPECTED_CAUSE = {"fnd_planted_authz": "missing_check", "fnd_planted_refund_bound": "wrong_check"}
WSTG_TO_CATEGORY = {
    "WSTG-ATHZ-04": "access_control.ownership",
    "WSTG-BUSL-01": "input_validation.bounds",
}


# ------------------------------------------------------------------ answer key


def load_key(labels: Path = TARGET / "labels.yaml") -> dict[str, Any]:
    raw = yaml.safe_load(labels.read_text(encoding="utf-8"))
    prefix = f"{raw.get('target_dir')}/" if raw.get("target_dir") else ""
    planted = []
    for p in raw["planted_findings"]:
        span = p["root_cause_span"]
        path = span["path"][len(prefix) :] if span["path"].startswith(prefix) else span["path"]
        planted.append(
            {
                "id": p["id"],
                "subject": p["subject"],
                "weakness_class": p["weakness_class"],
                "expectation": p["violated_expectation"],
                "path": path,
                "symbol": span["symbol"],
                "check_category": p["expected_check_category"],
            }
        )
    return {"planted": planted, "negatives": raw.get("negatives", [])}


def _route(ep: Any) -> str:
    return f"{(ep.method or '').upper()} {ep.path_template or ''}".strip()


def _locate(intel: Any, key: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map each planted finding and negative subject to its handler entry point (id, path, lines)."""
    by_route = {_route(e): e for e in intel.entry_points()}
    out: dict[str, dict[str, Any]] = {}
    for p in key["planted"]:
        ep = by_route.get(p["subject"]) or next(
            (e for e in intel.entry_points() if e.handler_symbol.rsplit(".", 1)[-1] == p["symbol"]),
            None,
        )
        out[p["id"]] = {
            "entry_point_id": ep.id if ep else None,
            "path": p["path"],
            "lines": (ep.span.start_line, ep.span.end_line) if ep else None,
            "symbol": p["symbol"],
            "weakness_class": p["weakness_class"],
            "planted_id": p["id"],
        }
    for n in key["negatives"]:
        ep = by_route.get(n["subject"])
        if ep is None:
            continue
        hyp_class = n.get("hypothesis_class") or ""
        # Key includes class so POST /refunds can be both a planted CWE-840 and a CWE-639 negative.
        nid = f"neg:{n['subject']}:{hyp_class}"
        out[nid] = {
            "entry_point_id": ep.id,
            "path": ep.path,
            "lines": (ep.span.start_line, ep.span.end_line),
            "symbol": ep.handler_symbol.rsplit(".", 1)[-1],
            "hypothesis_class": hyp_class,
        }
    return out


def _covers(refs: list[Any], loc: dict[str, Any]) -> bool:
    """A citation covers a location when it names the symbol or its span overlaps the handler."""
    for r in refs:
        if r.path and r.path != loc["path"]:
            continue
        if r.symbol and r.symbol.rsplit(".", 1)[-1] == loc["symbol"]:
            return True
        if r.span and loc["lines"]:
            lo, hi = loc["lines"]
            if r.span.start_line <= hi and r.span.end_line >= lo:
                return True
    return False


def _matches(h: Any, loc: dict[str, Any]) -> bool:
    """Match a hypothesis to a location by entry point (or span fallback).

    Prefer entry-point identity. If both sides name an entry point and they differ, that is a
    mismatch (do not fall back to span overlap — a broad citation like ``create_app`` would
    otherwise match every handler). Span/symbol fallback is only used when either side lacks an
    entry-point id.
    """
    loc_ep = loc.get("entry_point_id")
    hyp_ep = getattr(h, "entry_point_id", None)
    if loc_ep and hyp_ep:
        return loc_ep == hyp_ep
    if loc_ep and hyp_ep is None:
        return _covers(h.cited_spans, loc)
    if not loc_ep:
        return _covers(h.cited_spans, loc)
    return False


def _matches_planted(h: Any, loc: dict[str, Any]) -> bool:
    """Planted match requires route/entry-point agreement *and* an accepted class family."""
    if not _matches(h, loc):
        return False
    planted_id = loc.get("planted_id")
    family = CLASS_FAMILY.get(planted_id or "", set()) or {loc.get("weakness_class")}
    return getattr(h, "weakness_class", None) in family


def _matches_negative(h: Any, loc: dict[str, Any]) -> bool:
    """Negative lookalike: same handler and the labelled hypothesis_class."""
    if not _matches(h, loc):
        return False
    want = loc.get("hypothesis_class")
    return not want or getattr(h, "weakness_class", None) == want


def _verify_evidence(fixed: list[Any]) -> list[dict[str, Any]]:
    """Bounded executed evidence for accepted patches (survives workdir cleanup)."""
    out: list[dict[str, Any]] = []
    for f in fixed:
        for v in f.verifications:
            out.append(
                {
                    "verification_id": getattr(v, "id", None),
                    "patch_id": getattr(v, "patch_id", None),
                    "verdict": v.verdict,
                    "sandbox_run_ids": list(getattr(v, "sandbox_run_ids", []) or []),
                    "verify_run_id": getattr(v, "verify_run_id", None),
                    "checks": [
                        {
                            "name": (c.name.value if hasattr(c.name, "value") else str(c.name)),
                            "passed": bool(c.passed),
                            "detail": (c.detail or "")[:200],
                        }
                        for c in (v.checks or [])
                    ],
                    "reasons": [str(r)[:200] for r in (getattr(v, "reasons", None) or [])][:10],
                }
            )
    return out


# ------------------------------------------------------------------ scoring one run


def score_run(result: Any, intel: Any, key: dict[str, Any], wall_s: float) -> dict[str, Any]:
    locs = _locate(intel, key)
    findings = result.findings
    planted: dict[str, Any] = {}
    matched_ids: set[str] = set()
    for p in key["planted"]:
        loc = locs[p["id"]]
        hits = [(i, f) for i, f in enumerate(findings) if _matches_planted(f.hypothesis, loc)]
        matched_ids.update(f.finding_id for _, f in hits)
        validated = [f for _, f in hits if f.validation is not None]
        confirmed = [f for f in validated if f.validation.outcome.value == "confirmed"]
        diagnosed = [f for f in confirmed if f.root_cause is not None]
        correct_dx = [
            f
            for f in diagnosed
            if _covers(f.root_cause.cause_spans, loc)
            and p["expectation"] in f.root_cause.violated_expectation_ids
        ]
        fixed = [
            f for f in confirmed if f.verifications and f.verifications[-1].verdict == "accepted"
        ]
        planted[p["id"]] = {
            "hypothesised": bool(hits),
            "rank": hits[0][0] + 1 if hits else None,  # position after Scout's (re-)ranking
            "class_exact": any(f.hypothesis.weakness_class == p["weakness_class"] for _, f in hits),
            "class_family": any(
                f.hypothesis.weakness_class in CLASS_FAMILY.get(p["id"], set()) for _, f in hits
            ),
            "reported_classes": sorted({f.hypothesis.weakness_class for _, f in hits}),
            "selected_for_validation": bool(validated),
            "validation_outcomes": [f.validation.outcome.value for f in validated],
            "validated": bool(confirmed),
            "diagnosed": bool(diagnosed),
            "diagnosed_correctly": bool(correct_dx),
            "cause_classes": [f.root_cause.cause_class.value for f in diagnosed],
            "cause_class_expected": any(
                f.root_cause.cause_class.value == EXPECTED_CAUSE.get(p["id"]) for f in diagnosed
            ),
            "patched": any(f.candidates for f in confirmed),
            "fixed_verify_accepted": bool(fixed),
            "verdicts": [v.verdict for f in confirmed for v in f.verifications],
            "verify_evidence": _verify_evidence(fixed),
            "hardened": any(f.status == "hardened" for f in fixed),
        }
    unmatched = [f for f in findings if f.finding_id not in matched_ids]
    false_pos = [
        f
        for f in unmatched
        if f.validation is not None and f.validation.outcome.value == "confirmed"
    ]
    negatives_hit = [
        k
        for k, loc in locs.items()
        if k.startswith("neg:") and any(_matches_negative(f.hypothesis, loc) for f in unmatched)
    ]
    report = result.report()
    per_agent: dict[str, Any] = {}
    for agent in AGENTS:
        runs = [r for r in result.runs if r.agent.value == agent]
        failed = [r for r in runs if r.error_code]
        per_agent[agent] = {
            "runs": len(runs),
            "succeeded": bool(runs) and all(r.status == "succeeded" for r in runs),
            "error_codes": [r.error_code.value for r in failed],
            "error_details": [(r.error_detail or "")[:300] for r in failed],
            # Per failed invocation (aligned with error_codes), not the role-wide sum.
            "error_turns": [int(r.llm_turns or 0) for r in failed],
            "llm_turns": sum(r.llm_turns for r in runs),
            "tool_calls": sum(r.tool_calls for r in runs),
            "tokens": sum(r.usage.total_tokens for r in runs),
            "usd": round(sum(r.usage.usd for r in runs), 6),
            "latency_s": round(
                sum(((r.ended_at or r.started_at) - r.started_at).total_seconds() for r in runs), 3
            ),
        }
    spec = report.get("specialist_inferences", [])
    return {
        "state": report["state"],
        "error_code": report.get("error_code"),
        "provider_modes": report["provider_modes"],
        "models": sorted({r.model_id for r in result.runs}),
        "planted": planted,
        "hypotheses": [
            {
                "title": f.hypothesis.title,
                "weakness_class": f.hypothesis.weakness_class,
                "entry_point_id": f.hypothesis.entry_point_id,
                "validation": f.validation.outcome.value if f.validation else None,
                "status": f.status,
            }
            for f in findings
        ],
        "false_positives_confirmed": len(false_pos),
        "false_positive_titles": [f.hypothesis.title for f in false_pos],
        "unmatched_hypotheses": len(unmatched),
        "negative_subjects_hypothesised": negatives_hit,
        "specialist_rerank_changed_order": "specialist.rerank" in report["audit_actions"],
        "specialist_sources": {s["agent"]: s["source"] for s in spec},
        "verify_specialist_top": [s["top"] for s in spec if s["agent"] == "verify"],
        "verify_specialist_notes": [
            n for f in findings for n in f.notes if n.startswith("verify specialist")
        ],
        "per_agent": per_agent,
        "usage": report["usage"],
        "tokens": report["usage"]["input_tokens"] + report["usage"]["output_tokens"],
        "usd": round(report["usage"]["usd"], 6),
        "wall_s": round(wall_s, 2),
    }


# ------------------------------------------------------------------ one assessment


def _usage_from_orch(orch: Any) -> tuple[float, int]:
    """Sum USD/tokens from orchestrator agent runs (partial live spend after a crash)."""
    if orch is None:
        return 0.0, 0
    runs = getattr(orch, "runs", None) or []
    usd = 0.0
    tokens = 0
    for r in runs:
        usage = getattr(r, "usage", None)
        if usage is None:
            continue
        usd += float(getattr(usage, "usd", 0.0) or 0.0)
        tokens += int(getattr(usage, "total_tokens", 0) or 0)
    return usd, tokens


def run_one(
    config: str, *, mode: str, max_usd: float, max_hypotheses: int, validate: int
) -> tuple[dict[str, Any], Any, Any]:
    sa_root = REPO / "security-agents"
    if str(sa_root) not in sys.path:
        sys.path.insert(0, str(sa_root))
    from security_agents.checks.library import default_library
    from security_agents.cli import TARGET_ID, build_scope
    from security_agents.contracts import Approval, Budget, new_id
    from security_agents.intel import LocalIntel, load_expectations
    from security_agents.llm import FakeLLM, ProviderGateway, live_gateway_from_env
    from security_agents.sandbox.driver import LocalProcessDriver
    from security_agents.sandbox.workspace import SnapshotHandle, copy_tree
    from security_agents.workflow import Orchestrator, OrchestratorConfig

    recipe_path = TARGET / "build_recipe.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    workdir = Path(tempfile.mkdtemp(prefix=f"livecmp-{config}-"))
    orch: Any = None
    t0 = time.monotonic()
    try:
        staging = workdir / "staging"
        copy_tree(
            TARGET / str(recipe.get("target_dir") or "."), staging
        )  # the answer key stays out
        shutil.copy2(recipe_path, staging / "build_recipe.json")
        snapshot = SnapshotHandle.from_directory(
            staging, workdir / "snapshot", snapshot_id=new_id("snp"), repo_id="rep_demo"
        )
        expectations = load_expectations(
            TARGET / "labels.yaml",
            snapshot_id=snapshot.snapshot_id,
            commit_sha=snapshot.commit_sha,
            strip_prefix=f"{recipe.get('target_dir')}/" if recipe.get("target_dir") else None,
        )
        intel = LocalIntel(snapshot, expectations=expectations)
        library = default_library()
        if mode == "live":
            gateway = live_gateway_from_env()
        else:
            from tests import fake_agents  # scripted trajectories: a pipeline check, not evidence

            gateway = ProviderGateway(FakeLLM(fake_agents.all_scripts(snapshot.root)))

        def approver(finding: Any) -> Any:
            if finding.plan is None:
                return None
            return Approval(
                project_id="prj_demo",
                subject_type="remediation",
                subject_id=finding.plan.id,
                decision="approved",
                decided_by="usr_operator",
                reason="auto-approved by live_comparison",
            )

        cfg = OrchestratorConfig(
            assessment_id=new_id("asm"),
            project_id="prj_demo",
            org_id="org_demo",
            scope=build_scope(categories=sorted(library.categories())),
            target_id=TARGET_ID,
            requested_by="usr_operator",
            budget=Budget(max_tokens=1_000_000, max_usd=max_usd, max_wall_clock_s=1800.0),
            max_hypotheses=max_hypotheses,
            max_hypotheses_to_validate=validate,
        )
        specialists = Specialists(enabled=False) if config == "claude_only" else Specialists()
        orch = Orchestrator(
            cfg,
            intel=intel,
            gateway=gateway,
            driver=LocalProcessDriver(allow=True),
            library=library,
            workdir=workdir / "work",
            approve_remediation=approver,
            specialists=specialists,
        )
        result = orch.run()
        wall = time.monotonic() - t0
        return score_run(result, intel, load_key(), wall), result, intel
    except Exception as exc:
        wall = time.monotonic() - t0
        usd, tokens = _usage_from_orch(orch)
        debited = "orchestrator_runs"
        if usd <= 0:
            # No recorded spend (crash before any turn, or usage unavailable): debit the run cap
            # so the total budget cannot be bypassed by repeated late crashes.
            usd = float(max_usd)
            debited = "per_run_cap"
        score = {
            "state": "crashed",
            "error_code": type(exc).__name__,
            "error_detail": str(exc)[:500],
            "usd": round(usd, 6),
            "tokens": tokens,
            "wall_s": round(wall, 2),
            "usage_debited": debited,
            "planted": {},
            "false_positives_confirmed": 0,
            "per_agent": {},
        }
        return score, None, None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ------------------------------------------------------------------ specialists only (no Claude)


def specialists_only() -> dict[str, Any]:
    """The deployed specialists' advice for the planted spans, scored where a label exists."""
    sa_root = REPO / "security-agents"
    if str(sa_root) not in sys.path:
        sys.path.insert(0, str(sa_root))
    from security_agents import specialist_bridge
    from security_agents.intel import LocalIntel
    from security_agents.sandbox.workspace import SnapshotHandle

    key = load_key()
    work = Path(tempfile.mkdtemp(prefix="livecmp-sp-"))
    snap = SnapshotHandle.from_directory(
        TARGET / "target", work / "snapshot", snapshot_id="snp_sp", repo_id="rep_demo"
    )
    intel = LocalIntel(snap)
    sp = Specialists()
    rows, _ = specialist_bridge.scout_survey(
        sp, intel
    )  # exactly what the hybrid Scout prompt shows
    locs = _locate(intel, key)
    raw = yaml.safe_load((TARGET / "labels.yaml").read_text(encoding="utf-8"))
    statements = {e["id"]: e["statement"] for e in raw["expectations"]}
    out: dict[str, Any] = {
        "scout_survey": [
            {k: r[k] for k in ("entry_point_id", "suspicion", "likely_cwe")} for r in rows
        ],
        "planted": {},
    }
    for p in key["planted"]:
        loc = locs[p["id"]]
        rank = next(
            (i + 1 for i, r in enumerate(rows) if r["entry_point_id"] == loc["entry_point_id"]),
            None,
        )
        code = specialist_bridge._span_text(intel, loc["path"], *loc["lines"])
        probe = sp.advise(
            "probe",
            {
                "hypothesis": f"{p['subject']} may violate: {statements[p['expectation']]}",
                "cwe": p["weakness_class"],
            },
        )
        dx = sp.advise("diagnose", {"code": code, "cwe": p["weakness_class"], "evidence": ""})
        patch = sp.advise("patch", {"code": code, "cwe": p["weakness_class"]})
        scout_row = next((r for r in rows if r["entry_point_id"] == loc["entry_point_id"]), {})
        out["planted"][p["id"]] = {
            "scout_rank": rank,
            "of": len(rows),
            "scout_top3": bool(rank and rank <= 3),
            "scout_likely_cwe": scout_row.get("likely_cwe"),
            "scout_likely_cwe_in_family": scout_row.get("likely_cwe")
            in CLASS_FAMILY.get(p["id"], set()),
            "probe_top": probe.top,
            "probe_source": probe.source,
            "probe_maps_to_expected_category": WSTG_TO_CATEGORY.get(probe.top or "")
            == p["check_category"],
            "diagnose_top": dx.top,
            "diagnose_source": dx.source,
            "diagnose_matches_expected_cause": dx.top == EXPECTED_CAUSE.get(p["id"]),
            "patch_top": patch.top,
            "patch_source": patch.source,
        }
    negatives = [k for k in locs if k.startswith("neg:")]
    out["negatives_in_scout_top3"] = [
        k
        for k in negatives
        if any(r["entry_point_id"] == locs[k]["entry_point_id"] for r in rows[:3])
    ]
    out["not_scored"] = {
        "validated / fixed / verify": "needs executed checks and patches, which only the Claude agents produce",
        "trace / fortify": "labels.yaml has no ATT&CK or D3FEND answer key",
    }
    shutil.rmtree(work, ignore_errors=True)
    return out


# ------------------------------------------------------------------ aggregation


def _num(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 4),
        "sd": round(statistics.stdev(xs), 4) if len(xs) > 1 else 0.0,
        "min": round(min(xs), 4),
        "max": round(max(xs), 4),
    }


def _rate(flags: list[bool]) -> dict[str, Any]:
    """k/n with a Wilson 95% interval (honest about how little a handful of runs says)."""
    n, k = len(flags), sum(bool(f) for f in flags)
    if n == 0:
        return {"k": 0, "n": 0}
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {
        "k": k,
        "n": n,
        "rate": round(p, 3),
        "wilson95": [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)],
    }


STAGES = (
    "hypothesised",
    "class_family",
    "selected_for_validation",
    "validated",
    "diagnosed_correctly",
    "cause_class_expected",
    "fixed_verify_accepted",
    "hardened",
)


def summarise(runs: list[dict[str, Any]], planted_ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for config in CONFIGS:
        rs = [r["score"] for r in runs if r["config"] == config]
        if not rs:
            continue
        s: dict[str, Any] = {
            "runs": len(rs),
            "completed": _rate([r["state"] == "completed" for r in rs]),
        }
        s["planted"] = {
            pid: {st: _rate([r["planted"][pid][st] for r in rs]) for st in STAGES}
            for pid in planted_ids
        }
        s["planted"].update(
            {
                f"{pid}.rank": _num(
                    [r["planted"][pid]["rank"] for r in rs if r["planted"][pid]["rank"]]
                )
                for pid in planted_ids
            }
        )
        s["planted_fixed_per_assessment"] = _num(
            [sum(r["planted"][p]["fixed_verify_accepted"] for p in planted_ids) for r in rs]
        )
        s["planted_validated_per_assessment"] = _num(
            [sum(r["planted"][p]["validated"] for p in planted_ids) for r in rs]
        )
        s["false_positives_confirmed"] = _num([r["false_positives_confirmed"] for r in rs])
        s["unmatched_hypotheses"] = _num([r["unmatched_hypotheses"] for r in rs])
        s["tokens"] = _num([r["tokens"] for r in rs])
        s["usd"] = _num([r["usd"] for r in rs])
        s["wall_s"] = _num([r["wall_s"] for r in rs])
        s["per_agent"] = {
            a: {
                "succeeded": _rate(
                    [r["per_agent"][a]["succeeded"] for r in rs if r["per_agent"][a]["runs"]]
                ),
                "tokens": _num(
                    [r["per_agent"][a]["tokens"] for r in rs if r["per_agent"][a]["runs"]]
                ),
                "usd": _num([r["per_agent"][a]["usd"] for r in rs if r["per_agent"][a]["runs"]]),
                "latency_s": _num(
                    [r["per_agent"][a]["latency_s"] for r in rs if r["per_agent"][a]["runs"]]
                ),
                "llm_turns": _num(
                    [r["per_agent"][a]["llm_turns"] for r in rs if r["per_agent"][a]["runs"]]
                ),
                "invoked": _rate([r["per_agent"][a]["runs"] > 0 for r in rs]),
            }
            for a in AGENTS
        }
        if config == "hybrid":
            s["specialist_rerank_changed_order"] = _rate(
                [r["specialist_rerank_changed_order"] for r in rs]
            )
            s["verify_specialist_disagreed_with_accepted"] = _rate(
                [bool(r["verify_specialist_notes"]) for r in rs]
            )
        out[config] = s
    return out


def provider_failure(score: dict[str, Any]) -> str | None:
    """Why a run says nothing about agent quality (the provider failed: credits, HTTP, outage), or None.

    LLM_UNAVAILABLE covers both an upstream failure after retries and a model refusal; a refusal is
    model behaviour and stays in the results. When ``error_details`` is missing, use the matching
    ``error_turns`` entry (turns for that failed invocation only) — not the role-wide ``llm_turns``
    sum, which would hide a later zero-turn outage after an earlier success.
    """
    if score.get("state") == "crashed":
        return f"crashed: {score.get('error_code')}"
    for agent, a in (score.get("per_agent") or {}).items():
        details = a.get("error_details") or []
        error_turns = a.get("error_turns") or []
        for i, code in enumerate(a.get("error_codes", [])):
            if code != "LLM_UNAVAILABLE":
                continue
            detail = details[i] if i < len(details) else None
            if detail is not None and detail.startswith("refusal"):
                continue
            if detail is None:
                turns = int(error_turns[i]) if i < len(error_turns) else 0
                if turns > 0:
                    continue
            return f"{agent}: LLM_UNAVAILABLE {detail or '(provider error)'}"[:200]
    return None


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, default=str) + "\n", encoding="utf-8")


def _finish(
    doc: dict[str, Any], runs: list[dict[str, Any]], planted_ids: list[str], spent: float, out: Path
) -> None:
    valid = [r for r in runs if not provider_failure(r["score"])]
    doc["runs"] = runs
    doc["excluded_runs"] = [
        {
            "config": r["config"],
            "attempt": r.get("attempt", r.get("rep")),
            "usd": r["score"].get("usd", 0.0),
            "reason": provider_failure(r["score"]),
        }
        for r in runs
        if provider_failure(r["score"])
    ]
    doc["summary"] = summarise(valid, planted_ids)
    doc["total_usd"] = round(spent, 4)
    _write(out, doc)


def _settings(
    *, max_hypotheses: int, validate: int, per_run_cap: float, total_max_usd: float
) -> dict[str, Any]:
    return {
        "max_hypotheses": max_hypotheses,
        "validate": validate,
        "per_run_cap_usd": per_run_cap,
        "total_cap_usd": total_max_usd,
        "target": "examples/payments-lite",
    }


def _resume_compatible(doc: dict[str, Any], *, mode: str, settings: dict[str, Any]) -> str | None:
    """Return a refusal reason if ``doc`` cannot be resumed with the new invocation, else None."""
    if not doc.get("runs"):
        return None
    if doc.get("mode") != mode:
        return f"mode mismatch: file has {doc.get('mode')!r}, invocation wants {mode!r}"
    prior = doc.get("settings") or {}
    for key in ("max_hypotheses", "validate", "per_run_cap_usd", "target"):
        if key in prior and prior[key] != settings.get(key):
            return f"settings.{key} mismatch: file has {prior[key]!r}, invocation wants {settings.get(key)!r}"
    return None


WHAT = (
    "seven-agent assessment on examples/payments-lite: Claude-only vs hybrid (trained specialists + Claude) "
    "vs specialists-only; scored against planted_findings/negatives (answer key never shown to agents). "
    "Agents receive behavioral expectations from labels.yaml expectations: (also in target README)."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="specialists.live_comparison",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--mode", choices=("replay", "live"), default="replay")
    ap.add_argument("--reps", type=int, default=3, help="assessments per Claude configuration")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--max-hypotheses", type=int, default=3)
    ap.add_argument("--validate", type=int, default=2, help="hypotheses validated per assessment")
    ap.add_argument(
        "--total-max-usd",
        type=float,
        default=20.0,
        help="stop starting new runs once spend reaches this",
    )
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument(
        "--resume", action="store_true", help="keep the runs already in --out and add to them"
    )
    ap.add_argument("--i-have-authorization", action="store_true")
    args = ap.parse_args(argv)

    if args.mode == "live":
        if os.environ.get("RUN_LIVE_LLM_EVAL") != "1" or not args.i_have_authorization:
            print(
                "live mode needs RUN_LIVE_LLM_EVAL=1 and --i-have-authorization (it costs money)",
                file=sys.stderr,
            )
            return 2
    elif args.out == OUT:
        print(
            "replay runs are scripted, not evidence: pass --out somewhere other than the results file",
            file=sys.stderr,
        )
        return 2
    per_run_cap = float(os.environ.get("LIVE_SEVEN_MAX_USD", "4"))
    configs = [c for c in args.configs.split(",") if c]
    unknown = [c for c in configs if c not in CONFIGS]
    if unknown:
        print(f"unknown --configs {unknown!r}; choose from {list(CONFIGS)}", file=sys.stderr)
        return 2
    key = load_key()
    planted_ids = [p["id"] for p in key["planted"]]
    settings = _settings(
        max_hypotheses=args.max_hypotheses,
        validate=args.validate,
        per_run_cap=per_run_cap,
        total_max_usd=args.total_max_usd,
    )

    doc: dict[str, Any] = {}
    if args.resume and args.out.is_file():
        doc = json.loads(args.out.read_text(encoding="utf-8"))
        reason = _resume_compatible(doc, mode=args.mode, settings=settings)
        if reason:
            print(f"--resume refused: {reason}. Start a new --out file.", file=sys.stderr)
            return 2
    elif args.out.is_file() and not args.resume:
        # Avoid silently overwriting a prior experiment with incompatible pooling semantics.
        prior = json.loads(args.out.read_text(encoding="utf-8"))
        if prior.get("runs"):
            print(
                f"{args.out} already has runs; pass --resume (same settings) or a new --out",
                file=sys.stderr,
            )
            return 2
    runs: list[dict[str, Any]] = doc.get("runs", [])
    doc.update(
        {
            "what": WHAT,
            "mode": args.mode,
            "settings": settings,
            "answer_key": {
                "planted": key["planted"],
                "negatives": key["negatives"],
                "class_family": {k: sorted(v) for k, v in CLASS_FAMILY.items()},
                "expected_cause": EXPECTED_CAUSE,
            },
        }
    )
    doc["specialists_only"] = specialists_only()
    spent = sum(r["score"].get("usd", 0.0) for r in runs)
    # Counterbalance: ABAB… then flip each pair so latency/rate-limit drift does not always
    # hit the same configuration first (A B B A A B …).
    order: list[str] = []
    for i in range(args.reps):
        pair = list(configs)
        if i % 2:
            pair.reverse()
        order.extend(pair)
    done = {
        c: sum(1 for r in runs if r["config"] == c and not provider_failure(r["score"]))
        for c in configs
    }
    for config in order:
        if done[config] >= args.reps:
            continue
        if spent >= args.total_max_usd:
            print(f"total cap reached (${spent:.2f}); stopping", file=sys.stderr)
            break
        cap = min(per_run_cap, args.total_max_usd - spent)
        attempt = sum(1 for r in runs if r["config"] == config) + 1
        print(
            f"[{datetime.now(UTC):%H:%M:%S}] {config} run {done[config] + 1}/{args.reps} (cap ${cap:.2f}) ...",
            flush=True,
        )
        score, _, _ = run_one(
            config,
            mode=args.mode,
            max_usd=cap,
            max_hypotheses=args.max_hypotheses,
            validate=args.validate,
        )
        spent += float(score.get("usd") or 0.0)
        failure = provider_failure(score)
        if not failure:
            done[config] += 1
        runs.append(
            {
                "config": config,
                "attempt": attempt,
                "at": datetime.now(UTC).isoformat(),
                "score": score,
            }
        )
        if score.get("state") == "crashed":
            print(
                f"  crashed: {score.get('error_code')} usd={score.get('usd', 0):.4f} "
                f"(debited {score.get('usage_debited')}; total ${spent:.2f})",
                file=sys.stderr,
                flush=True,
            )
        else:
            found = {
                p: (
                    "F"
                    if v["fixed_verify_accepted"]
                    else "V"
                    if v["validated"]
                    else "H"
                    if v["hypothesised"]
                    else "-"
                )
                for p, v in (score.get("planted") or {}).items()
            }
            print(
                f"  {score['state']} planted={found} fp={score.get('false_positives_confirmed')} "
                f"tokens={score.get('tokens')} usd={score.get('usd', 0):.4f} "
                f"wall={score.get('wall_s', 0):.0f}s (total ${spent:.2f})",
                flush=True,
            )
        _finish(doc, runs, planted_ids, spent, args.out)
        if failure:
            print(f"  provider failure, excluded from the results: {failure}", flush=True)
            if int(score.get("tokens") or 0) == 0 and score.get("usage_debited") != "per_run_cap":
                print(
                    "provider is failing before the first turn; stopping (fix it, then --resume)",
                    file=sys.stderr,
                )
                break
    _finish(doc, runs, planted_ids, spent, args.out)
    print(
        json.dumps(
            {
                c: {
                    k: v
                    for k, v in s.items()
                    if k
                    in (
                        "runs",
                        "planted_fixed_per_assessment",
                        "false_positives_confirmed",
                        "usd",
                        "wall_s",
                    )
                }
                for c, s in doc["summary"].items()
            },
            indent=1,
        )
    )
    if args.mode == "live":
        missing = [
            c
            for c in configs
            if not any(r["config"] == c and not provider_failure(r["score"]) for r in runs)
        ]
        if missing:
            print(f"no usable live assessments for: {missing}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
