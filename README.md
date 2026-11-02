# hidden-op

Finding statistically underrated League of Legends picks — champion/role combos
that are genuinely strong but rarely played (think Singed mid, Vel'Koz support,
AP Shaco jungle).

Raw winrate is a bad signal for rare picks: tiny samples are noisy, and niche
picks are played mostly by mains, which inflates their numbers. This project fits
a **partially pooled logistic model** so each champion-role estimate borrows
strength from the champion's other roles and from its archetype in that role,
then corrects for the playrate bias.

```
logit P(win) = role + champion + archetype×role + champion×role offset + β·log(playrate)
```

A pick is flagged **hidden OP** when its playrate-adjusted 90% interval sits
entirely above 50% and its playrate is under 1%.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                              # model checks on synthetic data
python scripts/run_model.py --synthetic       # end-to-end demo, no API key

cp .env.example .env                          # add your Riot dev key
export $(cat .env | xargs)
python -m hiddenop.collector.collect --platform na1 --days 14
python scripts/run_model.py --score-pair Sylas:BOTTOM --score-pair Nidalee:UTILITY
```

## Layout

| Path | What |
|---|---|
| `hiddenop/collector/` | Rate-limited Riot API client, Master+/Diamond I crawler, SQLite storage |
| `hiddenop/aggregate.py` | Participants → champion × role × patch table |
| `hiddenop/archetypes.py` | Champion → archetype (Data Dragon tags + CSV overrides) |
| `hiddenop/model.py` | Hierarchical logistic model, MAP + Laplace intervals |
| `hiddenop/score.py` | Ranking and hidden-OP flags |
| `hiddenop/backtest.py` | Walk-forward: do flagged picks keep winning next patch? |
| `hiddenop/simulate.py` | Synthetic world with planted hidden and decoy picks |

See [HANDOFF.md](HANDOFF.md) for design decisions and the roadmap.

Related work: [Machine LoLing](https://machineloling.com) — especially their
champion-identity embeddings, which are per-role and remove the strength
direction. This project goes the other way: cross-role, strength-focused.
