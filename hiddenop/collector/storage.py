"""SQLite storage. One row per match, one row per participant.

Raw match JSON is also kept (compressed) so new features can be extracted later
without re-downloading — API budget is the scarce resource, disk is not.
"""
from __future__ import annotations

import json
import sqlite3
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
    raw BLOB                      -- zlib(json)
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
CREATE INDEX IF NOT EXISTS ix_part_champ_role ON participants(champion, role);
"""


def connect(path: str | Path = "data/hiddenop.db") -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def patch_of(game_version: str) -> str:
    major, minor = game_version.split(".")[:2]
    return f"{major}.{minor}"


def store_match(con: sqlite3.Connection, platform: str, m: dict) -> None:
    info = m["info"]
    mid = m["metadata"]["matchId"]
    con.execute(
        "INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?)",
        (mid, platform, patch_of(info["gameVersion"]), info.get("gameStartTimestamp"),
         info.get("gameDuration"), zlib.compress(json.dumps(m).encode())),
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
