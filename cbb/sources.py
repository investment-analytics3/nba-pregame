"""Men's college basketball data (all free, no keys).

    box scores   sportsdataverse releases (ESPN team + player box scores, refreshed about daily):
                 espn_mens_college_basketball_team_boxscores / _player_boxscores, file year = season end
                 (2026-27 = 2027). Cached in data/cbb/cache/ (git-ignored), refreshed when older than 3 h.
    catch-up     games the release doesn't have yet (usually last night's) come from ESPN game pages and are
                 committed to data/cbb/catchup/<date>.json, so each game is fetched once.
    D-I teams    ESPN teams?groups=50 (365 in 2026-27), saved to data/cbb/d1.json.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta

import pandas as pd

from nba.http import FetchError, get
from . import espn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "cbb")
CACHE = os.path.join(DATA, "cache")
REL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
TEAM_URL = REL + "/espn_mens_college_basketball_team_boxscores/team_box_{y}.parquet"
PLAYER_URL = REL + "/espn_mens_college_basketball_player_boxscores/player_box_{y}.parquet"
MAX_AGE = 3 * 3600
CATCHUP_DAYS = 4


def season_for(d: date) -> int:
    """Season label = the year it ends (Nov 2026 -> 2027), as sportsdataverse names its files."""
    return d.year + 1 if d.month >= 7 else d.year


def _fetch(url: str, path: str, refresh: bool) -> str | None:
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE
    if (refresh and not fresh) or not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            body = get(url, want="bytes", timeout=120, retries=2)
        except FetchError:
            body = None
        if not body:
            return path if os.path.exists(path) else None
        with open(path + ".tmp", "wb") as f:
            f.write(body)
        os.replace(path + ".tmp", path)
    return path


def team_box(season: int, refresh: bool = True) -> pd.DataFrame:
    p = _fetch(TEAM_URL.format(y=season), os.path.join(CACHE, f"team_box_{season}.parquet"), refresh)
    return pd.read_parquet(p) if p else pd.DataFrame()


def player_box(season: int, refresh: bool = True) -> pd.DataFrame:
    p = _fetch(PLAYER_URL.format(y=season), os.path.join(CACHE, f"player_box_{season}.parquet"), refresh)
    cols = ["game_id", "game_date", "team_id", "athlete_id", "athlete_display_name", "minutes", "starter", "did_not_play", "points"]
    return pd.read_parquet(p, columns=cols) if p else pd.DataFrame(columns=cols)


def d1_teams(refresh: bool = True) -> dict:
    """{team_id: {...}} for Division I, from ESPN (saved; the saved copy is used if ESPN is down)."""
    path = os.path.join(DATA, "d1.json")
    saved = {}
    if os.path.exists(path):
        with open(path) as f:
            saved = {int(k): v for k, v in json.load(f).items()}
    if refresh and (not saved or time.time() - os.path.getmtime(path) > 7 * 86400):
        try:
            t = espn.d1_teams()
            if len(t) >= 300:
                os.makedirs(DATA, exist_ok=True)
                with open(path, "w") as f:
                    json.dump({str(k): v for k, v in sorted(t.items())}, f, indent=0)
                return t
        except FetchError:
            pass
    return saved


def catchup(release: pd.DataFrame, today: date, d1: set, notes: list) -> pd.DataFrame:
    """Finished D-I games from the last few days that the release doesn't have yet (ESPN game pages).

    Each day's rows are saved to data/cbb/catchup/<date>.json and reused; a day is re-checked only
    while it is missing games."""
    have = set(release["game_id"].astype(int)) if len(release) else set()
    rows = []
    for back in range(1, CATCHUP_DAYS + 1):
        day = today - timedelta(days=back)
        path = os.path.join(DATA, "catchup", f"{day.isoformat()}.json")
        saved = []
        if os.path.exists(path):
            with open(path) as f:
                saved = json.load(f)
        got = {r["game_id"] for r in saved}
        try:
            events = espn.parse_events(espn.scoreboard(f"{day:%Y%m%d}"))
        except FetchError:
            rows += [r for r in saved if r["game_id"] not in have]
            continue
        added = missed = 0
        for ev in events:
            gid = int(ev["id"])
            if not ev["completed"] or gid in have or gid in got:
                continue
            if int(ev["home"]["id"]) not in d1 and int(ev["away"]["id"]) not in d1:
                continue
            try:
                saved += espn.box_rows(espn.summary(ev["id"]), day.isoformat())
                added += 1
            except (FetchError, KeyError, ValueError):
                missed += 1
        if missed:
            notes.append(f"{missed} box score{'s' if missed > 1 else ''} from {day:%b %-d} not available yet; picked up on a later build.")
        if added:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(saved, f)
        rows += [r for r in saved if r["game_id"] not in have]
    # keep the folder small: drop catch-up files the release has fully caught up with
    for name in os.listdir(os.path.join(DATA, "catchup")) if os.path.isdir(os.path.join(DATA, "catchup")) else []:
        try:
            d = date.fromisoformat(name[:10])
        except ValueError:
            continue
        if d < today - timedelta(days=CATCHUP_DAYS + 3):
            os.remove(os.path.join(DATA, "catchup", name))
    return pd.DataFrame(rows)


def season_rows(season: int, today: date, d1: set, notes: list, refresh: bool = True) -> tuple[pd.DataFrame, str]:
    """All of this season's team box rows (release + catch-up), as cbb.metrics.box_rows input, and the last date."""
    tb = team_box(season, refresh)
    if not len(tb):
        notes.append("Box-score release unavailable; using ESPN catch-up only.")
        tb = pd.DataFrame(columns=["game_id", "game_date", "team_id", "opponent_team_id", "team_home_away", "team_abbreviation",
                                   "field_goals_made", "field_goals_attempted", "three_point_field_goals_made",
                                   "free_throws_attempted", "offensive_rebounds", "total_turnovers", "team_score"])
    tb = tb[tb["season_type"] == 2] if "season_type" in tb else tb
    cu = catchup(tb, today, d1, notes)
    keep = ["game_id", "game_date", "team_id", "opponent_team_id", "team_home_away", "team_abbreviation",
            "field_goals_made", "field_goals_attempted", "three_point_field_goals_made", "free_throws_attempted",
            "offensive_rebounds", "total_turnovers", "team_score"]
    tb = tb[keep].copy()
    tb["game_date"] = tb["game_date"].astype(str).str[:10]
    allr = pd.concat([tb, cu[keep]] if len(cu) else [tb], ignore_index=True)
    allr = allr.drop_duplicates(["game_id", "team_id"])
    allr = allr[allr["game_date"] < today.isoformat()]
    return allr, (allr["game_date"].max() if len(allr) else None)
