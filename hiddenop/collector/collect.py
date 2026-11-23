"""Crawl high-elo solo queue matches into SQLite.

Usage:
    python -m hiddenop.collector.collect --platform na1 --days 14 --max-matches 50000

Strategy: seed players from the requested tiers (default Master+, Diamond, Emerald),
then pull each player's recent ranked match IDs and download matches we haven't
seen. Players are crawled round-robin across tiers so the large Master ladder
doesn't starve lower brackets, where off-meta experiments are more common. Each
match is tagged with the tier of the player it was found through. Every match yields 10
participants, so ~1 API call per match once seeding is done. Safe to stop and
restart — progress lives in the DB.
"""
from __future__ import annotations

import argparse
import logging
import time

from .riot_client import GaveUp, RiotClient
from .storage import connect, known_match, store_match

log = logging.getLogger("collect")


APEX = ("CHALLENGER", "GRANDMASTER", "MASTER")
DIVISIONS = ("I", "II", "III", "IV")
DEFAULT_TIERS = "MASTER+,DIAMOND,EMERALD"


def parse_tiers(spec: str) -> list[str]:
    """'MASTER+,DIAMOND,EMERALD' -> ['CHALLENGER','GRANDMASTER','MASTER','DIAMOND','EMERALD']"""
    out: list[str] = []
    for t in (x.strip().upper() for x in spec.split(",") if x.strip()):
        out.extend(APEX if t == "MASTER+" else [t])
    return list(dict.fromkeys(out))


def _add(con, entries, platform: str, tier: str) -> int:
    rows = [(e["puuid"], platform, tier) for e in entries if e.get("puuid")]
    if entries and not rows:
        log.warning("%s %s: entries have no puuid field; league-v4 may have changed", platform, tier)
    con.executemany("INSERT OR IGNORE INTO players(puuid, platform, tier) VALUES (?,?,?)", rows)
    now = int(time.time())
    con.executemany(
        "INSERT OR IGNORE INTO ladder_snapshots VALUES (?,?,?,?,?,?,?,?)",
        [(e["puuid"], platform, now, tier, "" if tier in APEX else e.get("rank", ""),
          e.get("leaguePoints"), e.get("wins"), e.get("losses"))
         for e in entries if e.get("puuid")])
    return len(rows)


def seed_players(client: RiotClient, con, platform: str, tiers: list[str],
                 pages_per_division: int) -> int:
    n = 0
    for tier in tiers:
        if tier in APEX:
            league = client.apex_league(platform, tier.lower()) or {}
            n += _add(con, league.get("entries", []), platform, tier)
            continue
        for div in DIVISIONS:
            for page in range(1, pages_per_division + 1):
                entries = client.league_entries(platform, tier, div, page)
                if not entries:
                    break
                n += _add(con, entries, platform, tier)
    con.commit()
    return n


def crawl(client: RiotClient, con, platform: str, days: int, max_matches: int,
          per_player: int = 10) -> int:
    """One pass over this platform's players. Returns the number of new matches."""
    start_time = int(time.time()) - days * 86400
    got = 0
    # Round-robin across tiers: least-recently-crawled first, interleaved by tier.
    players = con.execute(
        """SELECT puuid, tier FROM (
               SELECT puuid, tier, last_crawled,
                      ROW_NUMBER() OVER (PARTITION BY tier ORDER BY last_crawled, RANDOM()) AS k
               FROM players WHERE platform=?)
           ORDER BY k, tier""", (platform,)
    ).fetchall()
    log.info("%s: crawling %d players", platform, len(players))
    for puuid, tier in players:
        try:
            ids = client.match_ids(platform, puuid, start_time=start_time, count=per_player)
        except GaveUp as e:
            log.warning("skipping player: %s", e)
            continue
        for mid in ids:
            if known_match(con, mid):
                continue
            try:
                m = client.match(platform, mid)
            except GaveUp as e:
                log.warning("skipping match: %s", e)
                continue
            if m and m.get("info", {}).get("queueId") == 420:
                store_match(con, platform, m, tier=tier)
                # Commit per match: other regions' collectors share the DB, and a
                # long-open write transaction would block them.
                con.commit()
                got += 1
                if got % 100 == 0:
                    log.info("%s: %d new matches", platform, got)
                if got >= max_matches:
                    return got
        con.execute("UPDATE players SET last_crawled=? WHERE puuid=?", (int(time.time()), puuid))
        con.commit()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="na1")
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--tiers", default=DEFAULT_TIERS,
                    help="comma list; MASTER+ = Challenger/GM/Master (e.g. MASTER+,DIAMOND,EMERALD)")
    ap.add_argument("--pages-per-division", type=int, default=3,
                    help="league-v4 pages (~205 players each) per division of non-apex tiers")
    ap.add_argument("--max-matches", type=int, default=50_000)
    ap.add_argument("--per-player", type=int, default=10,
                    help="recent match IDs to take per player per pass (keeps tiers balanced)")
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--loop", action="store_true",
                    help="keep crawling pass after pass (re-seeding daily) until --max-matches or Ctrl+C")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    client = RiotClient()
    con = connect(args.db)
    tiers = parse_tiers(args.tiers)
    seeded_at = 0.0
    total = 0
    while True:
        # Ladders change, so a long --loop run re-seeds once a day.
        if not args.skip_seed and time.time() - seeded_at > 86400:
            log.info("seeded %d players", seed_players(client, con, args.platform, tiers,
                                                     args.pages_per_division))
            seeded_at = time.time()
        got = crawl(client, con, args.platform, args.days, args.max_matches - total, args.per_player)
        total += got
        log.info("%s: pass done, %d new matches (%d this run)", args.platform, got, total)
        if not args.loop or total >= args.max_matches:
            break
        if got == 0:
            log.info("nothing new; sleeping 30 min")
            time.sleep(1800)


if __name__ == "__main__":
    main()
