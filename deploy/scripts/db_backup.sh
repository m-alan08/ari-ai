#!/usr/bin/env bash
# Backup the control-plane database to a compressed pg_dump file.
#
#   DATABASE_URL=postgresql+psycopg://app:secret@db:5432/hackatl \
#     deploy/scripts/db_backup.sh [output-dir]
#
# Defaults to ./backups. Prints the dump path, size and sha256 — keep those three
# lines in the deploy log; they are what the restore runbook asks for.
#
# Requires a pg_dump client whose major version matches the server
# (macOS: `brew install libpq`; the backend image already ships one).
set -euo pipefail

OUT_DIR="${1:-backups}"
URL="${DATABASE_URL:?set DATABASE_URL (postgresql[+psycopg]://user:pass@host:port/db)}"
# SQLAlchemy URLs carry a driver suffix plain postgres clients do not understand.
URL="${URL/postgresql+psycopg:/postgresql:}"

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/hackatl-$STAMP.dump"

pg_dump --format=custom --compress=6 --no-owner --no-privileges \
  --file="$FILE" "$URL"

SIZE="$(du -h "$FILE" | cut -f1)"
if command -v shasum >/dev/null 2>&1; then SHA="$(shasum -a 256 "$FILE" | cut -d' ' -f1)";
else SHA="$(sha256sum "$FILE" | cut -d' ' -f1)"; fi

echo "backup: $FILE"
echo "size:   $SIZE"
echo "sha256: $SHA"
echo "verify: pg_restore --list '$FILE' | head"
