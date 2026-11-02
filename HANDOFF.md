# Handoff: hidden-op

Context for whoever (or whichever Claude Code session) picks this up next.

## Goal

Quantify "hidden OP" picks: champion-role combos with high true strength and low
playrate, using winrate, playrate, archetype, and whatever else the data supports.
It's expected that some picks (e.g. Nidalee support, whose identity differs from
jungle Nidalee) can't be fully captured from data; for those the model should say
"uncertain", not guess.

## Status (v1 scaffold, built and tested on synthetic data only)

- `pytest` passes 7 checks on a simulated world: planted hidden pick is flagged,
  a playrate-inflated decoy is not, adjusted WR tracks ground truth far better than
  raw WR (r≈0.89 vs 0.41), flagged picks are ≥80% truly positive, the playrate
  bias is recovered, unseen combos get wide intervals, the backtest runs.
- **Not yet run against the real Riot API.** The collector was written blind —
  the build environment couldn't reach riotgames.com. Expect small fixes.

## First steps on a real machine

1. `cp .env.example .env`, add a dev key from developer.riotgames.com
   (expires every 24h — regenerate daily, or register a personal key).
2. Smoke-test the client: `seed_players` then one `match()` call. Check:
   - league entries include `puuid` (they should in current league-v4; if not,
     resolve via summoner-v4).
   - `championName` values match Data Dragon ids (`MonkeyKing` for Wukong, etc.).
   - `teamPosition` is populated (empty string for remakes/odd games is filtered).
3. Run the collector per platform in parallel (limits are per routing value):
   `na1`, `euw1`, `kr`. ~100 req / 2 min each ⇒ ~70k req/day/platform.
4. After ~1 patch of data: `python scripts/run_model.py` and sanity-check the
   top of the table against your game knowledge.

## Design decisions

- **Model = MAP + Laplace, not MCMC.** ~750 params, fits in <1 s. Good enough for
  intervals. If you want learned hyperparameters (prior SDs), move to
  PyMC/NumPyro or tune `Priors` by held-out log-likelihood across patches.
- **Strength excludes the playrate term.** `adj_wr` = predicted WR at the role's
  typical playrate. Known caveat: the playrate slope mixes two opposite effects —
  one-trick inflation (niche looks better) and "strong picks get popular"
  (popular looks better). β is the net; the mastery correction (v2) is what
  actually separates them.
- **Priors:** champ 0.15, archetype×role 0.15, champ×role offset 0.10 (logit).
  The tight offset prior is what lets Sylas BOTTOM borrow from Sylas + Mage-in-bot.
- **Raw match JSON is stored compressed** so new features (timelines aside) can
  be extracted without re-spending API budget.
- **Archetypes = Data Dragon primary tag** for v1. Coarse but objective. Override
  via `data/archetypes_override.csv`.

## Roadmap

**v2 — corrections that matter most**
- One-trick/mastery correction: for low-playrate candidates, pull
  champion-mastery-v4 for their players; compare WR at matched experience levels
  and use winrate delta (game result minus player's own baseline).
- Build-path splitting: cluster item sets per champion so AP Shaco and AD Shaco
  become separate "picks" (items are already stored).
- Draft-position / counterpick split if obtainable.

**v2 — role-aware modeling (top lane is different)**

Working hypothesis: top lane is more matchup-specific than other roles. Melee
tops must enter the enemy's threat range to farm, so every last hit is a trade,
and matchups hinge on several cross-cutting traits (true/%HP damage, sustain,
range, all-in power, tankiness, wave clear). Marksmen and mages differ on fewer
of these, so skills transfer (Caitlyn → Jinx). Machine LoLing's class
encyclopedia (below) found the same thing from data: support/ADC/mid cluster
cleanly, but top tanks and fighters correlate with mixed groups and only
Ranged and Duelist form clean classes.

- **Archetypes from Machine LoLing.** Use their Season 16 per-role classes
  (matchup + synergy delta profiles, WR-corrected, empirical-Bayes shrunk,
  correlation + Ward/SNN clustering) for support, ADC, mid, jungle. For top,
  don't force classes — lean on champion-specific estimates.
  https://machineloling.com/2026/04/13/the-definitive-season-16-champion-class-encyclopedia/
- **Role-specific priors.** Looser champion×role offset (and weaker
  archetype×role pooling) for TOP. Pick the SDs per role by held-out
  log-likelihood across patches.
- **Blind score vs counter score** (top first, then all roles). A single WR
  blends blind and counter contexts — e.g. Vayne top is coinflippy blind but
  strong into Gnar/Jayce. Report per champion:
  - *Blind score*: opponent-playrate-weighted mean matchup delta, minus
    penalties for matchup variance and bad worst cases (≈ blindability).
  - *Counter score*: best matchups weighted by how often those opponents are
    played — surfaces niche picks that punish popular blinds.
  Piggyback on Machine LoLing's blindability work: watch "The math behind
  champion blindability" (2026-10-30, video-only post) and align definitions
  so results are comparable, then extend with the counter score.
  https://machineloling.com/2026/10/30/the-math-behind-champion-blindability/
  Their data says Ahri (mid) is very blindable; in top essentially only Gnar
  is — his kit covers two axes (mini outranges melee, mega survives all-ins), so
  his counters beat him on both (Jayce, Vayne/Tristana top, Sylas).
- **Tests of the hypothesis** (good blog material: "why top lane breaks clustering"):
  1. Matchup variance by role — spread of shrunk champ-vs-champ deltas should be
     widest in top if top is more matchup-dependent.
  2. Hand-drawn groups (juggernauts: Darius/Mordekaiser/Garen vs skirmishers:
     Riven/Fiora) — is within-group matchup correlation clearly higher than
     between-group? If yes, the categories exist and clustering method/noise is
     the problem.
  3. Factor model (matrix factorization on matchup deltas) per role: how many
     dimensions does each role need? If top needs more and the axes are
     interpretable (anti-tank, extended fighter, ranged bully), that supports
     "top laners are mixtures of traits, not members of one class" — and the
     factor weights replace hard archetype tags as model input.

**v3 — richer identity**
- Matchup interactions (archetype × enemy-archetype in lane).
- Learned embeddings (matrix factorization on matchup outcomes) vs hand tags.
- Behavioral features per game (damage to champs, heals on allies, deaths) so a
  support-style Nidalee game looks like an enchanter game, not a jungle one.

**Headline evaluation for the write-up**
- Backtest on older patches: train before a known breakout off-role pick
  (verify which patches — candidates: Seraphine/Swain/Ziggs/Karthus bot) and check
  its rank while its playrate was still near zero. Match-v5 only serves recent
  history, so this likely needs an archived dataset — investigate sources.
- Before/after table: naive top-10 by raw WR vs model top-10.

## Outreach

Once there's a real backtest number, email Machine LoLing (contact on their
homepage). Lead with the result; reference their champion-identity post and the
commenter asking about cross-lane counters.
