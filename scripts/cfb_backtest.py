#!/usr/bin/env python3
"""College football backtest (one-off; not scheduled) for the draft CFB rules (Nov 10 2026).

Replays FBS-vs-FBS regular-season games (weeks 4+) whose spread favorite is -10 to -20, scoring each
game only from plays BEFORE its week (no hindsight), and reports straight-up favorite win rates by tier.

Rules as drafted with the owner (spec "College Football Board"):
  metrics      off EPA/play, off success, def EPA/play allowed, def success allowed (scrimmage rush +
               pass plays; success = EPA > 0), season + last 3 games, ranked among FBS teams (138)
  hero score   40: fav 16 (top 10 = 2, 11-25 = 1), dog 16 (ranks 26+ = 1, 114+ = 2), health 4, home 1,
               rest 1, form 2 (fav last 2)
  tiers        Qualified = score 28+ OR last-3 mismatch (fav last-3 top 25 in >= 3 of 4, dog last-3
               rank 114+ in >= 3 of 4, fav season top half in all four, dog season top 25 in none);
               Watch list = 18-27; No track = the rest
  power gate   opponent top 25 in any ranking -> No track. Backtest proxy (no historical AP/CFP/FPI
               in the free data): opponent top 25 in pregame Elo (CFBD, in cfbfastR schedules) or in our
               own season net EPA
  injury gate  no historical injury data: assumed clear, health = 4 (overstates scores slightly)

Data: sportsdataverse espn_cfb_pbp release (ESPN play-by-play with EPA and ESPN's pregame spread) and
cfbfastR-data schedules (FBS flags, neutral sites, dates, pregame Elo).

    python scripts/cfb_backtest.py [--cache DIR] [--seasons 2024 2025 2026] [--out data/cfb/backtest.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request
from collections import defaultdict

import numpy as np
import pandas as pd

PBP_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_{y}.parquet"
SCHED_URL = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/schedules/csv/cfb_schedules_{y}.csv"
METRICS = ("off_epa", "off_sr", "def_epa", "def_sr")
LABELS = {"off_epa": "Off EPA/play", "off_sr": "Off success", "def_epa": "Def EPA allowed", "def_sr": "Def success allowed"}
SPREAD_MIN, SPREAD_MAX = 10.0, 20.0
STRONG, ELITE, WEAK_FROM = 25, 10, 114
QUALIFY, WATCH = 28, 18
FIRST_WEEK = 4
PBP_COLS = ["game_id", "week", "seasonType", "pos_team_id", "def_pos_team_id", "EPA", "rush", "pass",
            "scrimmage_play", "homeTeamId", "awayTeamId", "homeTeamAbbrev", "awayTeamAbbrev", "gameSpread",
            "homeFavorite", "gameSpreadAvailable", "homeFinalScore", "awayFinalScore", "status_type_completed"]


def fetch(url: str, path: str) -> str:
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)
    return path


def load(cache: str, y: int):
    pbp = pd.read_parquet(fetch(PBP_URL.format(y=y), os.path.join(cache, f"pbp_{y}.parquet")), columns=PBP_COLS)
    sched_path = os.path.join(cache, f"sched_{y}.csv")
    local = f"/home/claude/sportsdataverse/cfbfastr-data/schedules/csv/cfb_schedules_{y}.csv"
    sched = pd.read_csv(local if os.path.exists(local) else fetch(SCHED_URL.format(y=y), sched_path))
    return pbp, sched


def team_games(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team): offense and defense EPA sums, success counts, play counts."""
    p = pbp[(pbp["scrimmage_play"] == True) & ((pbp["rush"] == 1) | (pbp["pass"] == 1)) & pbp["EPA"].notna()].copy()  # noqa: E712
    p["succ"] = (p["EPA"] > 0).astype(int)
    off = p.groupby(["game_id", "week", "pos_team_id"]).agg(off_epa_sum=("EPA", "sum"), off_succ=("succ", "sum"),
                                                            off_n=("EPA", "size")).reset_index()
    off = off.rename(columns={"pos_team_id": "team"})
    de = p.groupby(["game_id", "pos_team_id", "def_pos_team_id"]).agg(def_epa_sum=("EPA", "sum"), def_succ=("succ", "sum"),
                                                                       def_n=("EPA", "size")).reset_index()
    de = de.rename(columns={"def_pos_team_id": "team"}).drop(columns="pos_team_id")
    tg = off.merge(de, on=["game_id", "team"], how="inner")
    return tg


def rank(values: dict, ascending: bool) -> dict:
    order = sorted(values, key=lambda t: values[t], reverse=not ascending)
    return {t: i + 1 for i, t in enumerate(order)}


