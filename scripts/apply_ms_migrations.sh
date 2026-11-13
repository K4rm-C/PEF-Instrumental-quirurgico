#!/bin/sh
set -e
i=0
until psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "SELECT 1" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 40 ] || exit 1
  sleep 2
done
mkdir -p /tmp/m
set --
for f in /migrations/0*.sql; do
  n=$(basename "$f")
  case "$n" in
    001_*) continue ;;
    002_seed_catalogs.sql|003_seed_demo.sql) continue ;;
  esac
  sed -E '/^(BEGIN|COMMIT);\r?$/d' "$f" > "/tmp/m/$n"
  set -- "$@" -f "/tmp/m/$n"
done
echo "applying $(( $# / 2 )) migration files"
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -X -v ON_ERROR_STOP=1 --single-transaction \
  -c "SELECT pg_advisory_xact_lock(hashtext('pef:schema:migration'))" "$@"
echo DONE
