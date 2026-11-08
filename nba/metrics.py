"""Team metrics computed from saved box scores.

eFG%            = (FGM + 0.5 * 3PM) / FGA
Possessions     = FGA - OREB + TOV + 0.44 * FTA   (averaged with the opponent's estimate)
Off efficiency  = points scored per 100 possessions
Def efficiency  = points allowed per 100 possessions
Net rating      = Off - Def

Season and last-3 values are built from summed raw totals (not averaged
percentages), then ranked 1-30 with 1 = best.
"""
from __future__ import annotations

from collections import defaultdict

METRICS = ("efg", "opp_efg", "off", "def", "net")
HIGHER_IS_BETTER = {"efg": True, "opp_efg": False, "off": True, "def": False, "net": True}
SUM_FIELDS = ("pts", "opp_pts", "fgm", "fga", "fg3m", "fta", "oreb", "tov",
              "o_fgm", "o_fga", "o_fg3m", "o_fta", "o_oreb", "o_tov")


def _poss(fga, oreb, tov, fta) -> float:
    return fga - oreb + tov + 0.44 * fta


def _with_opponent(rows: list[dict]) -> list[dict]:
    by_key = {(r["game_id"], r["team_id"]): r for r in rows}
    out = []
    for r in rows:
        o = by_key.get((r["game_id"], r["opp_id"]))
        if not o:
            continue  # incomplete game; skip
        x = dict(r)
        for f in ("fgm", "fga", "fg3m", "fta", "oreb", "tov"):
            x["o_" + f] = o[f]
        out.append(x)
    return out


def _calc(games: list[dict]) -> dict:
    t = {f: sum(g[f] for g in games) for f in SUM_FIELDS}
    poss = sum(0.5 * (_poss(g["fga"], g["oreb"], g["tov"], g["fta"])
                      + _poss(g["o_fga"], g["o_oreb"], g["o_tov"], g["o_fta"])) for g in games)
    if not games or poss <= 0 or t["fga"] <= 0 or t["o_fga"] <= 0:
        return {m: None for m in METRICS}
    off = 100 * t["pts"] / poss
    dfn = 100 * t["opp_pts"] / poss
    return {
        "efg": round(100 * (t["fgm"] + 0.5 * t["fg3m"]) / t["fga"], 2),
        "opp_efg": round(100 * (t["o_fgm"] + 0.5 * t["o_fg3m"]) / t["o_fga"], 2),
        "off": round(off, 2),
        "def": round(dfn, 2),
        "net": round(off - dfn, 2),
        "poss_per_game": round(poss / len(games), 1),
    }


def _rank(teams: dict, window: str):
    for m in METRICS:
        vals = [(tid, d[window][m]) for tid, d in teams.items() if d[window][m] is not None]
        vals.sort(key=lambda kv: kv[1], reverse=HIGHER_IS_BETTER[m])
        rank, prev = 0, None
        for i, (tid, v) in enumerate(vals, start=1):
            if v != prev:
                rank, prev = i, v
            teams[tid][window].setdefault("ranks", {})[m] = rank


def _record(games: list[dict]) -> str:
    w = sum(1 for g in games if g["pts"] > g["opp_pts"])
    return f"{w}-{len(games) - w}"


def compute(rows: list[dict], before: str | None = None) -> dict:
    """Metrics for every team using games strictly before `before` (YYYY-MM-DD)."""
    rows = [r for r in rows if before is None or r["game_date"] < before]
    games = _with_opponent(rows)
    by_team = defaultdict(list)
    for g in games:
        by_team[g["team_id"]].append(g)
    teams = {}
    for tid, gs in by_team.items():
        gs.sort(key=lambda g: (g["game_date"], g["game_id"]))
        last3 = gs[-3:]
        teams[tid] = {
            "team_id": tid, "team": gs[-1]["team"], "games": len(gs),
            "record": _record(gs), "l3_record": _record(last3),
            "last_game_date": gs[-1]["game_date"],
            "season": _calc(gs), "last3": _calc(last3),
            "last3_games": [{"game_id": g["game_id"], "date": g["game_date"], "site": g["site"],
                             "opp_id": g["opp_id"], "opp": g["opp"], "pts": g["pts"],
                             "opp_pts": g["opp_pts"]} for g in reversed(last3)],
        }
    _rank(teams, "season")
    _rank(teams, "last3")
    through = max((r["game_date"] for r in rows), default=None)
    return {"games_through": through, "team_count": len(teams), "teams": teams}
