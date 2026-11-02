"""Fit the model on collected data and write the ranked table.

    python scripts/run_model.py --db data/hiddenop.db --patches 3 --out results/scores.csv
    python scripts/run_model.py --synthetic        # no API key needed

Extra off-role combos to score even with zero games:
    --score-pair Sylas:BOTTOM --score-pair Nidalee:UTILITY
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from hiddenop.aggregate import add_playrate, champ_role_table, patch_key  # noqa: E402
from hiddenop.model import fit  # noqa: E402
from hiddenop.score import score_table  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--patches", type=int, default=3, help="train on the last N patches")
    ap.add_argument("--max-playrate", type=float, default=0.01)
    ap.add_argument("--score-pair", action="append", default=[], help="Champion:ROLE")
    ap.add_argument("--out", default="results/scores.csv")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        from hiddenop.simulate import simulate
        df, _, arch = simulate()
        df = add_playrate(df)
    else:
        from hiddenop.archetypes import load_archetypes
        from hiddenop.collector.storage import connect
        df = champ_role_table(connect(args.db))
        keep = sorted(df.patch.unique(), key=patch_key)[-args.patches:]
        df = df[df.patch.isin(keep)]
        arch = load_archetypes()

    model = fit(df, arch)
    pairs = [tuple(p.split(":")) for p in args.score_pair]
    table = score_table(model, df, max_playrate=args.max_playrate, extra_pairs=pairs)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    pd.set_option("display.width", 140)
    print("Hidden OP candidates:")
    print(table[table.hidden_op].head(20).to_string(index=False))
    if pairs:
        print("\nRequested pairs:")
        print(table[table[["champion", "role"]].apply(tuple, axis=1).isin(pairs)].to_string(index=False))
    print(f"\nFull table -> {args.out}")


if __name__ == "__main__":
    main()
