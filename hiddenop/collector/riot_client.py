"""Minimal Riot API client with a two-window rate limiter.

Personal/dev keys: 20 req / 1 s and 100 req / 120 s, enforced per routing value
(platform like na1, or region like americas). We keep one limiter per host.
"""
from __future__ import annotations

import os
import time
from collections import deque

import requests

PLATFORM_TO_REGION = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "sg2": "sea", "tw2": "sea", "vn2": "sea",
}

DEFAULT_LIMITS = ((20, 1.0), (100, 120.0))  # (requests, seconds)


class RateLimiter:
    def __init__(self, limits=DEFAULT_LIMITS, safety=0.9):
        # Run slightly under the limit so clock skew doesn't trigger 429s.
        self.windows = [(max(1, int(n * safety)), secs, deque()) for n, secs in limits]

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            wait = 0.0
            for n, secs, hist in self.windows:
                while hist and now - hist[0] >= secs:
                    hist.popleft()
                if len(hist) >= n:
                    wait = max(wait, secs - (now - hist[0]))
            if wait <= 0:
                for _, _, hist in self.windows:
                    hist.append(now)
                return
            time.sleep(wait + 0.01)


class RiotClient:
    def __init__(self, api_key: str | None = None, limits=DEFAULT_LIMITS, timeout=10):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set RIOT_API_KEY (see .env.example)")
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key
        self.limits = limits
        self.timeout = timeout
        self._limiters: dict[str, RateLimiter] = {}

    def _get(self, host: str, path: str, params=None, max_retries=5):
        limiter = self._limiters.setdefault(host, RateLimiter(self.limits))
        url = f"https://{host}.api.riotgames.com{path}"
        for attempt in range(max_retries):
            limiter.acquire()
            r = self.session.get(url, params=params, timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 5)))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        raise RuntimeError(f"Gave up on {url} after {max_retries} attempts")

    # --- league-v4 (platform routing) ---
    def apex_league(self, platform: str, tier: str, queue="RANKED_SOLO_5x5"):
        """tier in {'challenger','grandmaster','master'}"""
        return self._get(platform, f"/lol/league/v4/{tier}leagues/by-queue/{queue}")

    def league_entries(self, platform: str, tier: str, division: str, page=1,
                       queue="RANKED_SOLO_5x5"):
        """tier like 'DIAMOND', division like 'I'. Returns [] past the last page."""
        return self._get(platform, f"/lol/league/v4/entries/{queue}/{tier}/{division}",
                         params={"page": page}) or []

    # --- match-v5 (regional routing) ---
    def match_ids(self, platform: str, puuid: str, start_time: int | None = None,
                  count=100, queue=420):
        params = {"queue": queue, "count": count}
        if start_time:
            params["startTime"] = start_time
        region = PLATFORM_TO_REGION[platform]
        return self._get(region, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params) or []

    def match(self, platform: str, match_id: str):
        region = PLATFORM_TO_REGION[platform]
        return self._get(region, f"/lol/match/v5/matches/{match_id}")

    # --- champion-mastery-v4 (platform routing) — for the v2 one-trick correction ---
    def mastery(self, platform: str, puuid: str, champion_id: int):
        return self._get(
            platform,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}",
        )
