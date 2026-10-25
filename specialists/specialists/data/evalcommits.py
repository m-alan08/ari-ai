"""Labelled-commit eval cases as specialist training rows.

``security-agents/evals/commits/cases.json`` plants real vulnerabilities by replacing an
``old`` code string with a ``new`` one on the repo's actual files, then scores whether the
pipeline flags/validates/fixes them. Each case is therefore a minimal pair we can teach
with: the ``new`` text is a labelled positive (its CWE, or ``vuln_other`` outside Scout's
class space), and the ``old`` text is a hard negative -- the safe version of the exact same
code. Benign cases contribute only safe rows.

Rows are pinned to splits by case id so a case's positive and negative never separate.
The file lives in the repo, so this loader is deterministic and needs no download.
"""

from __future__ import annotations

import json
import os
import zlib
from pathlib import Path
from typing import Any

from .realdata import OUR_SCOUT_CWES, VULN_OTHER

CASES = Path(
    os.environ.get(
        "SPECIALISTS_EVAL_CASES",
        Path(__file__).parents[3] / "security-agents" / "evals" / "commits" / "cases.json",
    )
)


def _split(case_id: str) -> str:
    bucket = zlib.crc32(case_id.encode()) % 12
    if bucket < 2:
        return "test"
    if bucket < 4:
        return "val"
    return "train"


def scout_rows() -> list[dict[str, Any]]:
    """Rows in the ``scout()`` builder's shape: {family, framework, code, label, split-key}."""
    if not CASES.exists():
        return []
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    rows: list[dict[str, Any]] = []
    for case in cases:
        split = _split(case["id"])
        cwe = case.get("cwe")
        positive = cwe if cwe in OUR_SCOUT_CWES else (VULN_OTHER if cwe else None)
        for edit in case["edits"]:
            if case["kind"] == "benign":
                rows.append(
                    {
                        "family": f"eval_commit:{case['id']}",
                        "framework": "python",
                        "code": edit["new"],
                        "label": "safe",
                        "split": split,
                    }
                )
            else:
                rows.append(
                    {
                        "family": f"eval_commit:{case['id']}",
                        "framework": "python",
                        "code": edit["new"],
                        "label": positive,
                        "split": split,
                    }
                )
                rows.append(
                    {
                        "family": f"eval_commit:{case['id']}",
                        "framework": "python",
                        "code": edit["old"],
                        "label": "safe",
                        "split": split,
                    }
                )
    return rows
