"""Move raw match JSON off the server to Google Drive, then free it locally.

    python scripts/archive_raw.py --remote gdrive:hidden-op/raw

Raw match JSON is ~90% of the database. The model only needs the slim tables
(players, matches without raw, participants), so on a small server we ship the
raw JSON to Drive in chunks and drop it locally. Match IDs stay in `matches`,
so the collector never downloads a match twice.

Each run:
  1. Finishes any chunk left over from a crashed run (upload, verify, prune).
  2. Copies up to --chunk-size matches that still have raw JSON into a new
     chunk file data/archive/raw-<UTC time>-<random>.db (SQLite: match_id, platform,
     patch, tier, game_start, raw = zlib(json), same as matches.raw).
  3. Uploads it with rclone and checks the MD5 that Drive reports against the
     local file.
  4. Only then sets matches.raw = NULL and matches.archive = <chunk name> for
     exactly the IDs in that chunk, and deletes the local chunk file.
  Repeats until fewer than --min-matches matches are left with raw JSON.

Safe while collectors run. A crash at any point loses nothing: the worst case
is a chunk uploaded twice. SQLite reuses the freed pages for new matches, so
the DB file stops growing quickly instead of shrinking (no VACUUM needed).

To read raw data later, download chunks from Drive and open them with sqlite3:
    json.loads(zlib.decompress(raw)) for each row of the `raw` table.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hiddenop.collector.storage import connect  # noqa: E402

log = logging.getLogger("archive")

CHUNK_SCHEMA = """
CREATE TABLE raw (
    match_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    patch TEXT NOT NULL,
    tier TEXT,
    game_start INTEGER,
    raw BLOB NOT NULL              -- zlib(json), same as matches.raw
);
"""


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_chunk(con: sqlite3.Connection, out_dir: Path, limit: int) -> Path | None:
    """Copy up to `limit` not-yet-archived raw matches into a new chunk file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Random suffix: a name must never repeat, or an upload would overwrite an
    # earlier chunk on Drive whose matches were already pruned locally.
    name = time.strftime("raw-%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{secrets.token_hex(4)}"
    path = out_dir / f"{name}.db"
    tmp = path.with_suffix(".db.tmp")
    chunk = sqlite3.connect(tmp)
    chunk.executescript(CHUNK_SCHEMA)
    chunk.commit()
    chunk.close()

    con.execute("ATTACH DATABASE ? AS chunk", (str(tmp),))
    try:
        con.execute(
            """INSERT INTO chunk.raw(match_id, platform, patch, tier, game_start, raw)
               SELECT match_id, platform, patch, tier, game_start, raw FROM main.matches
               WHERE raw IS NOT NULL ORDER BY rowid LIMIT ?""", (limit,))
        con.commit()
    finally:
        con.execute("DETACH DATABASE chunk")

    chunk = sqlite3.connect(tmp)
    rows = chunk.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    ok = chunk.execute("PRAGMA integrity_check").fetchone()[0]
    chunk.close()
    if ok != "ok":
        raise RuntimeError(f"chunk {tmp} failed integrity check: {ok}")
    if rows == 0:
        tmp.unlink()
        return None
    tmp.rename(path)  # only finished chunks get the .db name
    log.info("wrote %s (%d matches, %.0f MB)", path.name, rows, path.stat().st_size / 1e6)
    return path


def upload_and_verify(path: Path, remote: str, rclone: str) -> None:
    """rclone copy to remote/<name>, then compare the remote MD5 with ours."""
    dest = f"{remote.rstrip('/')}/{path.name}"
    local = md5(path)
    subprocess.run([rclone, "copyto", str(path), dest, "--retries", "5"], check=True)
    out = subprocess.run([rclone, "md5sum", dest], check=True, capture_output=True, text=True).stdout
    remote_md5 = out.split()[0] if out.strip() else ""
    if remote_md5.lower() != local:
        raise RuntimeError(f"MD5 mismatch for {dest}: local {local}, remote {remote_md5 or 'missing'}")
    log.info("uploaded and verified %s (md5 %s)", dest, local)


def prune(con: sqlite3.Connection, path: Path, batch: int = 2000) -> int:
    """Drop local raw JSON for exactly the matches stored in this chunk."""
    chunk = sqlite3.connect(path)
    ids = [r[0] for r in chunk.execute("SELECT match_id FROM raw")]
    chunk.close()
    for i in range(0, len(ids), batch):  # short write transactions; collectors keep writing
        part = ids[i:i + batch]
        con.executemany("UPDATE matches SET raw = NULL, archive = ? WHERE match_id = ?",
                        [(path.stem, mid) for mid in part])
        con.commit()
    return len(ids)


def finish(con, path: Path, remote: str, rclone: str) -> int:
    upload_and_verify(path, remote, rclone)
    n = prune(con, path)
    path.unlink()
    log.info("pruned raw JSON for %d matches", n)
    return n


def pending(con) -> int:
    return con.execute("SELECT COUNT(*) FROM matches WHERE raw IS NOT NULL").fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/hiddenop.db")
    ap.add_argument("--remote", required=True, help="rclone destination, e.g. gdrive:hidden-op/raw")
    ap.add_argument("--work-dir", default="data/archive", help="where chunk files are built")
    ap.add_argument("--chunk-size", type=int, default=25_000, help="matches per chunk (~0.3-0.5 GB)")
    ap.add_argument("--min-matches", type=int, default=1000,
                    help="don't make a chunk smaller than this; wait for the next run")
    ap.add_argument("--rclone", default="rclone")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    con = connect(args.db)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    for tmp in work.glob("*.db.tmp"):  # half-built chunk from a crash: never uploaded, rebuild
        tmp.unlink()

    total = 0
    for leftover in sorted(work.glob("raw-*.db")):
        log.info("finishing leftover chunk %s", leftover.name)
        total += finish(con, leftover, args.remote, args.rclone)

    while pending(con) >= args.min_matches:
        path = make_chunk(con, work, args.chunk_size)
        if path is None:
            break
        total += finish(con, path, args.remote, args.rclone)

    log.info("done: archived %d matches; %d still have raw JSON locally", total, pending(con))
    return total


if __name__ == "__main__":
    main()
