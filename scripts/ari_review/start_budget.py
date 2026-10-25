"""Start trusted root gate before codex-action irreversibly drops privileges."""

import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

if __name__ == "__main__":
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("paid job reruns are disabled")
    policy = json.loads(Path(".github/ari-review/policy.json").read_text())
    capability = secrets.token_urlsafe(32)
    temporary = Path(os.environ["RUNNER_TEMP"]) / "ari-budget-config.json"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(
            {"key": os.environ["REAL_OPENAI_KEY"], "capability": capability, "policy": policy},
            handle,
        )
    subprocess.run(["sudo", "install", "-d", "-m", "700", "/root/ari-budget"], check=True)
    subprocess.run(
        ["sudo", "install", "-m", "600", str(temporary), "/root/ari-budget/config.json"], check=True
    )
    temporary.unlink()
    subprocess.run(
        [
            "sudo",
            "install",
            "-m",
            "500",
            "scripts/ari_review/budget_proxy.py",
            "/root/ari-budget/proxy.py",
        ],
        check=True,
    )
    subprocess.Popen(
        [
            "sudo",
            "/usr/bin/python3",
            "/root/ari-budget/proxy.py",
            "--config",
            "/root/ari-budget/config.json",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", 8877), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("cost gate failed to start")
    for name in ("ari-codex-home", "ari-read-only-review"):
        (Path(os.environ["RUNNER_TEMP"]) / name).mkdir(mode=0o700)
    subprocess.run(
        ["git", "-C", str(Path(os.environ["RUNNER_TEMP"]) / "ari-read-only-review"), "init", "-q"],
        check=True,
    )
    print("::add-mask::" + capability)
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write("capability=" + capability + "\n")
