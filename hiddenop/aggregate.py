"""Turn participant rows into the champion x role x patch table the model consumes.

Output columns: champion, role, patch, games, wins, playrate
playrate = games of this champ in this role / total games in this role (that patch).
"""
from __future__ import annotations

import sqlite3

import pandas as pd

ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def champ_role_table(con: sqlite3.Connection, min_patch: str | None = None) -> pd.DataFrame:
    q = """
        SELECT p.champion, p.role, m.patch, COUNT(*) AS games, SUM(p.win) AS wins
        FROM participants p JOIN matches m USING (match_id)
        WHERE p.role != ''
        GROUP BY p.champion, p.role, m.patch
    """
    df = pd.read_sql_query(q, con)
    if min_patch:
        df = df[df.patch.map(patch_key) >= patch_key(min_patch)]
    return add_playrate(df)


def add_playrate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    role_totals = df.groupby(["patch", "role"]).games.transform("sum")
    df["playrate"] = df.games / role_totals
    return df


def patch_key(p: str) -> tuple[int, int]:
    a, b = p.split(".")
    return int(a), int(b)
