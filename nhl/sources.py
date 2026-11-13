"""NHL data (all free, no keys): sportsdataverse releases built from the NHL API, refreshed nightly (~midnight PT):
    nhl_pbp_full        play-by-play with an expected-goals value per unblocked shot, strength, goalies
    nhl_schedules       final scores (shootouts included)
    nhl_player_boxscores  time on ice (key skaters), goalie starts (regular starter)
File year = season end (2026-27 = 2027). Cached in data/nhl/cache/ (git-ignored), refreshed when older than 3 h.
"""
from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd

from nba.http import FetchError, get
from . import metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "nhl")
CACHE = os.path.join(DATA, "cache")
REL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
URLS = {"pbp": REL + "/nhl_pbp_full/play_by_play_{y}.parquet", "sched": REL + "/nhl_schedules/nhl_schedule_{y}.parquet",
        "players": REL + "/nhl_player_boxscores/player_box_{y}.parquet"}
MAX_AGE = 3 * 3600


def season_for(d: date) -> int:
    return d.year + 1 if d.month >= 7 else d.year


def _fetch(kind: str, season: int, refresh: bool) -> str | None:
    path = os.path.join(CACHE, f"{kind}_{season}.parquet")
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE
    if (refresh and not fresh) or not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        try:
            body = get(URLS[kind].format(y=season), want="bytes", timeout=180, retries=2)
        except FetchError:
            body = None
        if body:
            with open(path + ".tmp", "wb") as f:
                f.write(body)
            os.replace(path + ".tmp", path)
    return path if os.path.exists(path) else None


def game_rows(season: int, refresh: bool = True) -> pd.DataFrame:
    """One row per (game, team) for the season so far (see nhl.metrics.game_rows)."""
    pp, sp = _fetch("pbp", season, refresh), _fetch("sched", season, refresh)
    if not pp or not sp:
        return pd.DataFrame()
    pbp = pd.read_parquet(pp, columns=metrics.PBP_COLS)
    return metrics.attach_results(metrics.game_rows(pbp), pd.read_parquet(sp))


def players(season: int, refresh: bool = True) -> pd.DataFrame:
    p = _fetch("players", season, refresh)
    cols = ["team_abbrev", "player_id", "player_name", "position", "toi", "game_id", "game_date", "starter", "goals", "points", "shots_against"]
    return pd.read_parquet(p, columns=cols) if p else pd.DataFrame(columns=cols)


def _toi(s) -> float:
    try:
        m, x = str(s).split(":")
        return int(m) + int(x) / 60
    except ValueError:
        return 0.0


def key_people(pb: pd.DataFrame, team: str) -> dict:
    """Key skaters = top 6 on the team in time on ice per game (half the team's games or more);
    regular goalie = most starts. Names as the NHL writes them ('B. Tkachuk')."""
    p = pb[pb["team_abbrev"] == team]
    if not len(p):
        return {"skaters": [], "goalies": []}
    n = p["game_id"].nunique()
    is_g = p["shots_against"].notna() | (p["position"] == "G")      # goalies have no position in this release
    sk = p[~is_g].assign(m=lambda d: d["toi"].map(_toi))
    g = sk.groupby(["player_id", "player_name"]).agg(gp=("game_id", "nunique"), toi=("m", "mean"), pts=("points", "sum")).reset_index()
    g = g[g["gp"] >= max(1, n / 2)].sort_values("toi", ascending=False).head(6)
    go = p[is_g]
    st = go[go["starter"] == True].groupby(["player_id", "player_name"]).size().sort_values(ascending=False)  # noqa: E712
    return {"skaters": [{"id": str(int(r.player_id)), "name": r.player_name, "toi": round(float(r.toi), 1), "pts": int(r.pts)} for r in g.itertuples()],
            "goalies": [{"id": str(int(i[0])), "name": i[1], "starts": int(v)} for i, v in st.items()]}
