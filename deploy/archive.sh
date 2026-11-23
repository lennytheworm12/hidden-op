#!/usr/bin/env bash
# Upload raw match JSON to Drive in verified chunks, then drop it from the local DB.
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE="${HIDDENOP_REMOTE:-$(grep -s '^HIDDENOP_REMOTE=' .env | cut -d= -f2-)}"
REMOTE="${REMOTE:-gdrive:hidden-op}"
exec .venv/bin/python scripts/archive_raw.py --remote "$REMOTE/raw"
