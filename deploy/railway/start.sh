#!/bin/sh
# Railway runs the API and the worker in one service so they share one volume
# (the artifact store and repository checkouts are local paths). Migrate first,
# then run both; if either exits, exit too so Railway restarts the container.
set -eu

# Every pusher and author by default; set a comma-separated list of logins to narrow it.
export GITHUB_AUTO_COMMIT_AUTHORS="${GITHUB_AUTO_COMMIT_AUTHORS-*}"

# RL defense policy retrained from results/protocol-v1/candidate.yaml (seed 0); the worker
# refuses a checkpoint whose sha256 differs from this pin.
RL_DIR=/app/rl-defense
export RL_CHECKPOINT_PATH="${RL_CHECKPOINT_PATH-$RL_DIR/results/protocol-v1-retrain/checkpoints/ddqn_seed0.pt}"
export RL_CHECKPOINT_SHA256="${RL_CHECKPOINT_SHA256-666145e00d4ee3b9a1839179d9d9aca5a245c20fe7a173e2cb960e34e0393fe8}"
export RL_TOPOLOGY_PATH="${RL_TOPOLOGY_PATH-$RL_DIR/configs/topology/payments_lite.yaml}"

# Product model policy: Haiku 5.5 only, including every agent and fast path.
export ANTHROPIC_MODEL=claude-haiku-5-5
export ANTHROPIC_MODEL_FAST=claude-haiku-5-5
export AGENT_MODEL_OVERRIDES=""
export LLM_SERVER_FALLBACKS=false

# Fail fast on a misconfigured deploy (missing secrets, dev-only settings in
# production) before touching the database; the worker re-checks migrations itself.
hackatl-preflight --config --worker

hackatl-migrate

# Twelve workers (~80 MB each) so concurrent commits each get their own run at once; jobs are
# claimed with SKIP LOCKED, so they never run the same job twice.
pids=""
i=0
while [ "$i" -lt "${WORKER_PROCESSES:-12}" ]; do
  hackatl-worker &
  pids="$pids $!"
  i=$((i + 1))
done
workers="$pids"
hackatl-api &
api=$!
pids="$pids $api"

# GitHub never retries a failed webhook: ask it to redeliver what this service missed while
# it was down (a deploy swaps containers). Harmless when nothing was missed.
python -m backend.redeliver &

# A deploy sends TERM: workers finish the job in hand and exit (Railway keeps this container
# for RAILWAY_DEPLOYMENT_DRAINING_SECONDS), so in-flight assessments are not cut off. The API
# keeps accepting webhooks into the queue until they are done, then stops.
draining=""
trap 'draining=1; kill -TERM $workers 2>/dev/null || true' TERM INT

alive() {
  for p in $pids; do kill -0 "$p" 2>/dev/null || return 1; done
}
while [ -z "$draining" ] && alive; do
  sleep 2
done

if [ -n "$draining" ]; then
  for p in $workers; do wait "$p" || true; done
  kill -TERM "$api" 2>/dev/null || true
  wait "$api" || true
  exit 0
fi
echo "a process exited; stopping the service so Railway restarts it" >&2
kill $pids 2>/dev/null || true
wait || true
exit 1
