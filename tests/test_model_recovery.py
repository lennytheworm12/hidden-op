"""Model sanity checks on synthetic data with known ground truth."""
import numpy as np
import pytest
from scipy.special import logit

from hiddenop.aggregate import add_playrate
from hiddenop.backtest import walk_forward
from hiddenop.model import fit
from hiddenop.score import score_table
from hiddenop.simulate import simulate


@pytest.fixture(scope="module")
def world():
    df, truth, arch = simulate()
    df = df[~((df.champion == "C000") & (df.role == "UTILITY"))]  # hold out: a never-played combo
    df = add_playrate(df)
    model = fit(df, arch)
    scored = score_table(model, df, max_playrate=0.01, extra_pairs=[("C000", "UTILITY")])
    return df, truth, arch, model, scored


def _row(scored, c, r):
    return scored[(scored.champion == c) & (scored.role == r)].iloc[0]


def test_planted_hidden_pick_is_flagged(world):
    *_, scored = world
    assert _row(scored, "C007", "BOTTOM").hidden_op


def test_playrate_inflated_decoy_is_not_flagged(world):
    *_, scored = world
    decoy = _row(scored, "C013", "JUNGLE")
    assert decoy.raw_wr > 0.52          # looks good naively...
    assert not decoy.hidden_op          # ...but not after adjustment


def test_adjusted_tracks_truth_better_than_raw(world):
    _, truth, _, _, scored = world
    s = scored[scored.games > 0]
    true = np.array([truth[(c, r)] for c, r in zip(s.champion, s.role)])
    adj = np.corrcoef(logit(s.adj_wr), true)[0, 1]
    raw = np.corrcoef(logit(s.raw_wr.clip(0.01, 0.99)), true)[0, 1]
    assert adj > raw + 0.2


def test_flags_are_mostly_real(world):
    _, truth, _, _, scored = world
    f = scored[scored.hidden_op]
    assert len(f) > 0
    assert np.mean([truth[(c, r)] > 0 for c, r in zip(f.champion, f.role)]) >= 0.8


def test_playrate_bias_recovered(world):
    _, _, _, model, _ = world
    beta = model.params[model.index["beta"]["playrate"]]
    assert -0.08 < beta < -0.01  # simulated bias is -0.04


def test_unseen_pair_gets_wide_interval(world):
    *_, scored = world
    unseen = _row(scored, "C000", "UTILITY")
    assert unseen.games == 0
    assert unseen.hi90 - unseen.lo90 > 0.08


def test_backtest_runs():
    df, _, arch = simulate(n_patches=5, games_per_role=30_000)
    bt = walk_forward(add_playrate(df), arch, window=3)
    assert len(bt) == 2
    assert (bt.model_next_wr > 0.5).all()
