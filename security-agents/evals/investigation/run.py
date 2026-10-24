"""Runner for the investigation-quality eval.

For each case: materialize the workspace (corpus.py), pin it as a snapshot
(the same directory-digest mechanism cli.py uses), run the real Orchestrator
with real tools, the real check-runner and the real local sandbox, then write
one result JSON per (case, provider, repeat).

Provider modes:
  fake    — FakeLLM + fake_policy scripts (deterministic; validates the harness)
  live    — AnthropicProvider via live_gateway_from_env; requires an authorized
            key already in the environment. When unavailable the case result is
            labelled provider_status="unavailable", never fabricated.
  record  — live gateway wrapped in RecordingProvider (JSONL per case)
  replay  — ReplayProvider over a previous recording

Usage: .venv/bin/python -m evals.investigation.run --provider fake [--case ID] [--repeat N] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # corpus/fake_policy/score imports

import corpus

from security_agents.checks.library import default_library
from security_agents.cli import TARGET_ID, build_scope
from security_agents.contracts import (
    Budget,
    new_id,
)
from security_agents.intel.labels import load_expectations
from security_agents.intel.port import LocalIntel
from security_agents.llm.gateway import ProviderGateway, live_gateway_from_env
from security_agents.llm.providers import FakeLLM, RecordingProvider, ReplayProvider
from security_agents.runtime.loop import run_agent
from security_agents.sandbox.driver import LocalProcessDriver
from security_agents.sandbox.workspace import SnapshotHandle
from security_agents.workflow.orchestrator import Orchestrator, OrchestratorConfig

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"
EVAL_CONFIG = {
    "max_hypotheses": 3,
    "max_hypotheses_to_validate": 3,
    "max_tokens": 1_000_000,
    "max_usd": 5.0,
    "max_wall_clock_s": 1800.0,
    "remediation": "declined (approver returns None)",
    "specialists": "package default (advisory only)",
}


def _repo_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _gateway(provider: str, case_dir: Path, snapshot_root: Path):
    """Return (gateway, provider_status, detail)."""
    if provider == "fake":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import fake_policy

        return ProviderGateway(FakeLLM(fake_policy.all_scripts())), "fake", "deterministic scripted policy"
    if provider in ("live", "record"):
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return None, "unavailable", "no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN in environment"
        gw = live_gateway_from_env()
        if provider == "record":
            rec = case_dir / "provider_recording.jsonl"
            gw = ProviderGateway(RecordingProvider(gw.provider, rec))
        return gw, "live", "Anthropic provider via live_gateway_from_env"
    if provider == "replay":
        rec = case_dir / "provider_recording.jsonl"
        if not rec.is_file():
            return None, "unavailable", f"no recording at {rec}"
        return ProviderGateway(ReplayProvider(rec)), "replay", f"replaying {rec.name}"
    raise ValueError(f"unknown provider {provider}")


def run_case(
    case: corpus.Case, *, provider: str, out_dir: Path, work_root: Path, repeat_idx: int = 0
) -> dict[str, Any]:
    """Materialize one case and run the real orchestrator over it."""
    case_dir = out_dir / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    mat = corpus.materialize_case(case, work_root)

    snap_dest = case_dir / "snapshot"
    snapshot = SnapshotHandle.from_directory(
        mat.workspace, snap_dest, snapshot_id=new_id("snp"), repo_id="rep_eval"
    )
    expectations = load_expectations(
        mat.labels_path,
        snapshot_id=snapshot.snapshot_id,
        commit_sha=snapshot.commit_sha,
        strip_prefix=f"{mat.target_dir_name}/" if mat.target_dir_name else None,
    )
    intel = LocalIntel(snapshot, expectations=expectations)
    library = default_library()
    scope = build_scope(categories=sorted(library.categories()))

    gateway, provider_status, provider_detail = _gateway(provider, case_dir, snap_dest)

    captured: dict[str, list[dict[str, Any]]] = {"outputs": [], "runs_meta": []}

    def step_runner(spec, **kw):
        outcome = run_agent(spec, **kw)
        captured["outputs"].append(
            {
                "agent": spec.name.value,
                "step_key": kw["ctx"].step_key,
                "output": outcome.output.model_dump(mode="json") if outcome.output is not None else None,
                "ok": outcome.ok,
            }
        )
        return outcome

    if gateway is None:
        return {
            "case_id": case.id,
            "kind": case.kind,
            "provider": provider,
            "provider_status": provider_status,
            "provider_detail": provider_detail,
            "manifest": corpus.manifest_for(case, mat),
            "result": None,
        }

    config = OrchestratorConfig(
        assessment_id=new_id("asm"),
        project_id="prj_eval",
        org_id="org_eval",
        scope=scope,
        target_id=TARGET_ID,
        requested_by="usr_evaluator",
        budget=Budget(
            max_tokens=EVAL_CONFIG["max_tokens"],
            max_usd=EVAL_CONFIG["max_usd"],
            max_wall_clock_s=EVAL_CONFIG["max_wall_clock_s"],
        ),
        max_hypotheses=EVAL_CONFIG["max_hypotheses"],
        max_hypotheses_to_validate=EVAL_CONFIG["max_hypotheses_to_validate"],
    )
    orch = Orchestrator(
        config,
        intel=intel,
        gateway=gateway,
        driver=LocalProcessDriver(allow=True),
        library=library,
        workdir=case_dir / "work",
        approve_remediation=lambda finding: None,  # decline remediation
        step_runner=step_runner,
    )

    started = time.monotonic()
    error: dict[str, Any] | None = None
    try:
        result = orch.run()
    except Exception as exc:  # a runner bug must not kill the whole batch
        result = None
        error = {"type": type(exc).__name__, "message": str(exc)[:400]}
        traceback.print_exc()
    wall_s = time.monotonic() - started

    report = result.report() if result is not None else None
    # Persist every provider request for the leakage audit / debugging.
    inner = getattr(gateway.provider, "provider", gateway.provider)
    if hasattr(inner, "calls"):
        (case_dir / "llm_calls.json").write_text(
            json.dumps(
                [{"agent": c.agent.value, "system": c.system, "messages": c.messages} for c in inner.calls],
                default=str,
            )
        )
    # Agent-visible step outputs (pre-filter): Scout's raw output lets the
    # scorer separate "detected" from "dropped by the check-category gate".
    captured["runs_meta"] = [
        {
            "agent": r.agent.value,
            "status": r.status,
            "tool_calls": r.tool_calls,
            "llm_turns": r.llm_turns,
            "tokens": r.usage.input_tokens + r.usage.output_tokens,
            "usd": r.usage.usd,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "error_code": r.error_code.value if r.error_code else None,
        }
        for r in (result.runs if result else [])
    ]

    return {
        "case_id": case.id,
        "kind": case.kind,
        "provider": provider,
        "provider_status": provider_status,
        "provider_detail": provider_detail,
        "repeat_idx": repeat_idx,
        "manifest": corpus.manifest_for(case, mat),
        "assessed_snapshot_sha": snapshot.commit_sha,
        "snapshot_sha_matches_manifest": snapshot.commit_sha
        == (mat.commit_shas[-1] if mat.commit_shas else ""),
        "wall_clock_s": round(wall_s, 3),
        "error": error,
        "final_state": result.state.value if result else None,
        "error_code": result.error_code.value if result and result.error_code else None,
        "report": report,
        "step_outputs": captured["outputs"],
        "runs": captured["runs_meta"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", choices=("fake", "live", "record", "replay"), default="fake")
    ap.add_argument("--case", action="append", help="run only these case ids (repeatable)")
    ap.add_argument("--repeat", type=int, default=1, help="repeat each case N times")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    ap.add_argument("--keep-work", action="store_true", help="keep materialized workspaces")
    args = ap.parse_args(argv)

    cases = corpus.load_cases()
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.id in wanted]
        missing = wanted - {c.id for c in cases}
        if missing:
            print(f"unknown cases: {sorted(missing)}", file=sys.stderr)
            return 2

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = args.out / f"{run_id}_{args.provider}"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="eval-inv-"))

    batch_meta = {
        "eval": "investigation",
        "run_id": run_id,
        "provider": args.provider,
        "repeat": args.repeat,
        "repo_commit": _repo_sha(),
        "eval_config": EVAL_CONFIG,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "cases": len(cases),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(batch_meta, indent=2))

    results: list[dict[str, Any]] = []
    for case in cases:
        for i in range(args.repeat):
            t0 = time.monotonic()
            try:
                res = run_case(
                    case, provider=args.provider, out_dir=out_dir, work_root=work_root, repeat_idx=i
                )
            except Exception as exc:
                res = {
                    "case_id": case.id,
                    "provider": args.provider,
                    "repeat_idx": i,
                    "provider_status": "runner_error",
                    "error": str(exc)[:300],
                }
            res["batch_wall_s"] = round(time.monotonic() - t0, 3)
            results.append(res)
            status = res.get("provider_status", "?")
            state = res.get("final_state") or res.get("error_code") or "-"
            print(f"{case.id:28s} rep{i} {status:12s} state={state} ({res['batch_wall_s']}s)", flush=True)

    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    if not args.keep_work:
        import shutil

        shutil.rmtree(work_root, ignore_errors=True)
    print(f"\nwrote {out_dir}/results.json ({len(results)} case-runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
