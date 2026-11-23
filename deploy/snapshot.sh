#!/usr/bin/env bash
# Upload a slim, consistent copy of the DB (no raw JSON) to Drive as hiddenop-slim.db.
# Drive keeps old versions of the file for 30 days.
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE="${HIDDENOP_REMOTE:-$(grep -s '^HIDDENOP_REMOTE=' .env | cut -d= -f2-)}"
REMOTE="${REMOTE:-gdrive:hidden-op}"

# The snapshot is built next to the DB and needs about as much free space as the DB.
db_bytes=$(du -cb data/hiddenop.db* | tail -1 | cut -f1)
free_bytes=$(df -B1 --output=avail data | tail -1 | tr -d ' ')
if (( free_bytes < db_bytes * 12 / 10 )); then
  echo "Skipping snapshot: need ~$((db_bytes / 1000000)) MB free, have $((free_bytes / 1000000)) MB." >&2
  exit 0
fi

mkdir -p data/snapshots
.venv/bin/python scripts/snapshot_db.py --slim-only --out data/snapshots
rclone copyto data/snapshots/hiddenop-slim.db "$REMOTE/hiddenop-slim.db" --retries 5
rclone check data/snapshots "$REMOTE" --include hiddenop-slim.db --one-way
rm -f data/snapshots/hiddenop-slim.db
echo "Uploaded $REMOTE/hiddenop-slim.db"
