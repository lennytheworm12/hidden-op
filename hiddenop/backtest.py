"""Walk-forward backtest: fit on patches <= t, check flagged picks on patch t+1.

The question it answers: "when the model called something hidden-OP, did it
keep winning next patch?" Report for each step:
  - n flagged
  - pooled next-patch WR of flagged picks (games-weighted) vs 50%
  - same for a naive baseline: low-playrate picks with raw WR > 53% and >= 30 games

The headline backtest for the write-up (did it catch picks like Seraphine bot
BEFORE they went mainstream?) needs older patches — see HANDOFF.md.
"""
from __future__ import annotations

import pandas as pd

from .aggregate import patch_key
from .model import Priors, fit
from .score import score_table


def walk_forward(df: pd.DataFrame, archetypes: dict[str, str], window: int = 3,
                 max_playrate: float = 0.01, priors: Priors | None = None) -> pd.DataFrame:
    patches = sorted(df.patch.unique(), key=patch_key)
    results = []
    for i in range(window, len(patches)):
        train = df[df.patch.isin(patches[i - window:i])]
        test = df[df.patch == patches[i]]
        model = fit(train, archetypes, priors=priors)
        scored = score_table(model, train, max_playrate=max_playrate)
        naive = scored[(scored.playrate < max_playrate) & (scored.games >= 30) & (scored.raw_wr > 0.53)]

        def next_patch_wr(picks: pd.DataFrame):
            j = test.merge(picks[["champion", "role"]], on=["champion", "role"])
            return (j.wins.sum() / j.games.sum()) if j.games.sum() else float("nan"), int(j.games.sum())

        flagged = scored[scored.hidden_op]
        m_wr, m_g = next_patch_wr(flagged)
        n_wr, n_g = next_patch_wr(naive)
        results.append({"test_patch": patches[i], "model_flagged": len(flagged),
                        "model_next_wr": m_wr, "model_next_games": m_g,
                        "naive_flagged": len(naive), "naive_next_wr": n_wr, "naive_next_games": n_g})
    return pd.DataFrame(results)
