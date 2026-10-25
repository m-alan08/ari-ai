#!/usr/bin/env bash
# Build a temporary integration branch from open PR head branches, then validate.
#
#   deploy/scripts/integration_branch.sh 412 441 458 496
#   deploy/scripts/integration_branch.sh --push 412 441 458 496
#
# Creates integration/pilot-<date> from origin/main, merges each PR's head in the
# given order, and runs `make check` + the backend suite. Conflicting PRs are
# reported and skipped (the merge is aborted), never force-resolved. Nothing is
# merged to main; --push only publishes the integration branch for CI.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PUSH=0
[ "${1:-}" = "--push" ] && { PUSH=1; shift; }
[ $# -ge 1 ] || { echo "usage: integration_branch.sh [--push] PR#..." >&2; exit 2; }

BRANCH="integration/pilot-$(date -u +%Y%m%d)"
git fetch origin --prune
git checkout -B "$BRANCH" origin/main

MERGED=(); SKIPPED=()
for pr in "$@"; do
  git fetch origin "pull/$pr/head:pr-$pr"
  if git merge --no-edit "pr-$pr"; then
    MERGED+=("$pr")
  else
    git merge --abort
    SKIPPED+=("$pr")
    echo "CONFLICT: PR #$pr does not merge cleanly onto $BRANCH (skipped)" >&2
  fi
done

echo "---"
echo "merged:  ${MERGED[*]:-none}"
echo "skipped: ${SKIPPED[*]:-none}"

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  make check
  uv run pytest -q backend/tests -x
fi

if [ "$PUSH" = "1" ]; then
  git push -f origin "$BRANCH"
  echo "pushed $BRANCH — open CI on it; delete when the PRs have landed"
fi
