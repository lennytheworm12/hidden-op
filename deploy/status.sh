#!/usr/bin/env bash
# One-screen health check: services, disk, data collected, recent errors.
cd "$(dirname "$0")/.."
echo "== Services =="
systemctl --no-pager --plain list-units 'hiddenop-collector@*' --all | grep hiddenop || true
systemctl --no-pager list-timers 'hiddenop-*' | head -n 4
echo
echo "== Disk =="
df -h --output=size,used,avail,pcent data | sed 's/^/  /'
echo
echo "== Data =="
.venv/bin/python scripts/db_status.py
.venv/bin/python - <<'PY'
import sqlite3
con = sqlite3.connect("file:data/hiddenop.db?mode=ro", uri=True, timeout=60)
a, r = con.execute("SELECT SUM(archive IS NOT NULL), SUM(raw IS NOT NULL) FROM matches").fetchone()
print(f"\nRaw JSON: {a or 0:,} matches archived to Drive, {r or 0:,} still local")
PY
echo
echo "== Problems in the last 24 h (last 10) =="
journalctl -u 'hiddenop-*' --since -24h --no-pager -q \
  | grep -iE 'error|traceback|rejected|skipping|failed|mismatch' | tail -n 10 || echo "  none"