def metrics_asof(tg: pd.DataFrame, week: int, fbs: set) -> dict:
    """{team: {"season": {m: v}, "last3": {...}, "ranks": {"season": {m: r}, "last3": {...}}, "net"}} as of before week."""
    prior = tg[tg["week"] < week].sort_values("week")
    out = {}
    for team, g in prior.groupby("team"):
        if team not in fbs or len(g) < 2:
            continue
        def agg(x):
            return {"off_epa": x["off_epa_sum"].sum() / max(x["off_n"].sum(), 1),
                    "off_sr": x["off_succ"].sum() / max(x["off_n"].sum(), 1),
                    "def_epa": x["def_epa_sum"].sum() / max(x["def_n"].sum(), 1),
                    "def_sr": x["def_succ"].sum() / max(x["def_n"].sum(), 1)}
        s, l3 = agg(g), agg(g.tail(3))
        out[team] = {"season": s, "last3": l3, "net": s["off_epa"] - s["def_epa"]}
    for w in ("season", "last3"):
        for m in METRICS:
            r = rank({t: v[w][m] for t, v in out.items()}, ascending=m.startswith("def"))
            for t, v in out.items():
                v.setdefault("ranks", {}).setdefault(w, {})[m] = r[t]
    rn = rank({t: v["net"] for t, v in out.items()}, ascending=False)
    for t, v in out.items():
        v["net_rank"] = rn[t]
    return out


def fav_pts(r):
    return 2 if r <= ELITE else (1 if r <= STRONG else 0)


def dog_pts(r):
    return 2 if r >= WEAK_FROM else (1 if r > STRONG else 0)


def score(f, d, home, rest_pt, form_l2):
    fs = sum(fav_pts(f["ranks"][w][m]) for w in ("season", "last3") for m in METRICS)
    ds = sum(dog_pts(d["ranks"][w][m]) for w in ("season", "last3") for m in METRICS)
    w2 = form_l2.count("W")
    form = 2 if (len(form_l2) == 2 and w2 == 2) else (1 if w2 == 1 else 0)
    return fs + ds + 4 + (1 if home else 0) + rest_pt + form


def route_b(f, d, n_fbs):
    half = n_fbs // 2
    f3 = sum(f["ranks"]["last3"][m] <= STRONG for m in METRICS)
    d3 = sum(d["ranks"]["last3"][m] >= WEAK_FROM for m in METRICS)
    floor = all(f["ranks"]["season"][m] <= half for m in METRICS)
    dog_top = any(d["ranks"]["season"][m] <= STRONG for m in METRICS)
    return f3 >= 3 and d3 >= 3 and floor and not dog_top


def season_rows(y: int, cache: str) -> list[dict]:
    pbp, sched = load(cache, y)
    sched = sched[sched["season_type"] == "regular"]
    fbs = set(sched.loc[sched["home_division"] == "fbs", "home_id"]) | set(sched.loc[sched["away_division"] == "fbs", "away_id"])
    tg = team_games(pbp)
    games = pbp.drop_duplicates("game_id").set_index("game_id")
    sch = sched.set_index("game_id")
    sched = sched.assign(date=pd.to_datetime(sched["start_date"], utc=True))
    # results + dates per team, in order
    res = defaultdict(list)                  # team -> [(week, date, W/L)]
    for _, s in sched[sched["completed"] == True].iterrows():  # noqa: E712
        if pd.isna(s["home_points"]) or pd.isna(s["away_points"]):
            continue
        hw = s["home_points"] > s["away_points"]
        res[s["home_id"]].append((s["week"], s["date"], "W" if hw else "L"))
        res[s["away_id"]].append((s["week"], s["date"], "L" if hw else "W"))
    for t in res:
        res[t].sort(key=lambda x: x[1])
    rows = []
    cache_m = {}
    for gid, s in sched.set_index("game_id").iterrows():
        if s["home_division"] != "fbs" or s["away_division"] != "fbs" or s["week"] < FIRST_WEEK or gid not in games.index:
            continue
        g = games.loc[gid]
        if not g["gameSpreadAvailable"] or pd.isna(g["gameSpread"]) or not g["status_type_completed"]:
            continue
        spread = abs(float(g["gameSpread"]))
        if not (SPREAD_MIN <= spread <= SPREAD_MAX):
            continue
        home_fav = bool(g["homeFavorite"])
        fav, dog = (s["home_id"], s["away_id"]) if home_fav else (s["away_id"], s["home_id"])
        wk = int(s["week"])
        m = cache_m.setdefault(wk, metrics_asof(tg, wk, fbs))
        if fav not in m or dog not in m:
            continue
        f, d = m[fav], m[dog]
        date = pd.Timestamp(s["start_date"]).tz_convert("UTC") if pd.Timestamp(s["start_date"]).tzinfo else pd.Timestamp(s["start_date"], tz="UTC")
        def prev(team):
            p = [r for r in res[team] if r[1] < date]
            return p
        pf, pd_ = prev(fav), prev(dog)
        rest_f = (date - pf[-1][1]).days if pf else None
        rest_d = (date - pd_[-1][1]).days if pd_ else None
        rest_pt = 1 if ((rest_f is not None and rest_f >= 13) or
                        (rest_d is not None and rest_d <= 5 and rest_f is not None and rest_f - rest_d >= 2)) else 0
        l2 = [r[2] for r in pf[-2:]]
        neutral = bool(s["neutral_site"])
        sc = score(f, d, home_fav and not neutral, rest_pt, l2)
        rb = route_b(f, d, len(fbs))
        # power gate proxy: dog top 25 in pregame Elo among FBS that week, or in our season net EPA
        elo = sched[(sched["week"] == wk)]
        elos = {}
        for _, e in elo.iterrows():
            if e["home_division"] == "fbs" and not pd.isna(e["home_pregame_elo"]):
                elos[e["home_id"]] = e["home_pregame_elo"]
            if e["away_division"] == "fbs" and not pd.isna(e["away_pregame_elo"]):
                elos[e["away_id"]] = e["away_pregame_elo"]
        elo_rank = rank(elos, ascending=False) if elos else {}
        dog_elo, dog_net = elo_rank.get(dog), d["net_rank"]
        power_fail = (dog_elo is not None and dog_elo <= STRONG) or dog_net <= STRONG
        tier = "qualified" if (sc >= QUALIFY or rb) else ("watch" if sc >= WATCH else "out")
        hp, ap = s["home_points"], s["away_points"]
        fav_pts_, dog_pts_ = (hp, ap) if home_fav else (ap, hp)
        rows.append({"fav_id": int(fav), "dog_id": int(dog), "home_fav": home_fav,
                     "fav_def_ranks": {w: {m: f["ranks"][w][m] for m in ("def_epa", "def_sr")} for w in ("season", "last3")},
                     "season": y, "week": wk, "game_id": int(gid), "fav": g["homeTeamAbbrev"] if home_fav else g["awayTeamAbbrev"],
                     "dog": g["awayTeamAbbrev"] if home_fav else g["homeTeamAbbrev"], "spread": spread, "score": sc,
                     "route_b": rb, "tier_raw": tier, "power_fail": bool(power_fail), "dog_elo_rank": dog_elo,
                     "dog_net_rank": dog_net, "tier": "out" if power_fail else tier,
                     "fav_win": bool(fav_pts_ > dog_pts_), "margin": float(fav_pts_ - dog_pts_),
                     "cover": bool(fav_pts_ - dog_pts_ > spread)})
    return rows


