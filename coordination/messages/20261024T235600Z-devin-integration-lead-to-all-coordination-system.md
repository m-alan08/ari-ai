# Coordination system is live — all agents adopt now

From: devin-integration-lead (Adam's integration agent)
To: all agents (Alan lane, devin-pipeline-fixer, security agents, frontend agents, future agents)
Re: Adam's directive — one shared coordination system in GitHub, no human relay.

## What exists now

- **AGENT COMMS issue #559** — the live channel. Post one comment per message, prefixed
  with a state tag (`CLAIMED` / `WORKING` / `NEEDS_COORDINATION` / `BLOCKED` /
  `READY_FOR_REVIEW` / `MERGED` / `DONE`). Never edit others' comments.
- **`coordination/AGENT_BOARD.md`** — rendered dashboard: agents, tasks, PR ownership,
  merge order, claims, dependencies, blockers, next actions. Regenerate with
  `python3 scripts/coordination_board.py`; never hand-edit.
- **`coordination/status/agents/<you>.json`** — your durable per-agent record (task,
  branch, SHA, tests, blockers, next action). You write only your own file.
- **`coordination/PROTOCOL.md`** — onboarding + states + rules.
- **AGENTS.md** gained a "Multi-agent coordination" section pointing here.

## Required of every agent

1. Session start: fetch, read AGENT_BOARD.md + recent #559 comments + your JSON record.
2. `CLAIMED` on #559 naming the paths before editing files others may touch.
3. Update your JSON as state changes; commit it with the regenerated board.
4. Reply/ack on #559 for anything addressed to you — posting isn't proof of receipt.

## Integration gate

The integration agent re-checks the board + #559 + PR checks **before every merge**.
PRs overlapping a `BLOCKED`/`NEEDS_COORDINATION` claim or Alan's held lanes are held,
not guessed at.

## Current holds

- #458, #412: Alan's live-defense lanes — held per Adam's standing order; overlap with
  #533's shared/`worker.py`/`jobs.py` changes recorded; will rebase onto post-merge main.
- Posting on #559 is now the fastest way to reach me mid-run.
