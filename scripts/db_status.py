"""Show how much data the collector has gathered so far.

    python scripts/db_status.py [--db data/hiddenop.db]

Safe to run while collectors are running.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TIER_ORDER = ["CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD", "PLATINUM"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/hiddenop.db")
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"No database at {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)

    size = sum(p.stat().st_size for p in db.parent.glob(db.name + "*"))
    total = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"{db}: {total:,} matches, {size / 1e9:.2f} GB on disk\n")

    print("Matches by region and tier:")
    rows = con.execute("SELECT platform, tier, COUNT(*) FROM matches GROUP BY 1, 2").fetchall()
    for plat in sorted({r[0] for r in rows}):
        parts = sorted(((t or "?", n) for p, t, n in rows if p == plat),
                       key=lambda x: TIER_ORDER.index(x[0]) if x[0] in TIER_ORDER else 99)
        print(f"  {plat:5} " + "  ".join(f"{t} {n:,}" for t, n in parts))

    print("\nMatches by patch:")
    for patch, n in con.execute("SELECT patch, COUNT(*) FROM matches GROUP BY 1 ORDER BY 1"):
        print(f"  {patch:6} {n:,}")

    def count(sql):
        try:
            return con.execute(sql).fetchone()[0] or 0
        except sqlite3.OperationalError:  # table not there in older DBs
            return 0
    tl = count("SELECT COUNT(DISTINCT match_id) FROM timelines")
    print(f"\nMatches with build/skill order: {tl:,} of {total:,}")
    print(f"Players with mastery: {count('SELECT COUNT(DISTINCT puuid) FROM mastery'):,}"
          f" (waiting: {count('SELECT COUNT(*) FROM mastery_todo'):,})")

    seen, players = con.execute(
        "SELECT SUM(last_crawled > 0), COUNT(*) FROM players").fetchone()
    print(f"\nPlayers crawled this pass or earlier: {seen or 0:,} of {players:,}")


if __name__ == "__main__":
    main()
