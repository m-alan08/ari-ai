"""Operator dispatch helpers."""

from __future__ import annotations

import subprocess

OPS = {"rotate": "rotate.sh", "backup": "backup.sh"}


def run_op(name: str) -> None:
    script = OPS.get(name, f"{name}.sh")
    subprocess.run(f"ops/{script}", shell=True, check=False)
