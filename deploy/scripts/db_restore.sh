#!/usr/bin/env bash
# Restore a pg_dump produced by db_backup.sh into DATABASE_URL.
#
#   RESTORE_CONFIRM=I-KNOW-THIS-OVERWRITES-DATA \
#   DATABASE_URL=postgresql+psycopg://app:secret@db:5432/hackatl \
#     deploy/scripts/db_restore.sh backups/hackatl-YYYYMMDDTHHMMSSZ.dump
#
# The restore overwrites existing objects in the target database (--clean
# --if-exists). It refuses to run without RESTORE_CONFIRM and refuses
# production-looking URLs unless RESTORE_ALLOW_PRODUCTION=yes is also set, so a
# staging drill cannot accidentally wipe the pilot's database.
# Afterwards the script re-applies migrations so a dump taken before a deploy
# still lands on the current schema.
set -euo pipefail

DUMP="${1:?usage: db_restore.sh <dump-file>}"
URL="${DATABASE_URL:?set DATABASE_URL}"
URL="${URL/postgresql+psycopg:/postgresql:}"

[ "${RESTORE_CONFIRM:-}" = "I-KNOW-THIS-OVERWRITES-DATA" ] || {
  echo "refusing: set RESTORE_CONFIRM=I-KNOW-THIS-OVERWRITES-DATA" >&2; exit 2; }
case "${APP_ENV:-development}" in
  production) [ "${RESTORE_ALLOW_PRODUCTION:-}" = "yes" ] || {
    echo "refusing: APP_ENV=production needs RESTORE_ALLOW_PRODUCTION=yes" >&2; exit 2; } ;;
esac

[ -f "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 2; }
pg_restore --list "$DUMP" >/dev/null || { echo "not a pg_dump custom file: $DUMP" >&2; exit 2; }

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$URL" "$DUMP"

echo "restore complete; re-applying migrations to current code head"
if command -v hackatl-migrate >/dev/null 2>&1; then
  hackatl-migrate
elif command -v uv >/dev/null 2>&1; then
  uv run hackatl-migrate
else
  echo "WARN: no hackatl-migrate on PATH; run migrations before starting services" >&2
fi
