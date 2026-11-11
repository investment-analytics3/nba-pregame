#!/usr/bin/env python3
"""College football early-entry backtest (one-off): can we get in before the end of the 3rd?

For FBS-vs-FBS favorites at -10 to -20 (same games as scripts/cfb_backtest.py), at the end of the
1st quarter and at halftime: favorite win rate by lead size, with and without in-game checks, compared
with the in-game win probability at that moment (sportsdataverse/ESPN WP model; the closest free
stand-in for a market price: no historical Kalshi prices exist for these games).

In-game checks (plays so far, scrimmage runs + passes):
  control 3 of 5   favorite better in EPA/play, success rate, 3rd-down conversion rate, explosive plays
                   (EPA_explosive), and fewer turnovers lost (ties count for neither side)
  defense          favorite's season AND last-3 defensive EPA and success-allowed ranks all top 25 of 138
  tracked          pregame tier Qualified or Watch list

    python scripts/cfb_early_backtest.py [--cache DIR] [--seasons 2022 ... 2026] [--out data/cfb/early_backtest.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfb_backtest as bt  # noqa: E402

COLS = ["game_id", "period", "homeScore", "awayScore", "home_wp_after", "pos_team_id", "EPA", "rush", "pass",
        "scrimmage_play", "down", "first_down_created", "EPA_explosive", "is_pos_team_turnover", "is_turnover"]
CHECKPOINTS = {"end_q1": 1, "half": 2, "end_q3": 3}
LEADS = (7, 10, 14, 17, 21)


def checkpoint_state(g: pd.DataFrame, period: int, fav: int, home_fav: bool) -> dict | None:
    x = g[g["period"] <= period]
    if x.empty or g["period"].max() <= period:
        return None
    last = x.iloc[-1]
    diff = last["homeScore"] - last["awayScore"]
    wp = last["home_wp_after"]
    s = {"lead": float(diff if home_fav else -diff), "fav_wp": float(wp if home_fav else 1 - wp)}
    p = x[(x["scrimmage_play"] == True) & ((x["rush"] == 1) | (x["pass"] == 1)) & x["EPA"].notna()]  # noqa: E712
    f, d = p[p["pos_team_id"] == fav], p[p["pos_team_id"] != fav]
    def rate(t, col):
        return t[col].mean() if len(t) else None
    def third(t):
        t3 = t[t["down"] == 3]
        return t3["first_down_created"].astype(float).mean() if len(t3) else None
    to = x[x["is_turnover"] == True]  # noqa: E712
    fav_to = int((to["pos_team_id"] == fav).sum())
    dog_to = int((to["pos_team_id"] != fav).sum())
    cats = {
        "epa": (rate(f, "EPA"), rate(d, "EPA")),
        "success": ((f["EPA"] > 0).mean() if len(f) else None, (d["EPA"] > 0).mean() if len(d) else None),
        "third_down": (third(f), third(d)),
        "explosive": (float(f["EPA_explosive"].fillna(0).astype(float).sum()), float(d["EPA_explosive"].fillna(0).astype(float).sum())),
    }
    won = sum(1 for a, b in cats.values() if a is not None and b is not None and a > b)
    won += 1 if fav_to < dog_to else 0
    s.update(control_cats=won, control=won >= 3)
    return s


def block(x: pd.DataFrame) -> dict:
    if not len(x):
        return {"games": 0}
    return {"games": int(len(x)), "wins": int(x["fav_win"].sum()), "win_pct": round(float(x["fav_win"].mean()), 3),
            "price_proxy": round(float(x["wp"].mean()), 3),
            "edge_pts": round(float((x["fav_win"].mean() - x["wp"].mean()) * 100), 1),
            "losses": int((~x["fav_win"]).sum())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/tmp/cfb_cache")
    ap.add_argument("--seasons", nargs="+", type=int, default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--out", default="data/cfb/early_backtest.json")
    a = ap.parse_args()
    rows = []
    for y in a.seasons:
        games = bt.season_rows(y, a.cache)
        ids = {g["game_id"] for g in games}
        pbp = pd.read_parquet(os.path.join(a.cache, f"pbp_{y}.parquet"), columns=COLS)
        pbp = pbp[pbp["game_id"].isin(ids)]
        by = dict(tuple(pbp.groupby("game_id")))
        for g in games:
            gp = by.get(g["game_id"])
            if gp is None:
                continue
            dr = g["fav_def_ranks"]
            defense = all(dr[w][m] <= bt.STRONG for w in ("season", "last3") for m in ("def_epa", "def_sr"))
            for cp, per in CHECKPOINTS.items():
                st = checkpoint_state(gp, per, g["fav_id"], g["home_fav"])
                if st:
                    rows.append({**{k: g[k] for k in ("season", "week", "game_id", "fav", "dog", "spread", "tier", "fav_win")},
                                 "checkpoint": cp, "lead": st["lead"], "wp": st["fav_wp"], "control": st["control"],
                                 "control_cats": st["control_cats"], "defense": defense})
        print(f"{y}: {len(games)} band games")
    df = pd.DataFrame(rows)
    out = {"seasons": a.seasons, "games": int(df["game_id"].nunique()), "checkpoints": {}}
    for cp in CHECKPOINTS:
        c = df[df["checkpoint"] == cp]
        res = {"all_games": block(c), "by_lead": {}, "lead_bands": {}}
        for t in LEADS:
            x = c[c["lead"] >= t]
            res["by_lead"][f"{t}+"] = {"all": block(x), "control": block(x[x["control"]]),
                                       "no_control": block(x[~x["control"]]),
                                       "control_and_defense": block(x[x["control"] & x["defense"]]),
                                       "tracked_pregame": block(x[x["tier"].isin(["qualified", "watch"])])}
        for lo, hi in ((7, 9), (10, 13), (14, 16), (17, 20), (21, 99)):
            x = c[(c["lead"] >= lo) & (c["lead"] <= hi)]
            res["lead_bands"][f"{lo}-{hi}"] = {"all": block(x), "control": block(x[x["control"]])}
        out["checkpoints"][cp] = res
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    df.to_csv(a.out.replace(".json", "_rows.csv"), index=False)
    for cp in CHECKPOINTS:
        print(f"\n== {cp}")
        for k, v in out["checkpoints"][cp]["by_lead"].items():
            print(k, {kk: (vv.get("games"), vv.get("win_pct"), vv.get("price_proxy"), vv.get("edge_pts")) for kk, vv in v.items()})
        for k, v in out["checkpoints"][cp]["lead_bands"].items():
            print("  band", k, {kk: (vv.get("games"), vv.get("win_pct"), vv.get("price_proxy"), vv.get("edge_pts")) for kk, vv in v.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
