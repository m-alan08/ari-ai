"""Out-of-distribution check on the project's authorized planted-defect target (payments-lite).

payments-lite is hand-written, never used for training, and labelled in
``examples/payments-lite/labels.yaml``. This scores every route handler with the Scout model and
the platform's static.analyze patterns and reports where the planted findings rank, and whether
Probe's top strategy maps to the check category the labels expect.

    uv run python -m specialists.fixture_eval
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .runtime import Specialists
from .tasks import STATIC_ANALYZE_PATTERNS

ROOT = Path(__file__).resolve().parents[2] / "examples" / "payments-lite"
WSTG_TO_CATEGORY = {
    "WSTG-ATHZ-04": "access_control.ownership",
    "WSTG-BUSL-01": "input_validation.bounds",
}


def handlers(app: Path) -> list[dict]:
    text = app.read_text()
    lines = text.splitlines()
    out = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr in {"get", "post", "patch", "delete", "put"}
                and deco.args
            ):
                route = f"{deco.func.attr.upper()} {ast.literal_eval(deco.args[0])}"
                start = deco.lineno
                out.append(
                    {
                        "route": route,
                        "symbol": node.name,
                        "code": "\n".join(lines[start - 1 : node.end_lineno]),
                    }
                )
    return out


def main() -> int:
    sp = Specialists()
    rows = handlers(ROOT / "target" / "payments_lite" / "app.py")
    for r in rows:
        a = sp.advise("scout", {"code": r["code"]}, top_k=13)
        r["suspicion"] = round(1 - (a.score("safe") or 0.0), 4)
        r["static_analyze"] = next(
            (
                cwe
                for pat, cwe in STATIC_ANALYZE_PATTERNS
                for line in r["code"].splitlines()
                if re.search(pat, line)
            ),
            None,
        )
        del r["code"]
    rows.sort(key=lambda r: -r["suspicion"])
    planted = {
        "GET /payments/{payment_id}": (
            "CWE-639",
            "access_control.ownership",
            "A customer can only read payments they own.",
        ),
        "POST /refunds": (
            "CWE-1284",
            "input_validation.bounds",
            "Total refunds for a payment must never exceed the payment's original amount.",
        ),
    }
    result = {
        "target": "examples/payments-lite (authorized fixture, never trained on)",
        "handlers_ranked": rows,
        "planted": {},
    }
    for route, (cwe, category, statement) in planted.items():
        rank = next((i + 1 for i, r in enumerate(rows) if r["route"] == route), None)
        probe = sp.advise("probe", {"hypothesis": f"{route} may violate: {statement}", "cwe": cwe})
        result["planted"][route] = {
            "scout_rank": rank,
            "of": len(rows),
            "static_analyze_flagged": bool(
                next(r for r in rows if r["route"] == route)["static_analyze"]
            ),
            "probe_top": probe.top,
            "probe_maps_to_expected_category": WSTG_TO_CATEGORY.get(probe.top or "") == category,
        }
    out = Path(__file__).resolve().parent.parent / "results" / "fixture_payments_lite.json"
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["planted"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
