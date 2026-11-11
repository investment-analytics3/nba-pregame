"""College stats as of a week (no hindsight): the four core metrics, season + last 3, ranked
among FBS teams; records, last results, rest, quarterbacks and key players.

Metrics = scrimmage runs + passes from ESPN play-by-play with EPA (sportsdataverse);
success = EPA > 0. Defensive metrics: lower is better (rank 1 = best defense).
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import pandas as pd

METRICS = ("off_epa", "off_sr", "def_epa", "def_sr")
LABELS = {"off_epa": "Off EPA/play", "off_sr": "Off success", "def_epa": "Def EPA allowed", "def_sr": "Def success allowed"}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z ]", "", s).strip()


def scrimmage(pbp: pd.DataFrame) -> pd.DataFrame:
    p = pbp[(pbp["scrimmage_play"] == True) & ((pbp["rush"] == 1) | (pbp["pass"] == 1)) & pbp["EPA"].notna()]  # noqa: E712
    return p.assign(succ=(p["EPA"] > 0).astype(int))


def team_games(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team): offense and defense EPA sums, success counts, play counts."""
    p = scrimmage(pbp)
    off = p.groupby(["game_id", "week", "pos_team_id"]).agg(off_epa_sum=("EPA", "sum"), off_succ=("succ", "sum"),
                                                            off_n=("EPA", "size")).reset_index().rename(columns={"pos_team_id": "team"})
    de = p.groupby(["game_id", "def_pos_team_id"]).agg(def_epa_sum=("EPA", "sum"), def_succ=("succ", "sum"),
                                                       def_n=("EPA", "size")).reset_index().rename(columns={"def_pos_team_id": "team"})
    tg = off.merge(de, on=["game_id", "team"], how="inner")
    tg["team"] = tg["team"].astype(int)
    return tg


def _rank(values: dict, ascending: bool) -> dict:
    order = sorted(values, key=lambda t: (values[t], t) if ascending else (-values[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def _agg(x: pd.DataFrame) -> dict:
    on, dn = max(x["off_n"].sum(), 1), max(x["def_n"].sum(), 1)
    return {"off_epa": x["off_epa_sum"].sum() / on, "off_sr": x["off_succ"].sum() / on,
            "def_epa": x["def_epa_sum"].sum() / dn, "def_sr": x["def_succ"].sum() / dn, "games": int(len(x))}


def week_metrics(tg: pd.DataFrame, week: int, fbs: set) -> dict:
    """{team_id: {"season": {...,"ranks"}, "last3": {...,"ranks"}, "net", "net_rank"}} from games before `week`."""
    prior = tg[tg["week"] < week].sort_values(["week", "game_id"])
    out = {}
    for team, g in prior.groupby("team"):
        if team not in fbs or len(g) < 2:
            continue
        s, l3 = _agg(g), _agg(g.tail(3))
        out[int(team)] = {"season": s, "last3": l3, "net": s["off_epa"] - s["def_epa"]}
    for w in ("season", "last3"):
        for m in METRICS:
            r = _rank({t: v[w][m] for t, v in out.items()}, ascending=m.startswith("def"))
            for t, v in out.items():
                v[w].setdefault("ranks", {})[m] = r[t]
    rn = _rank({t: v["net"] for t, v in out.items()}, ascending=False)
    for t, v in out.items():
        v["net_rank"] = rn[t]
    return out


def records(sched: pd.DataFrame, before_utc: pd.Timestamp) -> dict:
    """{team_id: {"record", "last2", "last3", "last_date"}} from completed games before a moment."""
    s = sched.assign(date=pd.to_datetime(sched["start_date"], utc=True))
    s = s[(s["completed"] == True) & (s["date"] < before_utc) & s["home_points"].notna()]  # noqa: E712
    res = defaultdict(list)
    for r in s.sort_values("date").itertuples():
        hw = r.home_points > r.away_points
        res[int(r.home_id)].append(("W" if hw else "L", r.date))
        res[int(r.away_id)].append(("L" if hw else "W", r.date))
    out = {}
    for t, lst in res.items():
        w = sum(1 for x in lst if x[0] == "W")
        out[t] = {"record": f"{w}-{len(lst) - w}", "last2": [x[0] for x in lst[-2:]], "last3": [x[0] for x in lst[-3:]],
                  "last_date": lst[-1][1]}
    return out


def players(pbp: pd.DataFrame, week: int) -> dict:
    """{team_id: {"qb": {...}, "key": [{"name", "pos"}]}} from plays before `week`.

    Regular QB = most dropbacks (passes + sacks); key players = that QB, the top rusher and the two
    most-targeted receivers (college has no snap counts in the free data). QB rank = EPA per
    dropback among QBs with 100+ dropbacks (1 = best).
    """
    p = pbp[(pbp["week"] < week) & pbp["EPA"].notna()]
    out, qbs = {}, {}
    for team, g in p.groupby("pos_team_id"):
        team = int(team)
        drop = g[(g["pass"] == 1) & g["passer_player_name"].notna()]
        key = []
        if len(drop):
            c = drop.groupby("passer_player_name").agg(n=("EPA", "size"), epa=("EPA", "mean")).sort_values("n", ascending=False)
            name = c.index[0]
            qbs[team] = {"regular": name, "dropbacks": int(c.iloc[0]["n"]), "epa_db": round(float(c.iloc[0]["epa"]), 3)}
            key.append({"name": name, "pos": "QB"})
        ru = g[(g["rush"] == 1) & g["rusher_player_name"].notna()]
        ru = ru[~ru["rusher_player_name"].isin([k["name"] for k in key])]
        if len(ru):
            key.append({"name": ru["rusher_player_name"].value_counts().index[0], "pos": "RB"})
        rc = g[g["receiver_player_name"].notna()]["receiver_player_name"].value_counts()
        key += [{"name": n, "pos": "WR"} for n in list(rc.index[:2])]
        out[team] = {"qb": qbs.get(team), "key": key}
    ranked = {t: q["epa_db"] for t, q in qbs.items() if q["dropbacks"] >= 100}
    for t, r in _rank(ranked, ascending=False).items():
        out[t]["qb"]["rank"] = r
    return out
