"""Champion -> archetype mapping.

v1 uses Riot's own class tags from Data Dragon (first tag = primary class:
Fighter, Tank, Mage, Assassin, Marksman, Support). It's coarse — Lux and Brand
are both 'Mage' — but it's objective and a fine baseline.

Override any champion by editing data/archetypes_override.csv
(columns: champion,archetype). Good next steps: Machine LoLing's per-role
clusters, or archetypes learned from matchup data (see HANDOFF.md).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

DDRAGON = "https://ddragon.leagueoflegends.com"


def fetch_ddragon_tags(cache: str | Path = "data/ddragon_champions.json") -> dict[str, str]:
    cache = Path(cache)
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        version = requests.get(f"{DDRAGON}/api/versions.json", timeout=10).json()[0]
        data = requests.get(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json", timeout=10).json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    # Match-v5 championName matches Data Dragon's champion id (e.g. 'MonkeyKing').
    return {cid: c["tags"][0] for cid, c in data["data"].items()}


def load_archetypes(override_csv: str | Path = "data/archetypes_override.csv") -> dict[str, str]:
    arch = fetch_ddragon_tags()
    p = Path(override_csv)
    if p.exists():
        for row in pd.read_csv(p).itertuples():
            arch[row.champion] = row.archetype
    return arch
