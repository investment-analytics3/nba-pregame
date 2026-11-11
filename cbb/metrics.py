"""College basketball team metrics from box scores, as of a date (no hindsight).

Same four metrics as the NBA board, from summed raw totals (not averaged percentages):
    eFG%        = (FGM + 0.5 * 3PM) / FGA
    Possessions = FGA - OREB + TOV + 0.475 * FTA   (college free-throw factor; averaged with the opponent's estimate)
    Off Eff     = points per 100 possessions;  Def Eff = points allowed per 100 possessions;  Net = Off - Def
Only games between two Division I teams count (cupcake games against non-D-I schools would inflate everything).
Ranks are among D-I teams (about 365), 1 = best for every metric (defensive metrics: lower value = better rank).
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

METRICS = ("efg", "off", "opp_efg", "def")
ALL = METRICS + ("net",)
LABELS = {"efg": "eFG%", "off": "Off Eff", "opp_efg": "Opp eFG%", "def": "Def Eff", "net": "Net rating"}
HIGHER_IS_BETTER = {"efg": True, "opp_efg": False, "off": True, "def": False, "net": True}
FT_FACTOR = 0.475
D1_MIN_GAMES = 15          # a full past season: D-I teams play 28+; non-D-I visitors play 1-3


def box_rows(tb: pd.DataFrame) -> pd.DataFrame:
    """sportsdataverse team box scores -> one row per (game, team) with the opponent's totals attached."""
    cols = {"field_goals_made": "fgm", "field_goals_attempted": "fga", "three_point_field_goals_made": "fg3m",
            "free_throws_attempted": "fta", "offensive_rebounds": "oreb", "total_turnovers": "tov",
            "team_score": "pts"}
    t = tb.rename(columns=cols)
    t = t[["game_id", "game_date", "team_id", "opponent_team_id", "team_home_away", "team_abbreviation"] + list(cols.values())].copy()
    t["game_id"] = t["game_id"].astype(int)
    t["team_id"] = t["team_id"].astype(int)
    t["opponent_team_id"] = t["opponent_team_id"].astype(int)
    t["game_date"] = t["game_date"].astype(str).str[:10]
    o = t[["game_id", "team_id", "team_abbreviation"] + list(cols.values())].rename(
        columns={c: "o_" + c for c in cols.values()} | {"team_id": "opponent_team_id", "team_abbreviation": "opp_abbr"})
    m = t.merge(o, on=["game_id", "opponent_team_id"], how="inner")
    return m.dropna(subset=["fga", "o_fga", "pts", "o_pts"])


def d1_from_counts(rows: pd.DataFrame, min_games: int = D1_MIN_GAMES) -> set:
    c = rows["team_id"].value_counts()
    return set(int(t) for t in c[c >= min_games].index)


def _poss(fga, oreb, tov, fta):
    return fga - oreb + tov + FT_FACTOR * fta


def _calc(g: pd.DataFrame) -> dict:
    if not len(g):
        return {m: None for m in ALL}
    s = g[["pts", "o_pts", "fgm", "fga", "fg3m", "fta", "oreb", "tov", "o_fgm", "o_fga", "o_fg3m", "o_fta", "o_oreb", "o_tov"]].sum()
    poss = 0.5 * (_poss(s.fga, s.oreb, s.tov, s.fta) + _poss(s.o_fga, s.o_oreb, s.o_tov, s.o_fta))
    if poss <= 0 or s.fga <= 0 or s.o_fga <= 0:
        return {m: None for m in ALL}
    off, dfn = 100 * s.pts / poss, 100 * s.o_pts / poss
    return {"efg": round(100 * (s.fgm + 0.5 * s.fg3m) / s.fga, 2), "opp_efg": round(100 * (s.o_fgm + 0.5 * s.o_fg3m) / s.o_fga, 2),
            "off": round(off, 2), "def": round(dfn, 2), "net": round(off - dfn, 2), "poss_per_game": round(poss / len(g), 1)}


