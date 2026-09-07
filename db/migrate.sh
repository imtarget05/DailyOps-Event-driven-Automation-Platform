#!/bin/sh
# db/migrate.sh — apply schema.sql + all migrations in order, idempotent.
# Used by the docker-compose `db-init` service (fresh + existing DBs) and
# mirrors the `make migrate` target for manual/host runs.
#   usage: ./db/migrate.sh <psql-args...>    e.g. -U dailyops -d dailyops
set -e

PSQL="${PSQL:-psql}"
DB_ARGS="$@"

echo "[db-init] applying db/schema.sql (fresh-DB init; no-op where tables exist)..."
$PSQL $DB_ARGS -v ON_ERROR_STOP=1 < /db/schema.sql

for f in /db/migrations/*.sql; do
  echo "[db-init] applying $(basename "$f")..."
  $PSQL $DB_ARGS -v ON_ERROR_STOP=1 < "$f"
done

echo "[db-init] database migrations OK"
