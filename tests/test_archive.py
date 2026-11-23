"""Raw-JSON archiving: upload, verify, prune, and crash safety (fake rclone, no network)."""
import json
import sqlite3
import stat
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import archive_raw  # noqa: E402

from hiddenop.collector.storage import connect, known_match, store_match  # noqa: E402

FAKE_RCLONE = """#!{py}
# Minimal stand-in for rclone: 'copyto SRC DEST' and 'md5sum DEST', where DEST is a local path.
import hashlib, shutil, sys, os
cmd, args = sys.argv[1], [a for a in sys.argv[2:] if not a.startswith("--") and not a.isdigit()]
if cmd == "copyto":
    os.makedirs(os.path.dirname(args[1]), exist_ok=True)
    shutil.copyfile(args[0], args[1])
    if os.environ.get("FAKE_CORRUPT"):
        open(args[1], "ab").write(b"x")
elif cmd == "md5sum":
    print(hashlib.md5(open(args[0], "rb").read()).hexdigest(), os.path.basename(args[0]))
"""


def _match(mid):
    parts = [dict(puuid=f"{mid}-p{i}", teamId=100 if i < 5 else 200, championName="Ahri",
                  championId=103, teamPosition="MIDDLE", win=i < 5) for i in range(10)]
    return {"metadata": {"matchId": mid},
            "info": {"queueId": 420, "gameVersion": "16.22.1.1", "participants": parts}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    rc = tmp_path / "rclone"
    rc.write_text(FAKE_RCLONE.format(py=sys.executable))
    rc.chmod(rc.stat().st_mode | stat.S_IEXEC)
    db = tmp_path / "data" / "t.db"
    con = connect(db)
    for i in range(25):
        store_match(con, "na1", _match(f"NA1_{i}"), tier="DIAMOND")
    con.commit()
    remote = tmp_path / "drive" / "raw"
    args = ["--db", str(db), "--remote", str(remote), "--work-dir", str(tmp_path / "work"),
            "--chunk-size", "10", "--min-matches", "3", "--rclone", str(rc)]
    return con, remote, args, tmp_path


def test_archive_uploads_then_prunes(env):
    con, remote, args, _ = env
    assert archive_raw.main(args) == 25
    chunks = sorted(remote.glob("raw-*.db"))
    assert len(chunks) == 3  # 10 + 10 + 5
    # Every match's raw JSON is on the "remote", readable, and gone locally.
    ids = set()
    for c in chunks:
        for mid, raw in sqlite3.connect(c).execute("SELECT match_id, raw FROM raw"):
            assert json.loads(zlib.decompress(raw))["metadata"]["matchId"] == mid
            ids.add(mid)
    assert len(ids) == 25
    assert con.execute("SELECT COUNT(*) FROM matches WHERE raw IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM matches WHERE archive IS NULL").fetchone()[0] == 0
    # Slim tables and seen-match IDs stay, so nothing gets fetched twice.
    assert con.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 250
    assert known_match(con, "NA1_0")
    # Nothing left: a second run makes no chunks.
    assert archive_raw.main(args) == 0


def test_min_matches_waits_for_more(env):
    con, remote, args, _ = env
    args[args.index("--min-matches") + 1] = "100"
    assert archive_raw.main(args) == 0
    assert not remote.exists()


def test_bad_upload_keeps_local_raw(env, monkeypatch):
    con, remote, args, tmp = env
    monkeypatch.setenv("FAKE_CORRUPT", "1")
    with pytest.raises(RuntimeError, match="MD5 mismatch"):
        archive_raw.main(args)
    assert con.execute("SELECT COUNT(*) FROM matches WHERE raw IS NOT NULL").fetchone()[0] == 25
    # The chunk waits locally; the next run uploads it first, then carries on.
    assert len(list((tmp / "work").glob("raw-*.db"))) == 1
    monkeypatch.delenv("FAKE_CORRUPT")
    assert archive_raw.main(args) == 25
    assert con.execute("SELECT COUNT(*) FROM matches WHERE raw IS NOT NULL").fetchone()[0] == 0