def win_prob_by_spread(all_rows: pd.DataFrame):
    """Logistic fit of favorite win on spread over every band game (the market baseline)."""
    x, yv = all_rows["spread"].to_numpy(), all_rows["fav_win"].astype(float).to_numpy()
    a, b = 0.0, 0.1
    for _ in range(4000):
        p = 1 / (1 + np.exp(-(a + b * x)))
        a -= 0.05 * np.mean(p - yv)
        b -= 0.005 * np.mean((p - yv) * x)
    return lambda s: 1 / (1 + math.exp(-(a + b * s)))


def summarize(rows: list[dict]) -> dict:
    df = pd.DataFrame(rows)
    wp = win_prob_by_spread(df)
    df["expected"] = df["spread"].map(wp)
    def block(x):
        if not len(x):
            return {"games": 0}
        return {"games": int(len(x)), "wins": int(x["fav_win"].sum()), "win_pct": round(float(x["fav_win"].mean()), 3),
                "expected_pct": round(float(x["expected"].mean()), 3),
                "edge_pts": round(float((x["fav_win"].mean() - x["expected"].mean()) * 100), 1),
                "avg_spread": round(float(x["spread"].mean()), 1), "cover_pct": round(float(x["cover"].mean()), 3)}
    out = {"all_band_games": block(df), "by_tier": {t: block(df[df["tier"] == t]) for t in ("qualified", "watch", "out")},
           "by_tier_without_power_gate": {t: block(df[df["tier_raw"] == t]) for t in ("qualified", "watch", "out")},
           "power_gate_blocked": block(df[df["power_fail"] & (df["tier_raw"] != "out")]),
           "qualified_routes": {"score_28": block(df[(df["tier"] == "qualified") & (df["score"] >= QUALIFY)]),
                                "last3_mismatch_only": block(df[(df["tier"] == "qualified") & (df["score"] < QUALIFY)])},
           "by_season": {int(y): {t: block(g[g["tier"] == t]) for t in ("qualified", "watch", "out")} for y, g in df.groupby("season")},
           "by_score_band": {f"{lo}-{hi}": block(df[(df["score"] >= lo) & (df["score"] <= hi)])
                             for lo, hi in ((0, 17), (18, 22), (23, 27), (28, 31), (32, 40))},
           "losses_qualified": df[(df["tier"] == "qualified") & (~df["fav_win"])][
               ["season", "week", "fav", "dog", "spread", "score", "margin"]].to_dict("records")}
    return out, df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/tmp/cfb_cache")
    ap.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--out", default="data/cfb/backtest.json")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    rows = []
    for y in a.seasons:
        r = season_rows(y, a.cache)
        print(f"{y}: {len(r)} band games")
        rows += r
    summary, df = summarize(rows)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"seasons": a.seasons, "rules": __doc__.split("Rules as drafted")[1].split("Data:")[0].strip(),
                   "summary": summary}, f, indent=1, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k != "losses_qualified"}, indent=1))
    print("qualified losses:", len(summary["losses_qualified"]))
    df.to_csv(a.out.replace(".json", "_games.csv"), index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
