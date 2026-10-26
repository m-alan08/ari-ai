#!/usr/bin/env python3
"""Check that every internal Markdown link and #anchor in the repo resolves.

    python3 scripts/check_links.py            # exit 1 if anything is broken

Also does a light Mermaid sanity check (each ```mermaid block starts with a known
diagram type). Standard library only. Used by .github/workflows/docs.yml (INFRA-003).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "runs", ".worktrees", "worktrees"}
MERMAID_TYPES = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "classDiagram",
    "erDiagram",
    "gantt",
    "pie",
    "journey",
    "gitGraph",
    "mindmap",
    "timeline",
    "C4Context",
)
LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def slug(heading: str) -> str:
    """GitHub-style anchor for a heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def markdown_files() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.md") if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts)
    )


def anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        cache[path] = {slug(h) for h in HEADING.findall(FENCE.sub("", path.read_text()))}
    return cache[path]


def check() -> list[str]:
    errors: list[str] = []
    cache: dict[Path, set[str]] = {}
    for md in markdown_files():
        raw = md.read_text()
        for block in re.findall(r"```mermaid\n(.*?)```", raw, re.S):
            first = block.strip().split(None, 1)[0] if block.strip() else ""
            if first not in MERMAID_TYPES:
                errors.append(
                    f"{md.relative_to(ROOT)}: mermaid block starts with unknown type {first!r}"
                )
        text = INLINE_CODE.sub("", FENCE.sub("", raw))
        for target in LINK.findall(text):
            if re.match(r"^(https?:|mailto:)", target):
                continue
            path, _, frag = target.partition("#")
            dest = (md.parent / path).resolve() if path else md
            if not dest.exists():
                errors.append(f"{md.relative_to(ROOT)}: broken link -> {target}")
            elif (
                frag
                and dest.is_file()
                and dest.suffix == ".md"
                and frag not in anchors(dest, cache)
            ):
                errors.append(f"{md.relative_to(ROOT)}: missing anchor -> {target}")
    return errors


def main() -> int:
    errors = check()
    files = len(markdown_files())
    for e in errors:
        print(e)
    print(f"checked {files} Markdown files: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
