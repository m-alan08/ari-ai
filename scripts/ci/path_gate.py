#!/usr/bin/env python3
"""Decide whether a CI suite must run for the current diff.

Writes ``run=true|false`` to ``$GITHUB_OUTPUT`` and a visible line to
``$GITHUB_STEP_SUMMARY`` (and stdout). A suite that does not run still emits a
green check whose summary says SKIPPED — a skip is never silent.

Usage::

    python3 scripts/ci/path_gate.py --globs "backend/**,shared/**" \
        [--base <sha>] [--always]

``--base`` is the commit to diff against (PR base sha or push "before"). When
it is empty, missing, or unfetchable the gate answers ``run=true`` — the gate
fails toward running, never toward skipping.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys


def _changed_files(base: str) -> list[str] | None:
    """File paths changed between ``base`` and HEAD; None when unknown."""
    base = (base or "").strip()
    if not base or set(base) == {"0"}:
        return None
    have = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True
        ).returncode
        == 0
    )
    if not have:
        subprocess.run(["git", "fetch", "-q", "origin", base], capture_output=True)
        have = (
            subprocess.run(
                ["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True
            ).returncode
            == 0
        )
    if not have:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern):
        return True
    # GitHub's `**/*.md` also matches top-level `README.md`.
    if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--globs", required=True, help="Comma-separated path globs")
    ap.add_argument("--base", default="", help="Base commit sha to diff against")
    ap.add_argument("--always", action="store_true", help="Run unconditionally")
    args = ap.parse_args()

    patterns = [p.strip() for p in args.globs.split(",") if p.strip()]
    reason = ""
    run = args.always
    if not run and not patterns:
        run, reason = True, "no globs supplied"
    if not run:
        changed = _changed_files(args.base)
        if changed is None:
            run, reason = True, "base unknown (dispatch, tag, or unfetchable sha)"
        else:
            hits = [p for p in changed if any(_matches(p, g) for g in patterns)]
            run = bool(hits)
            reason = f"{len(hits)}/{len(changed)} changed files match"
            if hits:
                reason += f" (e.g. {hits[0]})"

    print(f"run={str(run).lower()}  # {reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"run={str(run).lower()}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        label = "RUN" if run else "SKIP"
        with open(summary, "a") as fh:
            fh.write(f"**{label}** — {reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
