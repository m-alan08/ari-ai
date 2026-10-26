#!/usr/bin/env bash
# Staging smoke test over the REAL HTTP surface — no mocks, no test client.
#
#   deploy/scripts/smoke_staging.sh https://staging-api.example.com
#   OPS_TOKEN=... deploy/scripts/smoke_staging.sh http://localhost:8000
#
# Every check is a real request; a failure prints what was expected and exits 1.
set -uo pipefail

BASE="${1:?usage: smoke_staging.sh <api-base-url>}"
BASE="${BASE%/}"
PASS=0; FAIL=0

check() { # name expected actual
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "PASS $1";
  else FAIL=$((FAIL+1)); echo "FAIL $1  expected=$2 actual=$3"; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
body() { curl -s "$@"; }

# --- Liveness / readiness ---
check "healthz 200" 200 "$(code "$BASE/healthz")"
READY="$(body "$BASE/readyz")"
READY_CODE="$(code "$BASE/readyz")"
check "readyz http status" 200 "$READY_CODE"
check "readyz reports ready" 'ready' "$(echo "$READY" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status","?"))' 2>/dev/null || echo parse-error)"

# --- Security headers ---
HEADERS="$(curl -s -D- -o /dev/null "$BASE/healthz")"
check "x-request-id present" yes "$(echo "$HEADERS" | grep -qi '^x-request-id:' && echo yes || echo no)"
check "nosniff present" yes "$(echo "$HEADERS" | grep -qi '^x-content-type-options: *nosniff' && echo yes || echo no)"

# --- Authentication / authorization ---
check "projects unauth -> 401" 401 "$(code "$BASE/api/v1/projects")"
# FastAPI validates the body before auth on some routes: any 4xx is a rejection.
POST_CODE="$(code -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/projects")"
case "$POST_CODE" in 4*) PASS=$((PASS+1)); echo "PASS unauth POST rejected ($POST_CODE)";;
  *) FAIL=$((FAIL+1)); echo "FAIL unauth POST rejected ($POST_CODE)";; esac
check "webhook without signature rejected" 401 "$(code -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/webhooks/github")"
# Dev login disabled = 403 (route exists, forbidden); stripped build = 404.
DEV_CODE="$(code -X POST -H 'Content-Type: application/json' -d '{"github_login":"smoke"}' "$BASE/api/v1/auth/dev/login")"
case "$DEV_CODE" in 403|404) PASS=$((PASS+1)); echo "PASS dev login unavailable ($DEV_CODE)";;
  *) FAIL=$((FAIL+1)); echo "FAIL dev login reachable ($DEV_CODE)";; esac

# --- CORS: only the configured origin ---
CORS="$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: GET' "$BASE/api/v1/projects")"
check "foreign origin preflight rejected" 400 "$CORS"

# --- Ops surface ---
OPS_CODE="$(code "$BASE/api/v1/ops/status")"
if [ -n "${OPS_TOKEN:-}" ]; then
  check "ops status without token -> 403" 403 "$OPS_CODE"
  OPS="$(body -H "X-Ops-Token: $OPS_TOKEN" "$BASE/api/v1/ops/status")"
  check "ops status 200" 200 "$(code -H "X-Ops-Token: $OPS_TOKEN" "$BASE/api/v1/ops/status")"
  check "ops status exposes queue" yes "$(echo "$OPS" | grep -q '"queue"' && echo yes || echo no)"
  check "ops migrations at head" 'true' "$(echo "$OPS" | python3 -c 'import json,sys;print(str(json.load(sys.stdin)["migrations"]["at_head"]).lower())' 2>/dev/null || echo parse-error)"
else
  # Unset OPS_TOKEN must leave the surface closed (403 in dev, 404 in staging/prod).
  if [ "$OPS_CODE" = "403" ] || [ "$OPS_CODE" = "404" ]; then
    PASS=$((PASS+1)); echo "PASS ops status closed without token ($OPS_CODE)"
  else FAIL=$((FAIL+1)); echo "FAIL ops status open without token ($OPS_CODE)"; fi
fi

# --- OpenAPI: served (dev) or dev-gated to 404 (pilot hardening) — both are valid;
# anything else means the app didn't boot fully. ---
OC="$(code "$BASE/api/openapi.json")"
if [ "$OC" = "200" ] || [ "$OC" = "404" ]; then
  PASS=$((PASS+1)); echo "PASS openapi.json served-or-gated ($OC)"
else FAIL=$((FAIL+1)); echo "FAIL openapi.json served-or-gated expected=200-or-404 actual=$OC"; fi

echo "---"
echo "smoke: $PASS passed, $FAIL failed ($BASE)"
[ "$FAIL" -eq 0 ]
