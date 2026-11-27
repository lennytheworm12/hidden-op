"""Crawl high-elo solo queue matches into SQLite.

Usage:
    python -m hiddenop.collector.collect --platform na1 --days 14 --max-matches 50000

Strategy: seed players from the full ladders of the requested tiers (default
Master+, Diamond, Emerald), then pull each player's recent ranked match IDs and
download matches we haven't seen. Breadth over depth: a player who already
appears in --max-per-player stored games from the window is skipped without an
API call, so the budget goes to many players with a few games each instead of
every game of a few grinders. For rare champions that matters, because one
player's skill shows up in all of their games. Never-crawled players go first,
round-robin across tiers so the large ladders don't starve the small ones. Each
match is tagged with the tier of the player it was found through. Every match yields 10
participants, so ~1 API call per match once seeding is done. Safe to stop and
restart — progress lives in the DB.
"""
from __future__ import annotations

import argparse
import logging
import time

from .riot_client import GaveUp, RiotClient
from .storage import connect, known_match, queue_mastery, store_mastery, store_match, store_timeline

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
    # Upsert: a player who moved tier since the last seed gets the new label.
    con.executemany("INSERT INTO players(puuid, platform, tier) VALUES (?,?,?)"
                    " ON CONFLICT(puuid) DO UPDATE SET tier=excluded.tier", rows)
    now = int(time.time())
    con.executemany(
        "INSERT OR IGNORE INTO ladder_snapshots VALUES (?,?,?,?,?,?,?,?)",
        [(e["puuid"], platform, now, tier, "" if tier in APEX else e.get("rank", ""),
          e.get("leaguePoints"), e.get("wins"), e.get("losses"))
         for e in entries if e.get("puuid")])
    return len(rows)


def seed_players(client: RiotClient, con, platform: str, tiers: list[str],
                 pages_per_division: int = 0) -> int:
    """pages_per_division=0 fetches every page (the whole ladder)."""
    n = 0
    for tier in tiers:
        if tier in APEX:
            league = client.apex_league(platform, tier.lower()) or {}
            n += _add(con, league.get("entries", []), platform, tier)
            con.commit()
            continue
        for div in DIVISIONS:
            page = 0
            while not pages_per_division or page < pages_per_division:
                page += 1
                entries = client.league_entries(platform, tier, div, page)
                if not entries:
                    break
                n += _add(con, entries, platform, tier)
                # Commit every page: the next page is an API call (~1 s), and an open
                # write transaction across many calls locks out the other regions.
                con.commit()
                if page % 50 == 0:
                    log.info("%s %s %s: %d pages so far", platform, tier, div, page)
        log.info("%s %s: seeded", platform, tier)
    con.commit()
    return n


def games_in_window(con, puuid: str, start_time: int) -> int:
    """Stored games this player appears in (found through anyone) since start_time (s)."""
    return con.execute(
        "SELECT COUNT(*) FROM participants p JOIN matches m USING(match_id)"
        " WHERE p.puuid=? AND m.game_start>=?", (puuid, start_time * 1000)).fetchone()[0]


def drain_mastery(client: RiotClient, con, platform: str, limit: int = 20) -> int:
    """Fetch queued players' mastery, but only while the platform API has spare
    capacity, so it never slows down match collection (which uses the regional API).
    Newest first, so a lookup lands close to the games it describes."""
    n = 0
    while n < limit and client.has_capacity(platform):
        row = con.execute("SELECT puuid FROM mastery_todo WHERE platform=? ORDER BY added_at DESC LIMIT 1",
                          (platform,)).fetchone()
        if not row:
            break
        try:
            entries = client.mastery_all(platform, row[0])
        except GaveUp as e:
            log.warning("skipping mastery: %s", e)
            con.execute("DELETE FROM mastery_todo WHERE puuid=?", row)
            con.commit()
            continue
        store_mastery(con, platform, row[0], entries or [])
        con.commit()
        n += 1
    return n


def crawl(client: RiotClient, con, platform: str, days: int, max_matches: int,
          per_player: int = 10, max_per_player: int = 5, timelines: bool = True, mastery: bool = True,
          mastery_refresh_days: float = 7) -> int:
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
    skipped = 0
    for puuid, tier in players:
        room = max_per_player - games_in_window(con, puuid, start_time) if max_per_player else per_player
        if room <= 0:  # already well covered; spend the calls on someone else
            skipped += 1
            con.execute("UPDATE players SET last_crawled=? WHERE puuid=?", (int(time.time()), puuid))
            con.commit()
            continue
        try:
            ids = client.match_ids(platform, puuid, start_time=start_time, count=per_player)
        except GaveUp as e:
            log.warning("skipping player: %s", e)
            continue
        for mid in ids:
            if room <= 0:
                break
            if known_match(con, mid):
                continue
            try:
                m = client.match(platform, mid)
            except GaveUp as e:
                log.warning("skipping match: %s", e)
                continue
            if m and m.get("info", {}).get("queueId") == 420:
                tl = None
                if timelines:
                    try:
                        tl = client.timeline(platform, mid)
                    except GaveUp as e:  # keep the match; the timeline can be backfilled
                        log.warning("no timeline: %s", e)
                store_match(con, platform, m, tier=tier)
                if tl:
                    store_timeline(con, mid, tl)
                if mastery:
                    queue_mastery(con, platform, [p["puuid"] for p in m["info"]["participants"]],
                                  mastery_refresh_days)
                # Commit per match: other regions' collectors share the DB, and a
                # long-open write transaction would block them.
                con.commit()
                if mastery:
                    drain_mastery(client, con, platform)
                got += 1
                room -= 1
                if got % 100 == 0:
                    log.info("%s: %d new matches", platform, got)
                if got >= max_matches:
                    return got
        con.execute("UPDATE players SET last_crawled=? WHERE puuid=?", (int(time.time()), puuid))
        con.commit()
    log.info("%s: %d players skipped (already in %d games from the window)", platform, skipped, max_per_player)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="na1")
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--tiers", default=DEFAULT_TIERS,
                    help="comma list; MASTER+ = Challenger/GM/Master (e.g. MASTER+,DIAMOND,EMERALD)")
    ap.add_argument("--pages-per-division", type=int, default=0,
                    help="league-v4 pages (~205 players each) per division of non-apex tiers; 0 = all")
    ap.add_argument("--max-matches", type=int, default=50_000)
    ap.add_argument("--per-player", type=int, default=10,
                    help="recent match IDs to look at per player per pass")
    ap.add_argument("--max-per-player", type=int, default=5,
                    help="skip players already in this many stored games from the window (0 = no cap)")
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--no-timelines", action="store_true",
                    help="skip build/skill order (timelines cost one extra call per match)")
    ap.add_argument("--no-mastery", action="store_true", help="skip champion mastery lookups")
    ap.add_argument("--mastery-refresh-days", type=float, default=7,
                    help="re-fetch a player's mastery when the last lookup is older than this")
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
        got = crawl(client, con, args.platform, args.days, args.max_matches - total, args.per_player,
                    max_per_player=args.max_per_player, timelines=not args.no_timelines, mastery=not args.no_mastery,
                    mastery_refresh_days=args.mastery_refresh_days)
        total += got
        log.info("%s: pass done, %d new matches (%d this run)", args.platform, got, total)
        if not args.loop or total >= args.max_matches:
            break
        if got == 0:
            log.info("nothing new; fetching queued mastery for 30 min, then crawling again")
            end = time.time() + 1800
            while time.time() < end:
                if args.no_mastery or drain_mastery(client, con, args.platform) == 0:
                    time.sleep(5)


if __name__ == "__main__":
    main()
