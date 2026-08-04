#!/usr/bin/env bash
# Dump the Postgres database to a timestamped, gzipped SQL file, then delete
# dumps older than FX_BACKUP_KEEP_DAYS. Plain SQL rather than `pg_dump -Fc`:
# restorable with a plain `psql`, no `pg_restore` version to match against
# the one that made the dump.
#
# Install as a cron entry, e.g.:
#   0 3 * * * /path/to/airlock/scripts/backup_postgres.sh >> /var/log/airlock-backup.log 2>&1
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DIR="${FX_BACKUP_DIR:-./backups}"
KEEP_DAYS="${FX_BACKUP_KEEP_DAYS:-14}"
USER="${POSTGRES_USER:-app}"

mkdir -p "$DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$DIR/file_explorer_${STAMP}.sql.gz"

echo "Dumping file_explorer to $OUT ..."
docker compose exec -T db pg_dump -U "$USER" -d file_explorer | gzip > "$OUT"
echo "Done: $(du -h "$OUT" | cut -f1)"

echo "Removing dumps older than ${KEEP_DAYS} days from $DIR ..."
find "$DIR" -name 'file_explorer_*.sql.gz' -mtime "+${KEEP_DAYS}" -print -delete
