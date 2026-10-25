"""Run immutable independent tests against candidate imports in a bounded container.

No candidate code, packaging script, conftest or dependency manifest executes on
this host or in the model/credential job. Only trusted code builds the image.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ari_review.cycle import git, merge_evidence, write


class Docker:
    def call(self, *args, check=True, **kwargs):
        return subprocess.run(["docker", *map(str, args)], check=check, text=True, **kwargs)


def test_command(
    network: str, candidate: Path, suite: Path, evidence: Path, config: Path, tests: list[str]
) -> list[str]:
    return [
        "run",
        "--rm",
        "--name",
        network + "-tests",
        "--network",
        network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=4",
        "--memory=8g",
        "--pids-limit=512",
        "--user=10001:10001",
        "--tmpfs",
        "/tmp:rw,nosuid,size=2g,uid=10001,gid=10001",
        "--mount",
        f"type=bind,src={candidate.resolve()},dst=/work,readonly",
        "--mount",
        f"type=bind,src={suite.resolve()}/backend/tests,dst=/work/backend/tests,readonly",
        "--mount",
        f"type=bind,src={suite.resolve()}/security-agents/tests,dst=/work/security-agents/tests,readonly",
        "--mount",
        f"type=bind,src={evidence.resolve()},dst=/suite/backend/test-results",
        "-e",
        "GIT_CONFIG_COUNT=1",
        "-e",
        "GIT_CONFIG_KEY_0=safe.directory",
        "-e",
        "GIT_CONFIG_VALUE_0=/work",
        "--mount",
        f"type=bind,src={candidate.resolve()},dst=/src,readonly",
        "--mount",
        f"type=bind,src={suite.resolve()},dst=/suite,readonly",
        "--mount",
        f"type=bind,src={evidence.resolve()},dst=/evidence",
        "--mount",
        f"type=bind,src={config.resolve()},dst=/config,readonly",
        "-e",
        "TEST_DATABASE_URL=postgresql+psycopg://ci:ci-only@pg/postgres",
        "-e",
        "RUN_LIVE_LLM_EVAL=0",
        "-e",
        "ANTHROPIC_API_KEY=",
        "-e",
        "HACKATL_LIVE_ANTHROPIC_API_KEY=",
        "-e",
        "OPENAI_API_KEY=",
        "-e",
        "PYTHONPATH=/work/backend:/work/shared:/work/standards:/work/monitoring:/work/security-agents:/work/code-intelligence:/work/code-review:/work/specialists:/suite/backend/tests:/suite/security-agents",
        "ari-review-tests",
        "sh",
        "-ec",
        "cd /work; "
        '/app/.venv/bin/python -c "import backend, security_agents; '
        "assert backend.__file__.startswith('/work/'); "
        "assert security_agents.__file__.startswith('/work/')\"; "
        "exec /app/.venv/bin/python -m pytest -c /config/pytest.ini "
        '--override-ini cache_dir=/tmp/pytest-cache --junitxml=/evidence/pytest.xml -q "$@"',
        "sh",
        *tests,
    ]


def read_untrusted_xml(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
            raise ValueError("invalid untrusted result file")
        return os.read(fd, 2_000_001)
    finally:
        os.close(fd)


def receipt(xml: Path, code: int, pin: dict) -> dict:
    result = {
        "candidate_sha": pin["candidate_sha"],
        "upstream_sha": pin["upstream_sha"],
        "regression_sha": pin["regression_sha"],
        "exit_code": code,
        "tests": 0,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
        "failures": [],
    }
    raw = read_untrusted_xml(xml)
    if raw is not None:
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ValueError("untrusted XML entity declaration")
        tree = ET.fromstring(raw)
        for node in tree.iter("testsuite"):
            for source, target in [
                ("tests", "tests"),
                ("skipped", "skipped"),
                ("failures", "failed"),
                ("errors", "errors"),
            ]:
                result[target] += int(node.attrib.get(source, 0))
        result["failures"] = [
            n.attrib.get("classname", "") + "::" + n.attrib.get("name", "")
            for n in tree.iter("testcase")
            if n.find("failure") is not None or n.find("error") is not None
        ][:50]
    if result["tests"] < 20 or result["skipped"] or result["failed"] or result["errors"]:
        result["exit_code"] = code or 1
    return result


def scrub(text: str) -> str:
    for pattern in (
        r"sk-ant-[A-Za-z0-9_-]{8,}",
        r"xox[abprs]-[A-Za-z0-9-]{6,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"sk_live_[A-Za-z0-9]{8,}",
        r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}",
    ):
        text = re.sub(pattern, "[REDACTED_SYNTHETIC_TEST_SECRET]", text)
    return text


def run(candidate: Path, directory: Path, policy: dict) -> int:
    pin = json.loads((directory / "pin.json").read_text())
    if git("rev-parse", "HEAD", cwd=candidate).stdout.strip() != pin["candidate_sha"]:
        raise ValueError("candidate worktree does not match pinned SHA")
    if git("status", "--porcelain", "--untracked-files=no", cwd=candidate).stdout.strip():
        raise ValueError("candidate tracked files are dirty before execution")
    canonical = "https://github.com/" + policy["canonical_repository"] + ".git"
    integration = "https://github.com/" + policy["integration_repository"] + ".git"
    # Fetch only trusted pinned Git objects, never branch names supplied by a PR.
    for ref in [pin["upstream_sha"], pin["regression_sha"], *pin["pr_heads"].values()]:
        git("fetch", "--no-tags", canonical, ref, cwd=candidate)
    for ref in pin["transfers"].values():
        git("fetch", "--no-tags", integration, ref, cwd=candidate)
    write(directory / "merge.json", merge_evidence(candidate, pin))
    suite = (directory.parent / "independent-suite").resolve()
    git("worktree", "add", "--detach", str(suite), pin["regression_sha"], cwd=candidate)
    # Only SHA/path inputs change. Keep the original negative assertions intact.
    replacements = {
        "eea084bc87ac6ca94f578073251c478655b09366": pin["upstream_sha"],
        "ae414985d09796cc950b7e833157861d239abc29": pin["transfers"]["reliability"],
        "cfa06bf649c5ca608c56badc497f0ad69ce444f0": pin["transfers"]["hardening"],
        "6e6733a69170ece2343f9a1eb3e06881d89e0ebe": pin["transfers"]["observability"],
        "db8b1fd718522d8b7b2ec8daaf51f93881e4142b": pin["transfers"]["onboarding"],
        "8396e836866e33ebc34a402a7c8bd0eb6bc769d8": pin["pr_heads"]["458"],
    }
    for name in ["test_rc2_transfer_manifest.py", "test_rc2_upstream_migrations.py"]:
        file = suite / "backend/tests" / name
        source = file.read_text()
        for old, new in replacements.items():
            source = source.replace(old, new)
        file.write_text(source)
    for source, target in [
        ("backend/tests/test_auto_commits.py", "backend/tests/test_ari_latest_quota.py"),
        (
            "security-agents/tests/test_e2e_replay.py",
            "security-agents/tests/test_ari_latest_protected.py",
        ),
    ]:
        (suite / target).write_text(
            git("show", pin["upstream_sha"] + ":" + source, cwd=candidate).stdout
        )
    tests = ["/suite/" + name for name in policy["regressions"]]
    tests += [
        "/suite/backend/tests/test_ari_latest_quota.py::test_over_limit_hourly_assessments_report_actual_reset",
        "/suite/backend/tests/test_ari_latest_quota.py::test_over_limit_daily_assessments_report_actual_reset",
        "/suite/security-agents/tests/test_ari_latest_protected.py::test_verify_rejects_a_planted_bad_patch_that_edits_a_protected_test",
    ]
    # Candidate-writable results are isolated from trusted pin/admission/receipt.
    raw_directory = Path(tempfile.mkdtemp(prefix="ari-untrusted-results-"))
    raw_directory.chmod(0o777)
    dependency_source = (directory.parent / "trusted-dependency-source").resolve()
    git("worktree", "add", "--detach", str(dependency_source), pin["upstream_sha"], cwd=candidate)
    # Fixed fixture mounts cannot be replaced by candidate helpers. Only results
    # are writable; all production bytes remain the checked-out immutable SHA.
    (suite / "backend/test-results").mkdir(exist_ok=True)
    for rel in ("backend/tests", "security-agents/tests"):
        (candidate / rel).mkdir(parents=True, exist_ok=True)
    for root in (candidate, suite):
        root.chmod(root.stat().st_mode | 0o555)
        for parent, dirs, files in os.walk(root):
            for name in dirs:
                path = Path(parent) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o555)
            for name in files:
                path = Path(parent) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o444)
    docker = Docker()
    network = "ari-audit-" + os.environ.get("GITHUB_RUN_ID", str(os.getpid()))
    code = 1
    try:
        docker.call(
            "build",
            "-f",
            str(Path(".github/ari-review/Dockerfile").resolve()),
            "-t",
            "ari-review-tests",
            str(dependency_source),
            timeout=900,
        )
        docker.call("network", "create", "--internal", network)
        docker.call(
            "run",
            "-d",
            "--name",
            network + "-pg",
            "--network",
            network,
            "--network-alias",
            "pg",
            "--user=postgres",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=1g",
            "--cpus=2",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,uid=999,gid=999",
            "-e",
            "POSTGRES_USER=ci",
            "-e",
            "POSTGRES_PASSWORD=ci-only",
            "-e",
            "POSTGRES_DB=postgres",
            "postgres:16.11-bookworm",
        )
        # Readiness is bounded and checked, not assumed after a fixed sleep.
        ready = docker.call(
            "exec",
            network + "-pg",
            "sh",
            "-ec",
            "for i in $(seq 1 30); do pg_isready -U ci && exit 0; sleep 1; done; exit 1",
            check=False,
        )
        if ready.returncode:
            raise RuntimeError("test PostgreSQL failed readiness")
        log = directory / "pytest.raw.log"
        with log.open("w") as handle:
            result = docker.call(
                *test_command(
                    network, candidate, suite, raw_directory, Path(".github/ari-review"), tests
                ),
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=1500,
                check=False,
            )
            code = result.returncode
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        (directory / "execution-error.txt").write_text(
            type(error).__name__ + ": isolated test execution failed"
        )
    finally:
        docker.call(
            "rm", "-f", network + "-tests", network + "-pg", check=False, capture_output=True
        )
        docker.call("network", "rm", network, check=False, capture_output=True)
    log = directory / "pytest.raw.log"
    if log.exists():
        (directory / "pytest.log").write_text(scrub(log.read_text(errors="replace")[:2_000_000]))
        log.unlink()
    try:
        data = receipt(raw_directory / "pytest.xml", code, pin)
        raw_xml = read_untrusted_xml(raw_directory / "pytest.xml")
        if raw_xml is not None:
            (directory / "pytest.xml").write_text(scrub(raw_xml.decode(errors="replace")))
    except (OSError, ValueError, ET.ParseError):
        data = receipt(directory / ".no-container-results", 1, pin)
        data["integrity_error"] = "missing, malformed or unsafe container result"
    finally:
        try:
            shutil.rmtree(raw_directory)
        except OSError:
            data["exit_code"] = 1
            data["cleanup_error"] = "untrusted result cleanup failed on disposable runner"
    write(directory / "receipt.json", data)
    # Raw XML can contain synthetic credential parameter IDs. Store only scrubbed text.
    xml = directory / "pytest.xml"
    if xml.exists():
        xml.write_text(scrub(xml.read_text(errors="replace")))
    return data["exit_code"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, default=Path("candidate"))
    parser.add_argument("--directory", type=Path, default=Path("review-evidence"))
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.candidate.resolve(),
            args.directory.resolve(),
            json.loads(Path(".github/ari-review/policy.json").read_text()),
        )
    )
