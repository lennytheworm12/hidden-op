# Handoff: hidden-op

Read this first if you're a new person or Claude Code session picking up the
project. It covers what the project is, where it stands, and what to do next.

## What this project is

**hidden-op finds League of Legends picks that are strong but rarely played**:
champion + role combos (and eventually champion + build) whose true strength is
high while their playrate is low. Examples of the kind of pick it's hunting for:
Singed mid, Vel'Koz support, AP Shaco jungle, off-role bot laners like Seraphine
or Swain before they went mainstream, and build discoveries like "blue Ezreal".

**Why raw winrate doesn't work:** rare picks have tiny samples, so their winrate
is mostly noise. They are also played mainly by one-tricks, which inflates their
numbers. A 58% winrate over 40 games tells you almost nothing.

**The approach:** a partially pooled (hierarchical) logistic model. Each
champion-role estimate borrows strength from the champion's other roles and from
its archetype in that role, and a playrate term absorbs the one-trick bias:

```
logit P(win) = role + champion + archetype×role + champion×role offset + β·log(playrate)
```

The pick's "true strength" is the predicted winrate with the playrate term set to
the role's typical level (`adj_wr`). A pick is flagged **hidden OP** when its 90%
interval sits entirely above 50% and its playrate is under 1%.

## Main goals

1. **Rank hidden-OP candidates** from real ranked data, with honest uncertainty.
   When the data can't support a call (e.g. support Nidalee plays nothing like
   jungle Nidalee), the model should say "uncertain" rather than guess.
2. **Look below Master.** Niche builds and off-role picks are often found by
   lower-elo players (Emerald/Diamond) before high elo adopts them. Collect
   several rank brackets and compare.
3. **Prove it works:** a backtest showing the model would have flagged a known
   breakout pick *before* it went mainstream, plus a before/after table of naive
   top-10 by raw winrate vs model top-10.
4. **Share it:** once there's a real backtest number, write it up and contact
   Machine LoLing (see Outreach).

## Current status (as of 2026-11-23)

- **Code:** working v1 on branch **`elo-brackets`** (not yet merged into `main`;
  `main` lacks the rank-bracket changes). Use that branch.
- **Tests:** `python -m pytest` passes 8 checks: one collector check with a fake
  client, and 7 on a simulated world: a planted
  hidden pick is flagged, a playrate-inflated decoy isn't, adjusted WR tracks
  ground truth far better than raw WR (r≈0.89 vs 0.41), ≥80% of flagged picks are
  truly positive, the playrate bias is recovered, unseen combos get wide
  intervals, and the backtest runs.
- **Real API smoke test passed (2026-11-02, na1, patch 16.21):** league entries
  include `puuid`, every `championName` matched Data Dragon ids, `teamPosition` was
  filled for every player, and the collector ran end to end with no fixes. A
  short tiered crawl got matches from every bracket (Challenger 9, GM 12,
  Master 9, Diamond 6, Emerald 4 out of 40).
- **Real data collected so far: none kept.** The test crawls lived in a temporary
  cloud container and are gone.
- **API key:** the owner now has a Riot key for a registered dev app (no 24-hour
  expiry). It's kept locally, never in git or chat.
- **Model has not yet been run on real data.** That's the next milestone.

## Where to run things

- **Collector: run on the owner's PC**, not in a Claude cloud session. Cloud
  containers get wiped when idle, background commands stop after 2 hours, and the
  database (~1 GB/day per region) is too big for git. On a PC it can run for
  days, and it resumes where it left off because progress is saved in the DB.
- **Code changes and analysis:** any Claude Code session (cloud or local). Push
  code to GitHub, pull on the PC.
- Cloud sessions that need the API must allow the Riot hosts
  (`na1/euw1/kr/americas/europe/asia.api.riotgames.com`) and
  `ddragon.leagueoflegends.com` in the environment's network settings, and get
  the key via the environment's secrets. Never paste it into chat.

## How to run

On the owner's Windows PC, in PowerShell:

```powershell
git clone https://github.com/lennytheworm12/hidden-op; cd hidden-op
git checkout elo-brackets
pip install -r requirements.txt
python -m pytest                     # 8 checks, no key needed
# Save the key to .env (gitignored). It prompts, so the key never lands in shell history.
$k = Read-Host "Riot API key"; Set-Content .env "RIOT_API_KEY=$k"
powercfg /change standby-timeout-ac 0   # stop the PC sleeping while plugged in

# One terminal per region, all in the hidden-op folder. They share one DB safely.
python -m hiddenop.collector.collect --platform na1  --days 14 --loop --max-matches 2000000
python -m hiddenop.collector.collect --platform euw1 --days 14 --loop --max-matches 2000000
python -m hiddenop.collector.collect --platform kr   --days 14 --loop --max-matches 2000000

# Or start all three in their own windows with one command:
powershell -ExecutionPolicy Bypass -File scripts\start_collectors.ps1

# Any time, from a fourth terminal:
python scripts/db_status.py          # matches per region/tier/patch, DB size
```

