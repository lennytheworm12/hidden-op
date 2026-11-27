"""Collector checks with a fake Riot client (no network)."""
from hiddenop.collector import collect
from hiddenop.collector.riot_client import GaveUp
from hiddenop.collector.storage import connect, mastery_of


def _timeline(mid):
    ev = [{"type": "SKILL_LEVEL_UP", "participantId": 1, "skillSlot": s, "levelUpType": "NORMAL",
           "timestamp": 1000 * k} for k, s in enumerate([1, 3, 2, 1])]
    ev += [{"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 1056, "timestamp": 5000},
           {"type": "ITEM_UNDO", "participantId": 1, "beforeId": 1056, "afterId": 0, "timestamp": 6000},
           {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3070, "timestamp": 7000}]
    frames = [{"participantFrames": {str(p): {"totalGold": 500 + m * 300, "minionsKilled": m * 8,
                                              "jungleMinionsKilled": 0, "xp": m * 400}
                                     for p in range(1, 11)}, "events": ev if m == 0 else []}
              for m in range(16)]
    return {"metadata": {"participants": [f"{mid}-p{i}" for i in range(10)]},
            "info": {"frames": frames}}


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

    def timeline(self, platform, mid):
        if mid == "M3":
            raise GaveUp("timeline outage")
        return _timeline(mid)

    def has_capacity(self, host):
        return True

    def mastery_all(self, platform, puuid):
        return [{"championId": 103, "championPoints": 250000, "championLevel": 30,
                 "lastPlayTime": 1_700_000_000_000}]


def test_crawl_skips_failures_and_dedupes(tmp_path):
    con = connect(tmp_path / "t.db")
    con.executemany("INSERT INTO players(puuid, platform, tier) VALUES (?,?,?)",
                    [("a", "na1", "EMERALD"), ("b", "na1", "MASTER")])
    got = collect.crawl(FakeClient(), con, "na1", days=14, max_matches=100)
    assert got == 3  # M1, M2, M3; BAD skipped, ARAM filtered, M2 not stored twice
    assert con.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 30
    assert con.execute("SELECT COUNT(*) FROM players WHERE last_crawled > 0").fetchone()[0] == 2
    # Timelines: M1 and M2 extracted; M3's timeline failed but the match was kept.
    assert con.execute("SELECT COUNT(DISTINCT match_id) FROM timelines").fetchone()[0] == 2
    items, skills, g10, cs15 = con.execute(
        "SELECT items, skills, gold10, cs15 FROM timelines WHERE puuid='M1-p0'").fetchone()
    assert skills == "QEWQ" and g10 == 3500 and cs15 == 120
    assert items == '[[5,1056,"B"],[6,1056,"U"],[7,3070,"B"]]'
    # Mastery: every participant fetched once, queue drained, lookup works.
    assert con.execute("SELECT COUNT(*) FROM mastery").fetchone()[0] == 30
    assert con.execute("SELECT COUNT(*) FROM mastery_todo").fetchone()[0] == 0
    assert mastery_of(con, "M1-p3", 103) == (250000, 30, 1_700_000_000)
    assert mastery_of(con, "M1-p3", 1) == (0, 0, 0)
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


def test_seed_all_pages_and_retier(tmp_path):
    class Paged(FakeLadder):
        def league_entries(self, platform, tier, div, page):
            if div != "I" or page > 3:
                return []
            return [{"puuid": f"y{page}", "rank": "I", "leaguePoints": 1, "wins": 1, "losses": 1}]
    con = connect(tmp_path / "t.db")
    assert collect.seed_players(Paged(), con, "na1", ["EMERALD"]) == 3  # 0 = every page
    assert collect.seed_players(Paged(), con, "na1", ["DIAMOND"], 1) == 1
    assert con.execute("SELECT tier FROM players WHERE puuid='y1'").fetchone()[0] == "DIAMOND"


def test_cap_games_per_player(tmp_path):
    import time
    now_ms = int(time.time() * 1000)

    class Busy(FakeClient):
        def __init__(self):
            self.ids = {"a": [f"A{i}" for i in range(8)], "a-dup": [], "b": ["B1", "B2"]}
            self.id_calls = []

        def match_ids(self, platform, puuid, start_time=None, count=10):
            self.id_calls.append(puuid)
            return self.ids[puuid][:count]

        def match(self, platform, mid):
            m = _match(mid)
            m["info"]["gameStartTimestamp"] = now_ms
            # player "a" is in every A-game; "b" is in A0..A2 too
            m["info"]["participants"][0]["puuid"] = "a" if mid.startswith("A") else "b"
            if mid in ("A0", "A1", "A2"):
                m["info"]["participants"][1]["puuid"] = "b"
            return m

    con = connect(tmp_path / "t.db")
    con.executemany("INSERT INTO players(puuid, platform, tier, last_crawled) VALUES (?,?,?,?)",
                    [("a", "na1", "EMERALD", 0), ("b", "na1", "EMERALD", 1)])
    c = Busy()
    got = collect.crawl(c, con, "na1", days=14, max_matches=100, max_per_player=3, mastery=False)
    # a: stops after 3 new games (of 8). b: already in A0..A2 -> skipped with no API call.
    assert got == 3 and c.id_calls == ["a"]
    assert collect.games_in_window(con, "a", int(time.time()) - 86400) == 3


def test_seed_does_not_hold_write_lock_between_pages(tmp_path):
    import sqlite3
    db = tmp_path / "t.db"

    class Other(FakeLadder):
        def league_entries(self, platform, tier, div, page):
            # Another region's collector writing between our API calls must not wait.
            o = sqlite3.connect(db, timeout=0.1)
            o.execute("INSERT OR IGNORE INTO players(puuid, platform, tier) VALUES (?,?,?)",
                      (f"o{div}{page}", "kr", "EMERALD"))
            o.commit()
            o.close()
            if page > 2:
                return []
            return [{"puuid": f"y{div}{page}", "rank": div, "leaguePoints": 1, "wins": 1, "losses": 1}]

    con = connect(db)
    assert collect.seed_players(Other(), con, "na1", ["MASTER", "EMERALD"]) == 9
