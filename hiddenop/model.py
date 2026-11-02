"""Hierarchical (partially pooled) logistic model of champion-role winrate.

    logit P(win) = mu[role]
                 + b[champ]                 shared champion strength (all roles)
                 + g[archetype, role]       e.g. "Mage in BOTTOM"
                 + d[champ, role]           role-specific offset, tight prior
                 + beta * z_playrate        nuisance: log playrate, centered per role

Gaussian priors on b, g, d do the partial pooling: a champ-role cell with 40
games stays near what its champion + archetype predict, and only its own data
moves it as games accumulate. Fit = MAP via L-BFGS, uncertainty = Laplace
approximation (inverse Hessian at the mode). Small, fast, no MCMC needed for v1.
Swap to PyMC/NumPyro later if you want learned hyperparameters (see HANDOFF.md).

"Strength" reported downstream = the linear predictor with the playrate term
set to zero, i.e. the winrate we'd expect at an average playrate for the role.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize, sparse
from scipy.special import expit


@dataclass
class Priors:
    sd_role: float = 1.0      # role intercepts (weak)
    sd_beta: float = 1.0      # playrate slope (weak)
    sd_champ: float = 0.15    # ~ +/-3.7% WR at 1 sd
    sd_arch_role: float = 0.15
    sd_champ_role: float = 0.10  # tighter: off-role cells borrow heavily


@dataclass
class FittedModel:
    params: np.ndarray
    cov: np.ndarray
    index: dict[str, dict] = field(repr=False)
    archetypes: dict[str, str] = field(repr=False)
    roles: list[str] = field(repr=False)
    playrate_center: dict[str, float] = field(repr=False)
    priors: Priors = field(default_factory=Priors, repr=False)

    def _strength_row(self, champion: str, role: str) -> sparse.csr_matrix:
        """Design row for the strength predictor (playrate term excluded)."""
        P = len(self.params)
        cols = [self.index["role"][role]]
        if champion in self.index["champ"]:
            cols.append(self.index["champ"][champion])
        arch = self.archetypes.get(champion)
        if (arch, role) in self.index["arch_role"]:
            cols.append(self.index["arch_role"][(arch, role)])
        if (champion, role) in self.index["champ_role"]:
            cols.append(self.index["champ_role"][(champion, role)])
        return sparse.csr_matrix((np.ones(len(cols)), ([0] * len(cols), cols)), shape=(1, P))

    def strength(self, champion: str, role: str, z: float = 1.645) -> dict:
        """Playrate-adjusted winrate with a (default 90%) interval.

        Works for champ-role combos never seen in training: unknown terms are 0
        (their prior mean), so the estimate comes from champion + archetype.
        """
        x = self._strength_row(champion, role).toarray().ravel()
        eta = float(x @ self.params)
        var = float(x @ self.cov @ x)
        # Terms absent from training sit at their prior mean (0) but still carry
        # their prior variance — otherwise unseen combos would look falsely certain.
        pr = self.priors
        if champion not in self.index["champ"]:
            var += pr.sd_champ ** 2
        if (self.archetypes.get(champion), role) not in self.index["arch_role"]:
            var += pr.sd_arch_role ** 2
        if (champion, role) not in self.index["champ_role"]:
            var += pr.sd_champ_role ** 2
        sd = np.sqrt(max(var, 0.0))
        return {"wr": expit(eta), "lo": expit(eta - z * sd), "hi": expit(eta + z * sd),
                "logit": eta, "logit_sd": sd}


def _build(df: pd.DataFrame, archetypes: dict[str, str], roles: list[str], priors: Priors):
    champs = sorted(df.champion.unique())
    arch_roles = sorted({(archetypes.get(c, "Unknown"), r) for c in champs for r in roles})
    champ_roles = sorted(set(zip(df.champion, df.role)))

    index: dict[str, dict] = {"role": {}, "beta": {}, "champ": {}, "arch_role": {}, "champ_role": {}}
    prior_sd: list[float] = []

    def add(group, key, sd):
        index[group][key] = len(prior_sd)
        prior_sd.append(sd)

    for r in roles:
        add("role", r, priors.sd_role)
    add("beta", "playrate", priors.sd_beta)
    for c in champs:
        add("champ", c, priors.sd_champ)
    for ar in arch_roles:
        add("arch_role", ar, priors.sd_arch_role)
    for cr in champ_roles:
        add("champ_role", cr, priors.sd_champ_role)

    # Centered log playrate per role (weighted by games so it's the "typical pick" level).
    logpr = np.log(df.playrate.clip(lower=1e-6))
    center = {r: float(np.average(logpr[df.role == r], weights=df.games[df.role == r]))
              for r in roles if (df.role == r).any()}
    zpr = logpr - df.role.map(center)

    rows, cols, vals = [], [], []
    for i, (c, r, z) in enumerate(zip(df.champion, df.role, zpr)):
        a = archetypes.get(c, "Unknown")
        for col, v in ((index["role"][r], 1.0), (index["beta"]["playrate"], z),
                       (index["champ"][c], 1.0), (index["arch_role"][(a, r)], 1.0),
                       (index["champ_role"][(c, r)], 1.0)):
            rows.append(i); cols.append(col); vals.append(v)
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(len(df), len(prior_sd)))
    return X, np.asarray(prior_sd), index, center


def fit(df: pd.DataFrame, archetypes: dict[str, str], roles: list[str] | None = None,
        priors: Priors | None = None) -> FittedModel:
    """df needs columns: champion, role, games, wins, playrate."""
    priors = priors or Priors()
    roles = roles or sorted(df.role.unique())
    X, prior_sd, index, center = _build(df, archetypes, roles, priors)
    n = df.games.to_numpy(float)
    k = df.wins.to_numpy(float)
    prec = 1.0 / prior_sd ** 2

    def nlp(theta):
        eta = X @ theta
        # binomial log-lik: k*eta - n*log(1+e^eta), computed stably
        ll = k * eta - n * np.logaddexp(0.0, eta)
        lp = -0.5 * np.sum(prec * theta ** 2)
        grad = X.T @ (k - n * expit(eta)) - prec * theta
        return -(ll.sum() + lp), -grad

    res = optimize.minimize(nlp, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B",
                            options={"maxiter": 5000})
    theta = res.x
    p = expit(X @ theta)
    W = sparse.diags(n * p * (1 - p))
    H = (X.T @ W @ X).toarray() + np.diag(prec)
    cov = np.linalg.inv(H)
    return FittedModel(theta, cov, index, archetypes, roles, center, priors)