def _rank(values: dict, higher: bool) -> dict:
    order = sorted(values, key=lambda t: (-values[t] if higher else values[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def _record(g: pd.DataFrame) -> str:
    w = int((g["pts"] > g["o_pts"]).sum())
    return f"{w}-{len(g) - w}"


def compute(rows: pd.DataFrame, d1: set, before: str | None = None, min_games: int = 1) -> dict:
    """{team_id: {...}} from D-I vs D-I games strictly before `before` (YYYY-MM-DD).

    Records and last-3 results count every game (non-D-I opponents too); the metrics only D-I games.
    """
    r = rows if before is None else rows[rows["game_date"] < before]
    r = r.sort_values(["game_date", "game_id"])
    dd = r[r["team_id"].isin(d1) & r["opponent_team_id"].isin(d1)]
    teams = {}
    all_by = {int(t): g for t, g in r.groupby("team_id")}
    for tid, g in dd.groupby("team_id"):
        tid = int(tid)
        if len(g) < min_games:
            continue
        ga = all_by.get(tid, g)
        l3 = ga.tail(3)
        teams[tid] = {"team_id": tid, "abbr": g["team_abbreviation"].iloc[-1], "games": int(len(g)), "games_all": int(len(ga)),
                      "record": _record(ga), "l3_record": _record(l3), "l3": ["W" if p > o else "L" for p, o in zip(l3["pts"], l3["o_pts"])],
                      "last_game_date": ga["game_date"].iloc[-1],
                      "season": _calc(g), "last3": _calc(g.tail(3)),
                      "last3_games": [{"game_id": int(x.game_id), "date": x.game_date, "site": x.team_home_away,
                                       "opp_id": int(x.opponent_team_id), "opp": x.opp_abbr, "d1_opp": int(x.opponent_team_id) in d1,
                                       "pts": int(x.pts), "opp_pts": int(x.o_pts),
                                       "metrics": {k: v for k, v in _calc(l3.iloc[[i]]).items() if k in ALL}}
                                      for i, x in reversed(list(enumerate(l3.itertuples())))]}
    for w in ("season", "last3"):
        for m in ALL:
            vals = {t: v[w][m] for t, v in teams.items() if v[w][m] is not None}
            for t, rk in _rank(vals, HIGHER_IS_BETTER[m]).items():
                teams[t][w].setdefault("ranks", {})[m] = rk
    return teams


def key_players(pb: pd.DataFrame, team_id: int, before: str | None = None) -> list[dict]:
    """Key player = started at least half of the team's games, or top 5 on the team in minutes per game (as NBA)."""
    p = pb[(pb["team_id"].astype(int) == int(team_id)) & (pb["did_not_play"] != True)]  # noqa: E712
    if before:
        p = p[p["game_date"].astype(str).str[:10] < before]
    if not len(p):
        return []
    n_games = p["game_id"].nunique()
    p = p.assign(min=pd.to_numeric(p["minutes"], errors="coerce").fillna(0), st=p["starter"].fillna(False).astype(bool))
    g = p.groupby(["athlete_id", "athlete_display_name"]).agg(starts=("st", "sum"), mins=("min", "sum"), gp=("game_id", "nunique")).reset_index()
    g["mpg"] = g["mins"] / g["gp"].clip(lower=1)
    top5 = set(g.sort_values("mpg", ascending=False).head(5)["athlete_id"])
    key = g[(g["starts"] >= n_games / 2) | g["athlete_id"].isin(top5)].sort_values("mpg", ascending=False)
    return [{"id": str(int(r.athlete_id)), "name": r.athlete_display_name, "mpg": round(float(r.mpg), 1), "starts": int(r.starts)}
            for r in key.itertuples()]


def b2b(rows: pd.DataFrame, team_id: int, day: str) -> bool:
    """Played the day before `day` (college back-to-backs happen in holiday and conference tournaments)."""
    prev = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return bool(((rows["team_id"] == int(team_id)) & (rows["game_date"] == prev)).any())
