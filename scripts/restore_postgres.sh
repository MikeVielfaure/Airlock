#!/usr/bin/env bash
# Restore a dump made by backup_postgres.sh. Destructive — overwrites
# whatever is currently in file_explorer — so it asks for the database name
# retyped as confirmation rather than a bare -y flag, the same pattern this
# project uses everywhere else a single action destroys data (revoking a
# confidentiality key, archiving a configuration).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: scripts/restore_postgres.sh <dump.sql.gz>" >&2
  exit 1
fi

USER="${POSTGRES_USER:-app}"

echo "This will OVERWRITE the current file_explorer database with $FILE."
read -r -p "Type the database name (file_explorer) to confirm: " CONFIRM
if [ "$CONFIRM" != "file_explorer" ]; then
  echo "Not confirmed — aborting." >&2
  exit 1
fi

echo "Restoring $FILE ..."
gunzip -c "$FILE" | docker compose exec -T db psql -U "$USER" -d file_explorer
echo "Done."
