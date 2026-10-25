"""Fetch comparison objects in the read-only audit job; never execute them."""

import json
from pathlib import Path

from scripts.ari_review.cycle import git

if __name__ == "__main__":
    pin = json.loads(Path("review-evidence/pin.json").read_text())
    git(
        "fetch",
        "--no-tags",
        "https://github.com/adamissac/ari-ai.git",
        pin["upstream_sha"],
        cwd="candidate",
    )
