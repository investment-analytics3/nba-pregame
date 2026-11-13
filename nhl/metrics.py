"""NHL team metrics from play-by-play (sportsdataverse nhl_pbp_full: NHL API events + an expected-goals value
on every unblocked shot), as of a date (no hindsight).

Four metrics (owner Nov 12-13, spec "NHL Board"), each ranked 1-32 for the season and the last 5 games:
    xgf    5-on-5 expected-goals share  xGF / (xGF + xGA), unblocked shots, both goalies in
    cf     5-on-5 shot-attempt share (Corsi)  attempts incl. blocked shots
    gsax   goaltending: goals saved above expected per game  (xG against - goals against, goalie in net,
           all strengths, no shootout)
    st     special teams: power-play % + penalty-kill %  (opportunity = each new stretch with more
           skaters than the opponent, both goalies in)
Last 5 for the metrics (hockey is noisy; three games say little), last 3 for the form record.
Higher is better for all four, so rank 1 = best.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METRICS = ("xgf", "cf", "gsax", "st")
LABELS = {"xgf": "xGF% 5v5", "cf": "Corsi 5v5", "gsax": "GSAx/gm", "st": "PP%+PK%", "pts": "Points %"}
SHORT = {"xgf": "xGF%", "cf": "CF%", "gsax": "GSAx", "st": "ST"}
RECENT = 5
UNBLOCKED = {"SHOT", "MISSED_SHOT", "GOAL"}
ATTEMPTS = UNBLOCKED | {"BLOCKED_SHOT"}
PBP_COLS = ["game_id", "event_type", "event_team_type", "period", "period_type", "game_seconds", "home_skaters", "away_skaters",
            "empty_net", "home_goalie_id", "away_goalie_id", "event_goalie_id", "xg", "season_type", "game_date", "home_abbr", "away_abbr",
            "home_score", "away_score"]


def game_rows(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team) with 5v5 shot/xG counts, goaltending, special teams, starting goalie and result."""
    p = pbp.copy()
    p["game_id"] = p["game_id"].ffill().bfill()
    p = p[p["season_type"] == "R"]
    p["game_id"] = p["game_id"].astype("int64")
    p["game_date"] = p.groupby("game_id")["game_date"].transform("first")
    p = p[p["period_type"] != "SHOOTOUT"]
    ev = p["event_type"]
    home_ev = p["event_team_type"] == "home"
    goalies_in = p["home_goalie_id"].notna() & p["away_goalie_id"].notna()
    five = (p["home_skaters"] == 5) & (p["away_skaters"] == 5) & goalies_in
    xg = p["xg"].fillna(0.0)
    att, unb, goal = ev.isin(ATTEMPTS), ev.isin(UNBLOCKED), ev == "GOAL"
    en = p["empty_net"].fillna(False).astype(bool)
    in_net = unb & p["event_goalie_id"].notna() & ~en
    f = pd.DataFrame({
        "game_id": p["game_id"],
        "h_cf": (att & five & home_ev).astype(int), "a_cf": (att & five & ~home_ev).astype(int),
        "h_xgf": np.where(unb & five & home_ev, xg, 0.0), "a_xgf": np.where(unb & five & ~home_ev, xg, 0.0),
        # goaltending: shots ON the home goalie are the away team's
        "h_xga_net": np.where(in_net & ~home_ev, xg, 0.0), "a_xga_net": np.where(in_net & home_ev, xg, 0.0),
        "h_ga_net": (in_net & goal & ~home_ev).astype(int), "a_ga_net": (in_net & goal & home_ev).astype(int),
    })
    g = f.groupby("game_id").sum()
    # special teams: stretches with a skater advantage (both goalies in)
    p = p.sort_values(["game_id", "game_seconds"], kind="stable")
    h_adv = (p["home_skaters"] > p["away_skaters"]) & goalies_in
    a_adv = (p["away_skaters"] > p["home_skaters"]) & goalies_in
    # a stretch counts as an opportunity only if play restarts in it with a faceoff: line changes make
    # brief skater-count blips that are not power plays (they inflated chances by ~25%)
    fo = (p["event_type"] == "FACEOFF").astype(int)
    for side, adv in (("h", h_adv), ("a", a_adv)):
        prev = adv.groupby(p["game_id"]).shift(fill_value=False)
        seg = ((adv & ~prev).groupby(p["game_id"]).cumsum()).where(adv, 0)
        key = p["game_id"].astype(str) + ":" + seg.astype(str)
        has_fo = fo.groupby(key).transform("max").astype(bool) & adv
        real = adv & has_fo
        start = real & ~prev
        p[f"{side}_ppo"] = start.astype(int)
        p[f"{side}_ppg"] = (real & (p["event_type"] == "GOAL") & (p["event_team_type"] == ("home" if side == "h" else "away"))).astype(int)
    g = g.join(p.groupby("game_id")[["h_ppo", "a_ppo", "h_ppg", "a_ppg"]].sum())
    first = p.groupby("game_id").agg(date=("game_date", "first"), home=("home_abbr", "first"), away=("away_abbr", "first"),
                                     h_goalie=("home_goalie_id", "first"), a_goalie=("away_goalie_id", "first"),
                                     last_period=("period", "max"))
    g = g.join(first)
    rows = []
    for gid, r in g.iterrows():
        for side, o in (("h", "a"), ("a", "h")):
            rows.append({"game_id": gid, "date": str(r.date)[:10], "team": r.home if side == "h" else r.away,
                         "opp": r.away if side == "h" else r.home, "site": "home" if side == "h" else "away",
                         "cf": r[f"{side}_cf"], "ca": r[f"{o}_cf"], "xgf": r[f"{side}_xgf"], "xga": r[f"{o}_xgf"],
                         "xga_net": r[f"{side}_xga_net"], "ga_net": r[f"{side}_ga_net"],
                         "ppo": r[f"{side}_ppo"], "ppg": r[f"{side}_ppg"], "pko": r[f"{o}_ppo"], "pkga": r[f"{o}_ppg"],
                         "goalie": r[f"{side}_goalie"], "extra": r.last_period > 3})
    return pd.DataFrame(rows)


