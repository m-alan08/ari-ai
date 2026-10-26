"""Machine-readable scenario reporting for the Live Defense e2e suite.

Every test receives a `report` fixture and records structured checks; the
conftest writes one JSON document per pytest run to
``test-results/live-defense-e2e/results-<ts>.json`` with, per scenario:
scenario id, nodeid, outcome, duration, failure stage, expected-vs-observed
checks, artifact references, and test-environment identity (git sha, schema +
orchestrator versions, provider mode, host, python). These files never claim
live-provider coverage: `provider_mode` is written into the environment block.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parents[3] / "test-results" / "live-defense-e2e"


def environment_identity() -> dict[str, Any]:
    try:
        git_sha = subprocess.check_output(
            ["git", "-C", str(RESULTS_DIR.parents[1]), "rev-parse", "HEAD"], text=True
        ).strip()
        git_branch = subprocess.check_output(
            ["git", "-C", str(RESULTS_DIR.parents[1]), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        git_sha = git_branch = "unknown"
    versions: dict[str, Any] = {}
    try:
        from shared import SCHEMA_VERSION
        from shared.live_defense import ORCHESTRATOR_VERSION

        versions = {"SCHEMA_VERSION": SCHEMA_VERSION, "ORCHESTRATOR_VERSION": ORCHESTRATOR_VERSION}
    except Exception:
        pass
    return {
        "git_sha": git_sha,
        "git_branch": git_branch,
        "schema_version": versions.get("SCHEMA_VERSION"),
        "orchestrator_version": versions.get("ORCHESTRATOR_VERSION"),
        "provider_mode": os.environ.get("LIVE_DEFENSE_E2E_PROVIDER", "fake"),
        "python": platform.python_version(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "captured_at": datetime.now(UTC).isoformat(),
    }


@dataclass
class ScenarioReport:
    """Structured per-test record; assertions also land here as checks."""

    scenario: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, name: str, expected: Any, observed: Any, ok: bool | None = None) -> None:
        self.checks.append(
            {
                "name": name,
                "expected": expected,
                "observed": observed,
                "ok": (expected == observed) if ok is None else bool(ok),
            }
        )

    def artifact(self, artifact_id: str, kind: str, detail: str = "") -> None:
        self.artifacts.append({"id": artifact_id, "kind": kind, "detail": detail})

    def note(self, text: str) -> None:
        self.notes.append(text)
