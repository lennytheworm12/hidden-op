"""Synthetic data with known ground truth, to check the model before real data exists.

World:
  - true strength(c, r) = champ effect + archetype-role effect + small offset
  - popularity is partly driven by strength, partly random "meta" noise
  - observed WR has a playrate bias: niche picks look better than they are
    (one-trick effect), popular picks look worse
  - planted HIDDEN pick: genuinely strong, rarely played
  - planted DECOY pick: rarely played, average strength — its raw WR is inflated
    only by the playrate bias, which the model should strip out
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import expit

from .aggregate import ROLES

ARCHES = ["Fighter", "Tank", "Mage", "Assassin", "Marksman", "Support"]


def make_world(n_champs=120, seed=0):
    rng = np.random.default_rng(seed)
    champs = [f"C{i:03d}" for i in range(n_champs)]
    arch = {c: ARCHES[i % len(ARCHES)] for i, c in enumerate(champs)}
    champ_eff = dict(zip(champs, rng.normal(0, 0.12, n_champs)))
    ar_eff = {(a, r): rng.normal(0, 0.12) for a in ARCHES for r in ROLES}
    # Each champ has 1-2 "main" roles; every other role is off-role (tiny playrate).
    main_roles = {c: set(rng.choice(ROLES, size=rng.integers(1, 3), replace=False)) for c in champs}
    return dict(rng=rng, champs=champs, arch=arch, champ_eff=champ_eff, ar_eff=ar_eff,
                main_roles=main_roles)


def simulate(world=None, n_patches=3, games_per_role=60_000, playrate_bias=-0.04,
             hidden=("C007", "BOTTOM", 0.30), decoy=("C013", "JUNGLE"), seed=1):
    w = world or make_world()
    rng = np.random.default_rng(seed)
    hc, hr, hboost = hidden
    dc, dr = decoy
    truth, rows = {}, []
    for c in w["champs"]:
        for r in ROLES:
            s = w["champ_eff"][c] + w["ar_eff"][(w["arch"][c], r)] + rng.normal(0, 0.05)
            if (c, r) == (hc, hr):
                s += hboost
            if (c, r) == (dc, dr):
                s = 0.0
            truth[(c, r)] = s

    for p in range(n_patches):
        patch = f"16.{18 + p}"
        for r in ROLES:
            pairs = [(c, r) for c in w["champs"]]
            pop = np.array([
                (2.5 if r in w["main_roles"][c] else -2.5) + 2.0 * truth[(c, r)] + rng.normal(0, 0.6)
                for c, _ in pairs])
            for i, (c, _) in enumerate(pairs):
                if (c, r) in ((hc, hr), (dc, dr)):
                    pop[i] = 0.5  # force rare: ~0.25% playrate, a few hundred games over 3 patches
            pr = np.exp(pop) / np.exp(pop).sum()
            games = rng.multinomial(games_per_role, pr)
            logpr = np.log(np.clip(pr, 1e-6, None))
            centered = logpr - np.average(logpr, weights=pr)
            for i, (c, _) in enumerate(pairs):
                if games[i] == 0:
                    continue
                bias = playrate_bias * centered[i]
                wins = rng.binomial(games[i], expit(truth[(c, r)] + bias))
                rows.append((c, r, patch, int(games[i]), int(wins)))
    df = pd.DataFrame(rows, columns=["champion", "role", "patch", "games", "wins"])
    return df, truth, w["arch"]
