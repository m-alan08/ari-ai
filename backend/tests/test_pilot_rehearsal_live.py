"""LIVE pilot rehearsal: real Anthropic provider + Vercel sandbox driver against
the prepared ledgerlite target (planted access-control defect). Runs only when
explicitly requested:

    ANTHROPIC_API_KEY=... VERCEL_TOKEN=... REHEARSAL_LIVE=1 \
        uv run pytest backend/tests/test_pilot_rehearsal_live.py -s

Cost is bounded by the manual-assessment budget (400k tok / $10 / 1800s).
Every agent run is asserted provider_mode == "live" — nothing scripted is
claimed here.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REHEARSAL_LIVE"), reason="live rehearsal; set REHEARSAL_LIVE=1"
)

from helpers import setup_project  # noqa: E402

from backend import worker  # noqa: E402
from backend.checkouts import register_binding  # noqa: E402

TARGET = Path("/Users/devin/repos/pilot-rehearsal-target")


@pytest.fixture
def live(settings_override, tmp_path):
    assert TARGET.is_dir()
    sha = subprocess.check_output(["git", "-C", TARGET, "rev-parse", "HEAD"], text=True).strip()
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps([]))
    # conftest blanks ANTHROPIC_API_KEY for test determinism; restore the real one.
    settings_override(
        security_workdir=str(tmp_path / "work"),
        repository_bindings_file=str(bindings),
        security_sandbox="vercel",
        anthropic_api_key=os.environ.get("HACKATL_LIVE_ANTHROPIC_API_KEY", ""),
    )
    return {"sha": sha, "bindings": bindings}


def test_live_provider_assessment_two_orgs(client_factory, live) -> None:
    alice, bob, eve = (
        client_factory("live-alice"),
        client_factory("live-bob"),
        client_factory("live-eve"),
    )
    ids = setup_project(alice, bob)
    register_binding(live["bindings"], ids["repo"], TARGET)

    r = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": live["sha"]},
    )
    assert r.status_code == 202, r.text
    asm_id = r.json()["assessment"]["id"]

    worker.drain(kinds=["run_assessment"])
    detail = alice.get(f"/api/v1/assessments/{asm_id}").json()
    state = detail["assessment"]["state"]
    runs = detail["agent_runs"]
    assert runs, "no agent steps ran"
    assert all(x["provider_mode"] == "live" for x in runs), [
        (x["agent"], x["provider_mode"], x["status"]) for x in runs
    ]
    assert state in ("AWAITING_APPROVAL", "COMPLETED", "FAILED"), state
    print("LIVE-STATE", state, "USAGE", detail["assessment"]["usage"])
    print("LIVE-FINDINGS", json.dumps(detail["findings"], indent=1)[:3000])

    # eve still gets 404 on the live run's artifacts.
    if detail["findings"]:
        fid = detail["findings"][0]["id"]
        assert eve.get(f"/api/v1/findings/{fid}").status_code == 404
        for run in runs:
            aid = run.get("transcript_artifact_id")
            if aid:
                assert eve.get(f"/api/v1/artifacts/{aid}").status_code == 404
