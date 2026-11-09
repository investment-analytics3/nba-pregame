"""Free NFL data: nflverse files (play-by-play, schedule, snap counts) and team codes.

nflverse publishes play-by-play as one parquet file per season on GitHub releases,
updated through the season. The schedule file carries ESPN event ids, rest days,
neutral sites and closing lines, which ties ESPN, nflverse and Kalshi together.
"""
from __future__ import annotations

import io
import os
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "nfl")
CACHE = os.path.join(DATA, "cache")          # not committed (see .gitignore)

PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
SNAPS_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.parquet"
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# Team codes differ by source. Internally everything uses nflverse codes.
ESPN_TO_NFL = {"LAR": "LA", "WSH": "WAS"}
NFL_TO_ESPN = {v: k for k, v in ESPN_TO_NFL.items()}
NFL_TO_KALSHI = {"LA": "LAR", "JAX": "JAC"}

SCRIMMAGE_COLS = ["game_id", "season", "season_type", "week", "play_id", "posteam", "defteam", "home_team",
                  "away_team", "pass", "rush", "qb_dropback", "epa", "success", "wp", "vegas_home_wp", "qtr",
                  "total_home_score", "total_away_score", "passer_player_id", "passer_player_name",
                  "interception", "fumble_lost", "sack", "qb_hit", "third_down_converted", "third_down_failed",
                  "play_type", "fixed_drive", "yardline_100", "spread_line", "result"]


def espn_code(nfl: str) -> str:
    return NFL_TO_ESPN.get(nfl, nfl)


def nfl_code(espn: str) -> str:
    return ESPN_TO_NFL.get(espn, espn)


def kalshi_code(nfl: str) -> str:
    return NFL_TO_KALSHI.get(nfl, nfl)


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-board/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _cached(name: str, url: str, refresh: bool) -> str:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if refresh or not os.path.exists(path):
        body = _download(url)
        with open(path, "wb") as f:
            f.write(body)
    return path


def pbp(season: int, refresh: bool = True) -> pd.DataFrame:
    path = _cached(f"pbp_{season}.parquet", PBP_URL.format(season=season), refresh)
    d = pd.read_parquet(path, columns=SCRIMMAGE_COLS)
    return d[d.season_type.isin(["REG", "POST"])].copy()


def snaps(season: int, refresh: bool = True) -> pd.DataFrame:
    try:
        path = _cached(f"snaps_{season}.parquet", SNAPS_URL.format(season=season), refresh)
        return pd.read_parquet(path)
    except Exception:          # snap counts are optional (used for key starters)
        return pd.DataFrame()


def schedule(refresh: bool = True) -> pd.DataFrame:
    path = _cached("games.csv", SCHEDULE_URL, refresh)
    g = pd.read_csv(path, low_memory=False)
    g["espn"] = g["espn"].astype("Int64").astype(str).replace("<NA>", "")
    return g


def schedule_from_bytes(body: bytes) -> pd.DataFrame:   # tests
    return pd.read_csv(io.BytesIO(body))
