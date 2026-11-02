"""Rank champion-role picks by playrate-adjusted strength and flag hidden OP ones.

hidden_op = interval lower bound above the role baseline AND playrate below a
threshold. Also reports naive (raw) winrate so the write-up can show how the
ranking changes once small samples and the playrate bias are accounted for.
"""
from __future__ import annotations

import pandas as pd

from .model import FittedModel


def score_table(model: FittedModel, df: pd.DataFrame, max_playrate: float = 0.02,
                baseline: float = 0.5, extra_pairs: list[tuple[str, str]] | None = None
                ) -> pd.DataFrame:
    """df: the champ-role table (possibly multi-patch); aggregated per champ-role here.
    extra_pairs: (champion, role) combos to score even if unseen, e.g. ('Sylas','BOTTOM').
    """
    agg = df.groupby(["champion", "role"], as_index=False).agg(
        games=("games", "sum"), wins=("wins", "sum"), playrate=("playrate", "mean"))
    if extra_pairs:
        seen = set(zip(agg.champion, agg.role))
        new = [{"champion": c, "role": r, "games": 0, "wins": 0, "playrate": 0.0}
               for c, r in extra_pairs if (c, r) not in seen]
        agg = pd.concat([agg, pd.DataFrame(new)], ignore_index=True)

    out = []
    for row in agg.itertuples():
        s = model.strength(row.champion, row.role)
        out.append({
            "champion": row.champion, "role": row.role, "games": row.games,
            "playrate": row.playrate,
            "raw_wr": row.wins / row.games if row.games else float("nan"),
            "adj_wr": s["wr"], "lo90": s["lo"], "hi90": s["hi"],
        })
    t = pd.DataFrame(out)
    t["hidden_op"] = (t.lo90 > baseline) & (t.playrate < max_playrate)
    return t.sort_values("lo90", ascending=False).reset_index(drop=True)
