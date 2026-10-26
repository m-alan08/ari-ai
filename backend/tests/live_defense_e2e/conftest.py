"""Fixtures and machine-readable reporting for the Live Defense e2e suite."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from .harness import OwnedStaging
from .report import RESULTS_DIR, ScenarioReport, environment_identity

pytest.importorskip("security_agents")

_records: dict[str, dict] = {}
_start: dict[str, float] = {}


@pytest.fixture
def staging(client_factory, settings_override, monkeypatch, tmp_path) -> OwnedStaging:
    env = OwnedStaging(client_factory, settings_override, monkeypatch, tmp_path)
    env.use_fake_llm()
    return env


@pytest.fixture
def report(request) -> ScenarioReport:
    scenario = request.node.name.split("_")[1].upper() if "_" in request.node.name else "?"
    rep = ScenarioReport(scenario=scenario)
    request.node._scenario_report = rep  # noqa: SLF001  (pytest node stash)
    return rep


@pytest.fixture(autouse=True)
def _timer(request):
    _start[request.node.nodeid] = time.monotonic()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":
        return
    scenario_report: ScenarioReport | None = getattr(item, "_scenario_report", None)
    _records[item.nodeid] = {
        "scenario": scenario_report.scenario if scenario_report else "?",
        "nodeid": item.nodeid,
        "outcome": rep.outcome,
        "duration_s": round(time.monotonic() - _start.get(item.nodeid, 0), 3),
        "failure_stage": (
            None
            if rep.outcome == "passed"
            else getattr(rep.longrepr, "reprcrash", rep.longrepr).__str__()[:400]
        ),
        "checks": scenario_report.checks if scenario_report else [],
        "artifacts": scenario_report.artifacts if scenario_report else [],
        "notes": scenario_report.notes if scenario_report else [],
        "markers": [m.name for m in item.iter_markers()],
    }


def pytest_sessionfinish(session):
    if not _records:
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"results-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    doc = {
        "suite": "live-defense-e2e",
        "environment": environment_identity(),
        "scenarios": list(_records.values()),
        "provider_evidence_note": (
            "All scenarios run through scripted providers (FakeLLM) unless the "
            "test is marked live_llm. FakeLLM results do not validate real "
            "Anthropic execution."
        ),
    }
    path.write_text(json.dumps(doc, indent=2, default=str))
    latest = RESULTS_DIR / "latest.json"
    latest.write_text(json.dumps(doc, indent=2, default=str))


def pytest_configure(config):
    config.addinivalue_line("markers", "live_defense_e2e: Live Defense owned-staging e2e scenario")
    config.addinivalue_line(
        "markers", "live_defense_smoke: fast subset of the live-defense e2e suite"
    )
    config.addinivalue_line(
        "markers", "live_defense_fault: failure-injection live-defense scenario"
    )
