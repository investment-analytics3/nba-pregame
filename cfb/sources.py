"""College football data sources (all free, no keys).

    play-by-play  sportsdataverse `espn_cfb_pbp` release: ESPN plays with EPA, updated weekly
    schedules     cfbfastR-data: FBS flags (home_division/away_division), neutral sites, dates, results
Both are fetched to data/cfb/cache/ (git-ignored) and refreshed when older than a few hours.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from nba.http import get

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "cfb")
CACHE = os.path.join(DATA, "cache")
PBP_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_{y}.parquet"
SCHED_URL = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/schedules/csv/cfb_schedules_{y}.csv"
PBP_COLS = ["game_id", "week", "seasonType", "pos_team_id", "def_pos_team_id", "EPA", "rush", "pass", "scrimmage_play",
            "passer_player_name", "rusher_player_name", "receiver_player_name", "sack"]
MAX_AGE = 3 * 3600


def _fetch(url: str, path: str, refresh: bool) -> str:
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE
    if refresh and not fresh or not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = get(url, want="bytes", timeout=120, retries=2)
        if not body:
            raise RuntimeError(f"empty download: {url}")
        with open(path + ".tmp", "wb") as f:
            f.write(body)
        os.replace(path + ".tmp", path)
    return path


def pbp(season: int, refresh: bool = True) -> pd.DataFrame:
    p = _fetch(PBP_URL.format(y=season), os.path.join(CACHE, f"pbp_{season}.parquet"), refresh)
    return pd.read_parquet(p, columns=PBP_COLS)


def schedule(season: int, refresh: bool = True) -> pd.DataFrame:
    p = _fetch(SCHED_URL.format(y=season), os.path.join(CACHE, f"sched_{season}.csv"), refresh)
    s = pd.read_csv(p)
    return s[s["season_type"] == "regular"].copy()


def fbs_ids(sched: pd.DataFrame) -> set:
    return set(sched.loc[sched["home_division"] == "fbs", "home_id"].astype(int)) | \
        set(sched.loc[sched["away_division"] == "fbs", "away_id"].astype(int))
