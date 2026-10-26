#!/usr/bin/env python3
"""Idempotently add project coordination links to existing root Claude/Codex instructions."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
marker = "<!-- HACKATL_SIX_AGENT_BUILD_PROTOCOL -->"
addition = (
    "\n\n"
    + marker
    + """
## Six-agent concurrent implementation protocol (current active phase)

Read `MASTER_BUILD_SPEC.md` before work, then `coordination/MESSAGES.md`, `coordination/CONTRACTS.md`, `coordination/OWNERSHIP.md`, `coordination/MILESTONES.md`, applicable `team-prompts/<PERSON>_*.md`, and new messages in `coordination/messages/`. The previous "Phase 0 only" directive is superseded by the user's explicit authorization to implement; other security, Git, test and architecture constraints remain in force. Every agent checks GitHub at session start, milestones, shared interface changes and handoff. Never overwrite another owner's work or claim unexecuted tests pass.
"""
)
for fn in ["AGENTS.md", "CLAUDE.md"]:
    path = root / fn
    if not path.exists():
        raise SystemExit(f"Missing required existing root instruction: {path}")
    data = path.read_text(encoding="utf-8")
    if marker in data:
        print(f"Already patched: {fn}")
    else:
        path.write_text(data.rstrip() + "\n" + addition, encoding="utf-8")
        print(f"Updated: {fn}")
print("Done; review `git diff` then commit to main only as the initial coordinated bootstrap.")
