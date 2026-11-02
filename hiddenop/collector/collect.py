"""Crawl high-elo solo queue matches into SQLite.

Usage:
    python -m hiddenop.collector.collect --platform na1 --days 14 --max-matches 50000

Strategy: seed players from Master+ and Diamond I, then pull each player's recent
ranked match IDs and download matches we haven't seen. Every match yields 10
participants, so ~1 API call per match once seeding is done. Safe to stop and
restart — progress lives in the DB.
"""
from __future__ import annotations

import argparse
import logging
import time

from .riot_client import RiotClient
from .storage import connect, known_match, store_match

log = logging.getLogger("collect")


def seed_players(client: RiotClient, con, platform: str, diamond_pages: int) -> int:
    n = 0
    for tier in ("challenger", "grandmaster", "master"):
        league = client.apex_league(platform, tier) or {}
        for e in league.get("entries", []):
            if e.get("puuid"):
                con.execute("INSERT OR IGNORE INTO players(puuid, platform, tier) VALUES (?,?,?)",
                            (e["puuid"], platform, tier.upper()))
                n += 1
    for page in range(1, diamond_pages + 1):
        entries = client.league_entries(platform, "DIAMOND", "I", page)
        if not entries:
            break
        for e in entries:
            if e.get("puuid"):
                con.execute("INSERT OR IGNORE INTO players(puuid, platform, tier) VALUES (?,?,?)",
                            (e["puuid"], platform, "DIAMOND"))
                n += 1
    con.commit()
    return n


def crawl(client: RiotClient, con, platform: str, days: int, max_matches: int) -> None:
    start_time = int(time.time()) - days * 86400
    got = 0
    players = con.execute(
        "SELECT puuid FROM players WHERE platform=? ORDER BY last_crawled ASC", (platform,)
    ).fetchall()
    for (puuid,) in players:
        for mid in client.match_ids(platform, puuid, start_time=start_time):
            if known_match(con, mid):
                continue
            m = client.match(platform, mid)
            if m and m.get("info", {}).get("queueId") == 420:
                store_match(con, platform, m)
                got += 1
                if got % 100 == 0:
                    con.commit()
                    log.info("%s: %d new matches", platform, got)
                if got >= max_matches:
                    con.commit()
                    return
        con.execute("UPDATE players SET last_crawled=? WHERE puuid=?", (int(time.time()), puuid))
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="na1")
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--diamond-pages", type=int, default=5)
    ap.add_argument("--max-matches", type=int, default=50_000)
    ap.add_argument("--skip-seed", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    client = RiotClient()
    con = connect(args.db)
    if not args.skip_seed:
        log.info("seeded %d players", seed_players(client, con, args.platform, args.diamond_pages))
    crawl(client, con, args.platform, args.days, args.max_matches)


if __name__ == "__main__":
    main()