The key is read from `RIOT_API_KEY` if set, else from `.env` in the folder you
run from. Ctrl+C stops a collector; rerun the same command to resume (progress is
in the DB; add `--skip-seed` to skip re-fetching ladders). With `--loop` it keeps
doing passes over the ladder, re-seeds daily, and sleeps 30 min when a pass finds
nothing new. Network drops and Riot 5xx are retried with backoff, then skipped,
so a long run doesn't die on one bad request. A rejected key (401/403) stops it.

**Shutdowns and crashes are safe.** Each match is committed on its own, so a
kill or power cut loses at most the match in flight; SQLite rolls back the
partial write, and the next run skips stored matches and continues with players
not yet crawled. (Tested by killing a crawl 3 times mid-run: integrity check ok,
no duplicates, no gaps.) To restart automatically after a reboot, register
`scripts/start_collectors.ps1` to run at logon (command at the top of that file).

Disk: about 1 GB/day per region, so ~40 GB for two weeks of three regions.

**Storage: live DB local, backups on Google Drive.** Keep `data/hiddenop.db` on
the PC's own disk, not in a Drive/OneDrive folder: three processes writing SQLite
inside a syncing folder risks locks and corruption, and Drive would keep
re-uploading a growing multi-GB file. Google Drive (owner has 2 TB) is the default
backup target: `snapshot_db.py` finds the Google Drive for Desktop folder and
writes a consistent copy to `My Drive/hidden-op/`, safe while collecting.

```powershell
python scripts/snapshot_db.py --slim   # full hiddenop.db + small hiddenop-slim.db (no raw JSON)

# Run it automatically every day at 4 AM (from the hidden-op folder):
$a = New-ScheduledTaskAction -Execute (Get-Command python).Source -Argument "scripts\snapshot_db.py --slim" -WorkingDirectory (Get-Location).Path
Register-ScheduledTask -TaskName "hidden-op backup" -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 4am)
```

Override the location with `--out FOLDER` or `BACKUP_DIR=...` in `.env`. The slim
copy has everything the model needs and is what a cloud session should download.

After roughly one patch of data:

```bash
python scripts/run_model.py --tiers EMERALD           # one bracket at a time
python scripts/run_model.py --tiers MASTER+
python scripts/run_model.py --score-pair Sylas:BOTTOM --score-pair Nidalee:UTILITY
python scripts/run_model.py --synthetic               # demo, no key needed
```

Useful collector options: `--tiers` (default `MASTER+,DIAMOND,EMERALD`; e.g. add
`PLATINUM`), `--pages-per-division` (~205 players per page, default 3),
`--per-player` (match IDs per player per pass, default 10, keeps brackets
balanced), `--max-matches`, `--db` (default `data/hiddenop.db`).

Throughput: ~100 requests / 2 min per region ⇒ roughly 70k matches/day/region.

## What data is stored (SQLite, `data/hiddenop.db`)

- `players`: puuid, platform, tier, last crawled.
- `matches`: id, platform, patch, start time, duration, **tier** (tier of the
  seed player it was found through), and the **full raw match JSON, compressed**.
- `participants`: one row per player per match with champion, role
  (`teamPosition`), win, **final items** (6 slots + trinket), damage to
  champions, healing and shielding on allies, and deaths.
- Everything else in the match JSON (runes, summoner spells, gold, CS, vision,
  KDA, ...) is in the raw blob and can be extracted later without new API calls.
- **Not collected:** build order and skill order. Those need the timeline
  endpoint, which costs one extra call per match (it would halve throughput).
  Final items are enough to detect build variants like blue Ezreal.

## Code map

| Path | What |
|---|---|
| `hiddenop/collector/riot_client.py` | Riot API client, two-window rate limiter per host, retries on 429/5xx |
| `hiddenop/collector/collect.py` | Seeds players by tier, crawls round-robin across tiers |
| `hiddenop/collector/storage.py` | SQLite schema, raw-JSON storage, auto-migration for old DBs |
| `hiddenop/aggregate.py` | Participants → champion × role × patch table (filterable by tier) |
| `hiddenop/archetypes.py` | Champion → archetype from Data Dragon tags + CSV overrides |
| `hiddenop/model.py` | Hierarchical logistic model, MAP + Laplace intervals |
| `hiddenop/score.py` | Ranking and hidden-OP flags, plus naive raw-WR columns |
| `hiddenop/backtest.py` | Walk-forward: do flagged picks keep winning next patch? |
| `hiddenop/simulate.py` | Synthetic world with planted hidden and decoy picks (for tests) |
| `scripts/run_model.py` | Fit on the DB and write `results/scores.csv` |
| `scripts/snapshot_db.py` | Consistent copy of the DB to another folder (e.g. Drive); `--slim` drops raw JSON |
| `scripts/db_status.py` | Progress report on the collected data (safe while collecting) |
| `tests/test_model_recovery.py` | The 7 synthetic checks |
| `tests/test_collector.py` | Collector check with a fake client |

