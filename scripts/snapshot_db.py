"""Copy a consistent snapshot of the database somewhere else, e.g. Google Drive.

Keep the live DB on a local disk: SQLite with several writers inside a folder
that Drive is syncing can corrupt or lock. Instead, snapshot it into the Drive
folder now and then. Safe to run while collectors are running.

    python scripts/snapshot_db.py --out "G:/My Drive/hidden-op"          # full copy
    python scripts/snapshot_db.py --out "G:/My Drive/hidden-op" --slim   # no raw JSON, much smaller

--slim keeps players, matches (without the raw JSON blob) and participants:
everything the model needs. The full copy also keeps the raw match JSON for
extracting new features later.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--out", required=True, help="folder to write the snapshot into")
    ap.add_argument("--slim", action="store_true", help="drop raw match JSON")
    args = ap.parse_args()

    src_path = Path(args.db)
    if not src_path.exists():
        sys.exit(f"No database at {src_path}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "hiddenop-slim.db" if args.slim else "hiddenop.db"
    final = out_dir / name
    # Build locally, then move into place in one step, so Drive never uploads a half-written file.
    tmp = src_path.parent / (name + ".snapshot-tmp")
    tmp.unlink(missing_ok=True)

    src = sqlite3.connect(src_path, timeout=120)
    dst = sqlite3.connect(tmp)
    src.backup(dst)  # consistent point-in-time copy, even with collectors writing
    src.close()
    if args.slim:
        dst.execute("UPDATE matches SET raw = NULL")
        dst.commit()
        dst.execute("VACUUM")
    dst.execute("PRAGMA journal_mode=DELETE")  # single self-contained file
    n = dst.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    dst.close()

    os.replace(tmp, final) if tmp.drive == final.drive else _copy_then_remove(tmp, final)
    print(f"{n:,} matches -> {final} ({final.stat().st_size / 1e9:.2f} GB)")


def _copy_then_remove(tmp: Path, final: Path) -> None:
    import shutil
    part = final.with_name(final.name + ".part")
    shutil.copyfile(tmp, part)
    os.replace(part, final)
    tmp.unlink()


if __name__ == "__main__":
    main()
