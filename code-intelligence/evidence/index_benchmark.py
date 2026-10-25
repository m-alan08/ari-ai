"""Index a real checkout through IntelligenceService and measure coverage + cost.

Ground truth is an independent `ast` walk over the same files `GitReader`
selects, so retained-coverage numbers reflect what the index kept rather than
what the source tree contains. Route decorators counted in ground truth that
are missing from the index must be matched by an `IndexIssue` — silently
dropped routes are a finding, not a statistic.

Usage (run from the repo root, uv workspace venv):

    PYTHONPATH=code-intelligence:shared \\
        .venv/bin/python code-intelligence/evidence/index_benchmark.py \\
        <repo_path> <full_40_char_commit_sha> [path_prefix]

Writes the measured JSON to stdout.
"""

from __future__ import annotations

import ast
import json
import resource
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from code_intelligence.api import IntelligenceService, RepositoryBinding
from code_intelligence.store import SQLiteIndexStore

ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "api_route"}


def ground_truth(files) -> dict[str, int]:
    stats = {"py_files": 0, "defs": 0, "classes": 0, "imports": 0, "calls": 0, "route_decs": 0}
    for f in files:
        if f.language != "python":
            continue
        stats["py_files"] += 1
        try:
            tree = ast.parse(f.content)
        except (SyntaxError, ValueError, RecursionError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stats["defs"] += 1
                for d in node.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr.lower() in ROUTE_VERBS
                    ):
                        stats["route_decs"] += 1
            elif isinstance(node, ast.ClassDef):
                stats["classes"] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                stats["imports"] += len(node.names)
            elif isinstance(node, ast.Call):
                stats["calls"] += 1
    return stats


def main() -> None:
    path, sha = Path(sys.argv[1]), sys.argv[2]
    prefix = sys.argv[3] if len(sys.argv) > 3 else ""
    db = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("benchmark.sqlite")
    if db.exists():
        db.unlink()

    store = SQLiteIndexStore(db)
    binding = RepositoryBinding("prj_benchmark00", "rep_benchmark00", "owned/bench", path, prefix)
    service = IntelligenceService(store, [binding])
    tracemalloc.start()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    ref = service.index_repository("prj_benchmark00", "rep_benchmark00", sha)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    index = store.get(ref)
    gt = ground_truth(index.files)
    route_issues = sum(1 for i in index.issues if i.reason.startswith("Route decorator"))
    routes = index.snapshot.stats.entry_points
    if routes + route_issues != gt["route_decs"]:
        print(
            f"WARNING: {gt['route_decs'] - routes - route_issues} route decorators "
            "neither indexed nor flagged",
            file=sys.stderr,
        )
    out = {
        "target": str(path),
        "prefix": prefix,
        "sha": sha,
        "index_version": ref.index_version,
        "elapsed_s": round(elapsed, 2),
        "py_heap_peak_mb": round(peak / 1e6, 1),
        "rss_delta_mb": round((rss1 - rss0) / 1e6, 1),
        "store_bytes": db.stat().st_size,
        "stats": index.snapshot.stats.model_dump(mode="json"),
        "ground_truth": gt,
        "issues_by_reason": dict(Counter(i.reason for i in index.issues)),
        "retained": {
            "py_files": f"{gt['py_files']} parsed of {index.snapshot.stats.files} stored",
            "symbols_vs_defs+classes": (
                f"{index.snapshot.stats.symbols}/{gt['defs'] + gt['classes']}"
            ),
            "imports": f"{len(index.imports)}/{gt['imports']}",
            "call_edges_vs_raw_calls": f"{index.snapshot.stats.call_edges}/{gt['calls']}",
            "routes": f"{routes}/{gt['route_decs']} (+{route_issues} flagged)",
            "expectations": index.snapshot.stats.expectations,
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
