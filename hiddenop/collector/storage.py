"""SQLite storage. One row per match, one row per participant.

Raw match JSON is also kept (compressed) so new features can be extracted later
without re-downloading — API budget is the scarce resource, disk is not.
"""
from __future__ import annotations

import json
import sqlite3
import time
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    tier TEXT,
    last_crawled INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    patch TEXT NOT NULL,          -- e.g. '16.21'
    game_start INTEGER,           -- ms epoch
    duration_s INTEGER,
    raw BLOB,                     -- zlib(json)
    tier TEXT                     -- tier of the seed player the match was found through
);
CREATE TABLE IF NOT EXISTS participants (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    team_id INTEGER,
    champion TEXT NOT NULL,
    champion_id INTEGER,
    role TEXT NOT NULL,           -- TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY ('' if Riot couldn't assign)
    win INTEGER NOT NULL,
    items TEXT,                   -- json list of item ids (for v2 build clustering)
    dmg_to_champs INTEGER,
    heal_on_allies INTEGER,
    shield_on_allies INTEGER,
    deaths INTEGER,
    PRIMARY KEY (match_id, puuid)
);
-- Ladder state of seeded players each time the ladder is fetched (daily with --loop).
-- Rank/LP/games-played at a point in time can't be fetched again later.
CREATE TABLE IF NOT EXISTS ladder_snapshots (
    puuid TEXT NOT NULL,
    platform TEXT NOT NULL,
    taken_at INTEGER NOT NULL,    -- s epoch
    tier TEXT,
    division TEXT,                -- I-IV ('' for apex tiers)
    lp INTEGER,
    wins INTEGER,
    losses INTEGER,
    PRIMARY KEY (puuid, taken_at)
);
-- Extracted from the match timeline (raw timeline is ~1 MB, not kept).
CREATE TABLE IF NOT EXISTS timelines (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    items TEXT,      -- json [[second, item_id, "B"|"S"|"U"], ...] buy / sell / undo, in order
    skills TEXT,     -- skill level-up order, e.g. "QEWQQRQEQE..." (R included)
    gold10 INTEGER, cs10 INTEGER, xp10 INTEGER,
    gold15 INTEGER, cs15 INTEGER, xp15 INTEGER,
    PRIMARY KEY (match_id, puuid)
);
-- Champion mastery per player (all champions in one zlib json blob), refreshed every few days.
CREATE TABLE IF NOT EXISTS mastery (
    puuid TEXT NOT NULL,
    platform TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,  -- s epoch
    data BLOB,                    -- zlib(json [[champion_id, points, level, last_play_s], ...])
    PRIMARY KEY (puuid, fetched_at)
);
-- Players seen in matches whose mastery hasn't been fetched yet (drained when the platform API is idle).
CREATE TABLE IF NOT EXISTS mastery_todo (
    puuid TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    added_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_part_champ_role ON participants(champion, role);
CREATE INDEX IF NOT EXISTS ix_part_puuid ON participants(puuid);  -- games-per-player cap
"""


def connect(path: str | Path = "data/hiddenop.db") -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Several collectors (one per region) share this file. WAL lets readers and a
    # writer coexist, and the long timeout makes writers wait their turn instead
    # of crashing with "database is locked".
    con = sqlite3.connect(path, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(matches)")}
    if "tier" not in cols:  # DBs created before tier tagging
        con.execute("ALTER TABLE matches ADD COLUMN tier TEXT")
    return con


def patch_of(game_version: str) -> str:
    major, minor = game_version.split(".")[:2]
    return f"{major}.{minor}"


def store_match(con: sqlite3.Connection, platform: str, m: dict, tier: str | None = None) -> None:
    info = m["info"]
    mid = m["metadata"]["matchId"]
    con.execute(
        "INSERT OR IGNORE INTO matches(match_id, platform, patch, game_start, duration_s, raw, tier)"
        " VALUES (?,?,?,?,?,?,?)",
        (mid, platform, patch_of(info["gameVersion"]), info.get("gameStartTimestamp"),
         info.get("gameDuration"), zlib.compress(json.dumps(m).encode()), tier),
    )
    rows = []
    for p in info["participants"]:
        items = [p.get(f"item{i}", 0) for i in range(7)]
        rows.append((
            mid, p["puuid"], p["teamId"], p["championName"], p["championId"],
            p.get("teamPosition") or "", int(p["win"]), json.dumps(items),
            p.get("totalDamageDealtToChampions"), p.get("totalHealsOnTeammates"),
            p.get("totalDamageShieldedOnTeammates"), p.get("deaths"),
        ))
    con.executemany("INSERT OR IGNORE INTO participants VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def known_match(con: sqlite3.Connection, match_id: str) -> bool:
    return con.execute("SELECT 1 FROM matches WHERE match_id=?", (match_id,)).fetchone() is not None


SKILL_KEYS = {1: "Q", 2: "W", 3: "E", 4: "R"}


def _frame_stats(frames: list, minute: int, pid: int):
    if len(frames) <= minute:
        return None, None, None
    pf = frames[minute].get("participantFrames", {}).get(str(pid), {})
    cs = (pf.get("minionsKilled") or 0) + (pf.get("jungleMinionsKilled") or 0)
    return pf.get("totalGold"), cs, pf.get("xp")


def store_timeline(con: sqlite3.Connection, match_id: str, tl: dict) -> None:
    """Keep build order, skill order and gold/CS/XP at 10 and 15 minutes per player."""
    puuids = tl["metadata"]["participants"]  # index i -> participantId i+1
    frames = tl["info"]["frames"]
    items = {pid: [] for pid in range(1, len(puuids) + 1)}
    skills = {pid: [] for pid in items}
    kinds = {"ITEM_PURCHASED": "B", "ITEM_SOLD": "S", "ITEM_UNDO": "U"}
    for fr in frames:
        for ev in fr.get("events", []):
            t, pid = ev.get("type"), ev.get("participantId")
            if pid not in items:
                continue
            if t in kinds:
                item = ev.get("itemId") or ev.get("beforeId") or 0
                items[pid].append([ev.get("timestamp", 0) // 1000, item, kinds[t]])
            elif t == "SKILL_LEVEL_UP" and ev.get("levelUpType", "NORMAL") == "NORMAL":
                skills[pid].append(SKILL_KEYS.get(ev.get("skillSlot"), "?"))
    rows = []
    for pid, puuid in enumerate(puuids, start=1):
        rows.append((match_id, puuid, json.dumps(items[pid], separators=(",", ":")),
                     "".join(skills[pid]), *_frame_stats(frames, 10, pid), *_frame_stats(frames, 15, pid)))
    con.executemany("INSERT OR IGNORE INTO timelines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def queue_mastery(con: sqlite3.Connection, platform: str, puuids, refresh_days: float) -> None:
    now = int(time.time())
    cutoff = now - int(refresh_days * 86400)
    for puuid in puuids:
        fresh = con.execute("SELECT 1 FROM mastery WHERE puuid=? AND fetched_at>=? LIMIT 1",
                            (puuid, cutoff)).fetchone()
        if not fresh:
            con.execute("INSERT OR IGNORE INTO mastery_todo VALUES (?,?,?)", (puuid, platform, now))


def store_mastery(con: sqlite3.Connection, platform: str, puuid: str, entries: list) -> None:
    data = [[e.get("championId"), e.get("championPoints"), e.get("championLevel"),
             (e.get("lastPlayTime") or 0) // 1000] for e in entries]
    con.execute("INSERT OR IGNORE INTO mastery VALUES (?,?,?,?)",
                (puuid, platform, int(time.time()),
                 zlib.compress(json.dumps(data, separators=(",", ":")).encode())))
    con.execute("DELETE FROM mastery_todo WHERE puuid=?", (puuid,))


def mastery_of(con: sqlite3.Connection, puuid: str, champion_id: int, before: int | None = None):
    """(points, level, last_play_s) from the latest snapshot (optionally taken before a time), or None."""
    q = "SELECT data FROM mastery WHERE puuid=?" + (" AND fetched_at<=?" if before else "")
    q += " ORDER BY fetched_at DESC LIMIT 1"
    row = con.execute(q, (puuid, before) if before else (puuid,)).fetchone()
    if not row:
        return None
    for cid, pts, lvl, last in json.loads(zlib.decompress(row[0])):
        if cid == champion_id:
            return pts, lvl, last
    return 0, 0, 0
