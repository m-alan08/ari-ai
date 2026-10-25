"""Host/container and workflow privilege boundaries, including artifact attacks."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.ari_review.ci import read_untrusted_xml, receipt
from scripts.ari_review.ci import test_command as container_command
from scripts.ari_review.common import fingerprint
from scripts.ari_review.devin import bounded_session


@pytest.mark.parametrize(
    "body",
    [
        '<!DOCTYPE x [<!ENTITY s SYSTEM "file:///etc/passwd">]><testsuites/>',
        '<!ENTITY x "expansion"><testsuites/>',
    ],
)
def test_junit_cannot_read_entities(tmp_path, body):
    file = tmp_path / "result.xml"
    file.write_text(body)
    with pytest.raises(ValueError):
        receipt(
            file,
            0,
            {"candidate_sha": "a" * 40, "upstream_sha": "b" * 40, "regression_sha": "c" * 40},
        )


def test_untrusted_result_symlink_cannot_read_host_secret(tmp_path):
    secret = tmp_path / "host-secret"
    secret.write_text("synthetic-never-read")
    raw = tmp_path / "untrusted"
    raw.mkdir()
    (raw / "pytest.xml").symlink_to(secret)
    with pytest.raises(OSError):
        read_untrusted_xml(raw / "pytest.xml")


def test_empty_or_skipped_results_do_not_pass(tmp_path):
    pin = {"candidate_sha": "a" * 40, "upstream_sha": "b" * 40, "regression_sha": "c" * 40}
    file = tmp_path / "result.xml"
    assert receipt(file, 0, pin)["exit_code"] != 0
    file.write_text('<testsuite tests="40" skipped="1" failures="0" errors="0"/>')
    assert receipt(file, 0, pin)["exit_code"] != 0


def test_candidate_mounts_do_not_include_keys_or_trusted_receipts(tmp_path):
    raw = tmp_path / "untrusted"
    command = container_command(
        "internal-net",
        tmp_path / "candidate",
        tmp_path / "suite",
        raw,
        tmp_path / "config",
        ["file"],
    )
    joined = " ".join(command)
    assert "--network internal-net" in joined
    assert "--cap-drop=ALL" in command and "--read-only" in command
    assert "GITHUB_TOKEN" not in joined and "DEVIN_API_KEY" not in joined
    assert "dst=/src,readonly" in joined and "dst=/suite,readonly" in joined
    assert f"src={raw},dst=/evidence" in joined
    assert "docker.sock" not in joined
    assert "dst=/work/backend/tests,readonly" in joined
    assert "dst=/work/security-agents/tests,readonly" in joined
    assert "cp -r /src" not in joined


def test_stable_known_finding_does_not_create_issue_per_wording():
    assert fingerprint({"id": "N7", "path": "a", "title": "one"}) == fingerprint(
        {"id": "N7", "path": "b", "title": "two"}
    )


def devin_data():
    now = datetime(2026, 10, 25, tzinfo=UTC)
    session = {
        "session_id": "devin-existing123",
        "org_id": "org-ari",
        "status": "suspended",
        "status_detail": "inactivity",
        "acus_consumed": 10,
    }
    config = {
        "org_id": "org-ari",
        "expires_at": "2026-11-01T00:00:00+00:00",
        "sessions": {
            "reliability": {
                "session_id": "devin-existing123",
                "hard_acu_limit_total": 12,
                "native_hard_cap_confirmed": True,
            }
        },
    }
    return session, config, now


def test_only_existing_idle_provider_capped_session_may_resume():
    session, config, now = devin_data()
    assert bounded_session("reliability", session, config, now) == "devin-existing123"


@pytest.mark.parametrize("change", ["busy", "budget", "unconfirmed", "expired", "foreign"])
def test_devin_resume_refuses_unbounded_busy_or_foreign_session(change):
    session, config, now = devin_data()
    if change == "busy":
        session["status"] = "running"
        session["status_detail"] = "working"
    if change == "budget":
        config["sessions"]["reliability"]["hard_acu_limit_total"] = 20
    if change == "unconfirmed":
        config["sessions"]["reliability"]["native_hard_cap_confirmed"] = False
    if change == "expired":
        config["expires_at"] = "2020-01-01T00:00:00+00:00"
    if change == "foreign":
        session["org_id"] = "org-other"
    with pytest.raises(ValueError):
        bounded_session("reliability", session, config, now)


def test_workflow_separates_execution_from_model_and_write_permissions():
    import yaml

    workflow = yaml.load(
        Path(".github/workflows/ari-candidate-review.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    tests = jobs["strict-regressions"]
    assert tests["permissions"] == {"contents": "read"}
    assert "secrets." not in json.dumps(tests)
    model = jobs["independent-codex"]
    assert model["permissions"].get("issues") != "write"
    last = model["steps"][-1]
    assert last["uses"].startswith("openai/codex-action@")
    assert last["with"]["openai-api-key"] == "${{ steps.budget.outputs.capability }}"
    assert last["with"]["safety-strategy"] == "drop-sudo"
    assert "shell_tool" in last["with"]["codex-args"]
    assert "pull_request_target" in workflow["on"] and "issue_comment" not in workflow["on"]
    refresh = yaml.load(
        Path(".github/workflows/ari-review-refresh.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert refresh["concurrency"]["group"] != workflow["concurrency"]["group"]
    assert "codex-action" not in json.dumps(refresh)
    assert "DEVIN_API_KEY" not in json.dumps(refresh["jobs"]["refresh"])
    assert "DEVIN_API_KEY" not in json.dumps(jobs["publish"])
    assert "github.run_attempt == 1" in model["if"]


def test_paid_guard_requires_all_workflow_and_self_ownership():
    import base64

    from scripts.ari_review.common import trusted_owners_guard

    policy = json.loads(Path(".github/ari-review/policy.json").read_text())

    class GuardAPI:
        def __init__(self, text):
            self.text = text

        def call(self, path):
            return {"content": base64.b64encode(self.text.encode()).decode()}

    text = "\n".join(
        p + " @m-alan08 @adamissac"
        for p in [
            "/.github/ari-review/",
            "/.github/workflows/",
            "/.github/CODEOWNERS",
            "/scripts/ari_review/",
        ]
    )
    assert trusted_owners_guard(GuardAPI(text), policy)
    assert not trusted_owners_guard(GuardAPI(text + "\n* @untrusted-bot"), policy)
    assert not trusted_owners_guard(
        GuardAPI(text.replace("/.github/workflows/", "/.github/workflows/ari-*.yml")), policy
    )
