"""Back up a consistent snapshot of the database to Google Drive (or any folder).

Keep the live DB on a local disk: SQLite with several writers inside a folder
that Drive is syncing can corrupt or lock. Instead, snapshot it into the Drive
folder now and then. Safe to run while collectors are running.

    python scripts/snapshot_db.py           # full copy to Google Drive/hidden-op
    python scripts/snapshot_db.py --slim    # also write a small copy without raw JSON
    python scripts/snapshot_db.py --out D:/backups

Where it goes, in order: --out, BACKUP_DIR in the environment or .env, then
the Google Drive for Desktop folder found automatically, plus "hidden-op".

--slim keeps players, matches (without the raw JSON blob) and participants:
everything the model needs. The full copy also keeps the raw match JSON for
extracting new features later.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hiddenop.collector.riot_client import dotenv_value  # noqa: E402


def find_google_drive() -> Path | None:
    """Google Drive for Desktop's 'My Drive' folder, if it's mounted."""
    home = Path.home()
    candidates = [Path(f"{d}:/My Drive") for d in "GHIJKLMNOPQRSTUVWXYZDEF"]
    candidates += [home / "Google Drive" / "My Drive", home / "My Drive", home / "Google Drive"]
    candidates += sorted(home.glob("Library/CloudStorage/GoogleDrive-*/My Drive"))  # macOS
    return next((c for c in candidates if c.is_dir()), None)


def backup_dir(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    env = os.environ.get("BACKUP_DIR") or dotenv_value("BACKUP_DIR")
    if env:
        return Path(env)
    drive = find_google_drive()
    if drive is None:
        sys.exit("Couldn't find Google Drive. Is Google Drive for Desktop running? "
                 "Or pass --out FOLDER, or set BACKUP_DIR in .env.")
    return drive / "hidden-op"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--out", default=None, help="folder to write into (default: Google Drive/hidden-op)")
    ap.add_argument("--slim", action="store_true",
                    help="also write hiddenop-slim.db without raw match JSON (small, for cloud sessions)")
    args = ap.parse_args()

    src_path = Path(args.db)
    if not src_path.exists():
        sys.exit(f"No database at {src_path}")
    out_dir = backup_dir(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot(src_path, out_dir / "hiddenop.db", slim=False)
    if args.slim:
        snapshot(src_path, out_dir / "hiddenop-slim.db", slim=True)


def snapshot(src_path: Path, final: Path, slim: bool) -> None:
    # Build locally, then move into place in one step, so Drive never uploads a half-written file.
    tmp = src_path.parent / (final.name + ".snapshot-tmp")
    tmp.unlink(missing_ok=True)

    src = sqlite3.connect(src_path, timeout=120)
    dst = sqlite3.connect(tmp)
    src.backup(dst)  # consistent point-in-time copy, even with collectors writing
    src.close()
    if slim:
        dst.execute("UPDATE matches SET raw = NULL")
        dst.commit()
        dst.execute("VACUUM")
    dst.execute("PRAGMA journal_mode=DELETE")  # single self-contained file
    n = dst.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    dst.close()

    if tmp.drive == final.drive:
        os.replace(tmp, final)
    else:
        _copy_then_remove(tmp, final)
    print(f"{n:,} matches -> {final} ({final.stat().st_size / 1e9:.2f} GB)")


def _copy_then_remove(tmp: Path, final: Path) -> None:
    import shutil
    part = final.with_name(final.name + ".part")
    shutil.copyfile(tmp, part)
    os.replace(part, final)
    tmp.unlink()


if __name__ == "__main__":
    main()
