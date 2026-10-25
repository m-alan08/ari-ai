#!/usr/bin/env bash
# verify_pair.sh — assert a deployed frontend/backend pair is contract-compatible.
#
#   deploy/verify_pair.sh <frontend-origin> [backend-origin]
#
#   deploy/verify_pair.sh https://ari-ai-lake.vercel.app
#   deploy/verify_pair.sh https://staging.vercel.app https://staging.up.railway.app
#
# Exit 0 = compatible (or explicitly skipped checks), 1 = mismatch/misconfigured,
# 2 = could not determine (backend or frontend unreachable). Prints JSON evidence.
set -euo pipefail

FRONTEND_ORIGIN="${1:?usage: verify_pair.sh <frontend-origin> [backend-origin]}"
BACKEND_ORIGIN="${2:-}"

fail() { echo "verify_pair: $*" >&2; exit 1; }

FRONT=$(curl -fsS --max-time 15 "$FRONTEND_ORIGIN/api/frontend-version") \
  || { echo "verify_pair: frontend-version unreachable"; exit 2; }
echo "$FRONT" | jq . >&2

FRONT_SCHEMA=$(echo "$FRONT" | jq -r '.frontend.schemaVersion')
FRONT_SHA=$(echo "$FRONT" | jq -r '.frontend.gitSha // "unset"')
STATUS=$(echo "$FRONT" | jq -r '.status')

# If the route couldn't reach the backend itself, try from here when given an origin.
BACK_SCHEMA=""; BACK_SHA=""
if [ -n "$BACKEND_ORIGIN" ]; then
  READY=$(curl -fsS --max-time 15 "$BACKEND_ORIGIN/readyz") || {
    echo "verify_pair: backend /readyz unreachable"; exit 2; }
  BACK_SCHEMA=$(echo "$READY" | jq -r '.schema_version // ""')
  BACK_SHA=$(echo "$READY" | jq -r '.git_sha // "unset"')
else
  STATUS_BACKEND=$(echo "$FRONT" | jq -r '.backend // empty')
  [ -n "$STATUS_BACKEND" ] || { echo "verify_pair: frontend could not reach backend"; exit 2; }
  BACK_SCHEMA=$(echo "$FRONT" | jq -r '.backend.schemaVersion // ""')
  BACK_SHA=$(echo "$FRONT" | jq -r '.backend.gitSha // "unset"')
fi

[ "$FRONT_SCHEMA" != "null" ] && [ -n "$FRONT_SCHEMA" ] || fail "frontend schema_version missing"
[ -n "$BACK_SCHEMA" ] || fail "backend schema_version missing (backend predates deploy identity)"
[ "$FRONT_SCHEMA" = "$BACK_SCHEMA" ] || \
  fail "SCHEMA MISMATCH frontend=$FRONT_SCHEMA backend=$BACK_SCHEMA — do not route pilot traffic"

echo "verify_pair: compatible schema=$FRONT_SCHEMA frontend_sha=$FRONT_SHA backend_sha=$BACK_SHA"
[ "$FRONT_SHA" = "$BACK_SHA" ] || \
  echo "verify_pair: note: commits differ (expected during staggered deploys; schema equality is the gate)"