def attach_results(rows: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    """Final scores (shootout included) from the schedule release."""
    s = sched[["game_id", "home_score", "away_score"]].copy()
    s["game_id"] = s["game_id"].astype("int64")
    m = rows.merge(s, on="game_id", how="inner")
    home = m["site"] == "home"
    m["gf"] = np.where(home, m["home_score"], m["away_score"])
    m["ga"] = np.where(home, m["away_score"], m["home_score"])
    m["win"] = m["gf"] > m["ga"]
    m["pts"] = np.where(m["win"], 2, np.where(m["extra"], 1, 0))
    return m.drop(columns=["home_score", "away_score"]).dropna(subset=["gf", "ga"])


def _calc(g: pd.DataFrame) -> dict:
    if not len(g):
        return {m: None for m in METRICS}
    s = g[["cf", "ca", "xgf", "xga", "xga_net", "ga_net", "ppo", "ppg", "pko", "pkga"]].sum()
    pp = s.ppg / s.ppo if s.ppo else 0.0
    pk = 1 - s.pkga / s.pko if s.pko else 1.0
    return {"xgf": round(100 * s.xgf / (s.xgf + s.xga), 2) if s.xgf + s.xga else None,
            "cf": round(100 * s.cf / (s.cf + s.ca), 2) if s.cf + s.ca else None,
            "gsax": round((s.xga_net - s.ga_net) / len(g), 3),
            "st": round(100 * (pp + pk), 1), "pp": round(100 * pp, 1), "pk": round(100 * pk, 1)}


def _rank(values: dict) -> dict:
    order = sorted(values, key=lambda t: (-values[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def _rec(g: pd.DataFrame) -> str:
    w = int(g["win"].sum())
    otl = int(((~g["win"]) & g["extra"]).sum())
    l_ = len(g) - w - otl
    return f"{w}-{l_}-{otl}"


def compute(rows: pd.DataFrame, before: str | None = None) -> dict:
    """{team: {...}} from games strictly before `before` (YYYY-MM-DD)."""
    r = rows if before is None else rows[rows["date"] < before]
    r = r.sort_values(["date", "game_id"])
    teams = {}
    for t, g in r.groupby("team"):
        l3, l5 = g.tail(3), g.tail(RECENT)
        starts = g["goalie"].value_counts()
        teams[t] = {"abbr": t, "games": int(len(g)), "record": _rec(g), "pts_pct": round(g["pts"].sum() / (2 * len(g)), 3),
                    "l3_record": _rec(l3), "l3": ["W" if w else ("OT" if x else "L") for w, x in zip(l3["win"], l3["extra"])],
                    "last_game_date": g["date"].iloc[-1], "season": _calc(g), "last5": _calc(l5),
                    "goalie_starts": {str(int(k)): int(v) for k, v in starts.items() if pd.notna(k)},
                    "regular_goalie": str(int(starts.index[0])) if len(starts) else None,
                    "last_games": [{"game_id": int(x.game_id), "date": x.date, "site": x.site, "opp": x.opp, "pts": int(x.gf),
                                    "opp_pts": int(x.ga), "extra": bool(x.extra)} for x in l3.iloc[::-1].itertuples()],
                    "trend_games": [{"date": x.date, "opp": x.opp, "metrics": {k: v for k, v in _calc(l5.iloc[[i]]).items() if k in METRICS}}
                                    for i, x in enumerate(l5.itertuples())]}
    for w in ("season", "last5"):
        for m in METRICS:
            vals = {t: v[w][m] for t, v in teams.items() if v[w][m] is not None}
            for t, rk in _rank(vals).items():
                teams[t][w].setdefault("ranks", {})[m] = rk
    pts = _rank({t: v["pts_pct"] for t, v in teams.items()})
    for t in teams:
        teams[t]["pts_rank"] = pts[t]
    return teams


def own_rank(teams: dict) -> dict:
    """Our daily power rank: average of the 5v5 expected-goals share rank and the points-% rank, re-ranked 1-32."""
    avg = {t: ((v["season"].get("ranks") or {}).get("xgf", 16) + v["pts_rank"]) / 2 for t, v in teams.items()}
    order = sorted(avg, key=lambda t: (avg[t], teams[t]["pts_rank"]))
    return {t: i + 1 for i, t in enumerate(order)}


def b2b(rows: pd.DataFrame, team: str, day: str) -> bool:
    prev = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return bool(((rows["team"] == team) & (rows["date"] == prev)).any())
