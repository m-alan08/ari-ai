"""Networkless synthetic container-layout smoke; never a release approval."""

import subprocess
import tempfile
from pathlib import Path

from scripts.ari_review.ci import test_command

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ari-layout-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        candidate, suite, raw = (root / name for name in ("candidate", "suite", "raw"))
        for path in (
            candidate / "backend/backend",
            candidate / "backend/tests",
            candidate / "security-agents/security_agents",
            candidate / "security-agents/tests",
            suite / "backend/tests",
            suite / "backend/test-results",
            suite / "security-agents/tests",
            raw,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (candidate / "backend/backend/__init__.py").write_text("")
        (candidate / "security-agents/security_agents/__init__.py").write_text("")
        (candidate / "backend/tests/helpers.py").write_text('MARKER="UNTRUSTED_CANDIDATE_HELPER"')
        (suite / "backend/tests/helpers.py").write_text('MARKER="INDEPENDENT_TRUSTED_HELPER"')
        (suite / "backend/tests/test_layout.py").write_text("""import os
from pathlib import Path
import pytest
import helpers

def test_fixed_fixture_and_read_only_source():
    assert helpers.MARKER == "INDEPENDENT_TRUSTED_HELPER"
    assert "GITHUB_TOKEN" not in os.environ and "GH_TOKEN" not in os.environ
    assert os.environ.get("OPENAI_API_KEY") == ""
    with pytest.raises(OSError):
        Path("/work/must-not-write").write_text("no")
    with pytest.raises(OSError):
        Path("/work/backend/tests/must-not-write").write_text("no")
""")
        raw.chmod(0o777)
        args = test_command(
            "none",
            candidate,
            suite,
            raw,
            Path(".github/ari-review"),
            ["/suite/backend/tests/test_layout.py"],
        )
        subprocess.run(["docker", *args], check=True, timeout=90)
        assert (raw / "pytest.xml").exists(), "real container did not write its result mount"
        print(
            "Synthetic container layout verified. No candidate security verdict or paid provider call."
        )