## Design decisions

- **Model = MAP + Laplace, not MCMC.** ~750 parameters, fits in under a second.
  Good enough for intervals. For learned prior widths, move to PyMC/NumPyro or
  tune `Priors` by held-out log-likelihood across patches.
- **Strength excludes the playrate term.** Caveat: the playrate slope mixes two
  opposite effects, one-trick inflation (niche looks better) and "strong picks
  get popular" (popular looks better). β is the net effect. The mastery
  correction (v2) is what actually separates them.
- **Priors (logit scale):** champion 0.15, archetype×role 0.15, champion×role
  offset 0.10. The tight offset is what lets Sylas BOTTOM borrow from Sylas
  overall and from Mage-in-bot.
- **Rank = seed player's tier; fit one bracket at a time.** Match-v5 has no rank
  field, so each match is labelled by the tier of the player it was found
  through (the other nine are usually nearby). Baseline winrates and playrate
  bias differ by rank, so brackets aren't mixed.
- **Archetypes = Data Dragon primary tag** (Fighter, Tank, Mage, Assassin,
  Marksman, Support). Coarse but objective. Override in
  `data/archetypes_override.csv`.
- **Raw JSON is kept** because API budget is the scarce resource, not disk.

## Next steps, in order

1. **Collect** about one patch of data on the PC (na1, euw1, kr).
2. **First real run:** fit per bracket and sanity-check the top of each table
   against game knowledge. Expect to tune `--max-playrate` and the minimum games.
3. **Build splitting:** cluster final item sets per champion so AP vs AD Shaco,
   or blue vs standard Ezreal, become separate "picks". The data is already stored.
4. **One-trick/mastery correction:** for low-playrate candidates, pull
   champion-mastery-v4 for their players and compare at matched experience
   (winrate delta = result minus the player's own baseline).
5. **Pooled ranks:** one model with a champion×role×bracket offset, so strength
   seen in Emerald informs Master with shrinkage, instead of fully separate fits.
6. **Headline backtest:** train on patches before a known breakout off-role pick
   (candidates to verify: Seraphine/Swain/Ziggs/Karthus bot) and check its rank
   while its playrate was near zero. Match-v5 only serves recent history, so this
   probably needs an archived dataset. Finding a source is an open task.

### Role-aware modeling (from `main`'s handoff, not yet scheduled)

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
  Piggyback on Machine LoLing's blindability work. Their Champion Pool Designer
  (https://pooldesigner.machineloling.com) is what the blindability video
  (https://machineloling.com/2026/10/30/the-math-behind-champion-blindability/)
  is based on. What the tool page states (formula itself not published):
  - Blindability = an aggregate z-score of how consistently a champion performs
    across opponents *and partners* (so it includes synergy, not just lane
    matchups); "polarized" champions score low. Shared z-scale across the tool.
  - Matchup/synergy Δs: Diamond+, patches 16.1–16.4, WR-corrected (champion's
    overall WR removed). Opponent/partner pick-rate floor 0.5%; optional
    pick-rate weighting. Playrates from a later patch across Silver–Master+.
  - No separate counter metric — their matchup Δ plays that role. So our
    *counter score* (playrate-weighted best matchups) is a genuine extension.
  - Individual Champ Compare reports Pearson similarity of Δ vectors (matchup,
    synergy, total) plus Blind Δz — useful for validating our archetypes.
  To do: watch the video for the exact formula and match it; compare our blind
  score against their Blind Δz on shared champions as a sanity check.
  Data access: the UI runs on a live API but there's no documented export.
  Read their "Terms and data license" page before pulling any numbers from it;
  otherwise compute our own Δs from collected matches (same recipe as above).
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

Later (v3): matchup interactions (archetype vs enemy archetype in lane), learned
champion embeddings from matchup outcomes instead of hand tags, and per-game
behavior features (damage, healing, deaths) so a support-style Nidalee game looks
like an enchanter game rather than a jungle one.

## Open questions

- Which archived dataset could cover older patches for the backtest?
- Right playrate cutoff for "hidden" (1% now). It may need to differ by role or rank.
- Minimum sample before a pick is shown at all, vs. just showing wide intervals.

## Outreach

Once there's a real backtest number, email Machine LoLing (contact on their
homepage, machineloling.com). Lead with the result. Reference their
champion-identity post (their embeddings are per-role and remove the strength
direction; this project is cross-role and strength-focused) and the commenter who
asked about cross-lane counters.
