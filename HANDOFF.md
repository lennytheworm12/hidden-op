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
- **Smoke-tested against the real Riot API (2026-11-02, na1, patch 16.21):**
  league-v4 entries include `puuid` (Challenger and Diamond/Emerald pages), match-v5
  `championName` values all matched Data Dragon ids, `teamPosition` was populated
  for every participant, and the collector ran end-to-end with no code fixes.
- **Rank brackets:** the crawl now covers Master+, Diamond and Emerald by default
  (`--tiers`), round-robin across tiers with a per-player cap (`--per-player`) so
  the 10k-player Master ladder doesn't drown out lower brackets. Off-meta builds
  often surface below Master. Each match is tagged with the seed player's tier;
  fit one bracket at a time with `run_model.py --tiers EMERALD` etc.
- Cloud sessions need `*.api.riotgames.com` and `ddragon.leagueoflegends.com`
  allowed in the environment's network settings, and `RIOT_API_KEY` set there.

## First steps on a real machine

1. `cp .env.example .env`, add a dev key from developer.riotgames.com
   (expires every 24h — regenerate daily, or register a personal key).
2. ~~Smoke-test the client~~ — done, all checks passed (see Status).
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
- **Rank = seed player's tier, fit per bracket.** Match-v5 has no rank field, so a
  match is labelled by the tier of the player we found it through (the other 9
  are usually nearby). Brackets are fit separately because baseline winrates and
  playrate bias differ by rank. A pooled model with a champ×role×bracket offset
  (strength in Emerald informs Master, with shrinkage) is the natural v2 upgrade.
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
