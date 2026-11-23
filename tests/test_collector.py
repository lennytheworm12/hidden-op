"""Collector checks with a fake Riot client (no network)."""
from hiddenop.collector import collect
from hiddenop.collector.riot_client import GaveUp
from hiddenop.collector.storage import connect


def _match(mid, queue=420):
    parts = [dict(puuid=f"{mid}-p{i}", teamId=100 if i < 5 else 200, championName="Ahri",
                  championId=103, teamPosition="MIDDLE", win=i < 5) for i in range(10)]
    return {"metadata": {"matchId": mid},
            "info": {"queueId": queue, "gameVersion": "16.22.1.1", "participants": parts}}


class FakeClient:
    def __init__(self):
        self.ids = {"a": ["M1", "M2", "BAD"], "b": ["M2", "M3", "ARAM"]}

    def match_ids(self, platform, puuid, start_time=None, count=10):
        return self.ids[puuid][:count]

    def match(self, platform, mid):
        if mid == "BAD":
            raise GaveUp("simulated outage")
        return _match(mid, queue=450 if mid == "ARAM" else 420)


def test_crawl_skips_failures_and_dedupes(tmp_path):
    con = connect(tmp_path / "t.db")
    con.executemany("INSERT INTO players(puuid, platform, tier) VALUES (?,?,?)",
                    [("a", "na1", "EMERALD"), ("b", "na1", "MASTER")])
    got = collect.crawl(FakeClient(), con, "na1", days=14, max_matches=100)
    assert got == 3  # M1, M2, M3; BAD skipped, ARAM filtered, M2 not stored twice
    assert con.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 30
    assert con.execute("SELECT COUNT(*) FROM players WHERE last_crawled > 0").fetchone()[0] == 2
    # A second connection (another region's collector) can write while the first is open.
    other = connect(tmp_path / "t.db")
    other.execute("INSERT INTO players(puuid, platform, tier) VALUES ('c','kr','DIAMOND')")
    other.commit()


class FakeLadder:
    def apex_league(self, platform, tier):
        return {"entries": [{"puuid": "x", "leaguePoints": 812, "wins": 120, "losses": 90}]}

    def league_entries(self, platform, tier, div, page):
        if div != "I" or page > 1:
            return []
        return [{"puuid": "y", "rank": "I", "leaguePoints": 55, "wins": 30, "losses": 25}]


def test_seed_keeps_ladder_snapshot(tmp_path):
    con = connect(tmp_path / "t.db")
    assert collect.seed_players(FakeLadder(), con, "na1", ["MASTER", "EMERALD"], 1) == 2
    rows = dict((r[0], r[1:]) for r in con.execute(
        "SELECT puuid, tier, division, lp, wins, losses FROM ladder_snapshots"))
    assert rows == {"x": ("MASTER", "", 812, 120, 90), "y": ("EMERALD", "I", 55, 30, 25)}
