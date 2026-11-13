"""One-off NHL backtest (owner Nov 13, 2026), two regular seasons (2024-25, 2025-26):

  1. Range: games where a top-7 team (our consensus proxy, see below) plays a bottom-7 team. How often does the
     top-7 team win, and at what price (closing DraftKings moneyline via ESPN)?
  2. Analysis Score inside the range: win rate by score band vs price -> Qualified / Watch list cutoffs.
  3. Live: how often a lead holds at the end of the 1st and 2nd and at 10:00, 8:00, 5:00 and 3:00 left in the 3rd,
     by lead size, for the range favorite and for every favorite -> the 3rd-period decision time and minimum lead.
     ESPN has no hockey win probability, so there is no fair in-game price to compare with: the live Kalshi price
     log is what will answer whether those leads are ever sold cheap.
  4. Rank stability: how early in the season the ranks settle (an early-season hold, as college basketball).

Power rank proxy: published power rankings have no free history, so the backtest ranks teams by our own daily
rank (average of the 5v5 expected-goals share rank and the points-% rank), as of the day before each game.

    python scripts/nhl_backtest.py DIR     # DIR: pbp_<y>.parquet, sched_<y>.parquet, odds_<y>.csv (dev-nhl-samples)
Writes data/nhl/backtest.json and data/nhl/backtest_games.csv.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nhl import metrics as M  # noqa: E402
from nhl import scoring as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "nhl")
SEASONS = (2025, 2026)
# game_seconds at each checkpoint (regulation = 3600)
MARKS = {"end1": 1200, "end2": 2400, "p3_10": 3000, "p3_8": 3120, "p3_5": 3300, "p3_3": 3420}
ESPN_TO_NHL = {"NJ": "NJD", "SJ": "SJS", "TB": "TBL", "LA": "LAK", "UTAH": "UTA", "WSH": "WSH", "MON": "MTL", "CLB": "CBJ"}


def implied(ml):
    ml = float(ml)
    return -ml / (-ml + 100) if ml < 0 else 100 / (ml + 100)


def leads(pbp: pd.DataFrame) -> pd.DataFrame:
    """Home lead at each checkpoint (score after the last event at or before the mark)."""
    p = pbp[["game_id", "game_seconds", "home_score", "away_score", "period_type", "season_type"]].copy()
    p["game_id"] = p["game_id"].ffill().bfill().astype("int64")
    p = p[(p["season_type"] == "R") & (p["period_type"] != "SHOOTOUT")].sort_values(["game_id", "game_seconds"], kind="stable")
    out = {}
    for gid, g in p.groupby("game_id"):
        sec, hs, as_ = g["game_seconds"].to_numpy(), g["home_score"].to_numpy(), g["away_score"].to_numpy()
        if not len(sec) or sec.max() < 3420:
            continue
        row = {}
        for k, t in MARKS.items():
            i = np.searchsorted(sec, t, side="right") - 1
            row[k] = int(hs[i] - as_[i]) if i >= 0 else 0
        out[gid] = row
    return pd.DataFrame.from_dict(out, orient="index")


def season(d: str, y: int) -> pd.DataFrame:
    pbp = pd.read_parquet(os.path.join(d, f"pbp_{y}.parquet"), columns=M.PBP_COLS)
    sched = pd.read_parquet(os.path.join(d, f"sched_{y}.parquet"))
    rows = M.attach_results(M.game_rows(pbp), sched)
    lead = leads(pbp)
    odds = pd.DataFrame()
    f = os.path.join(d, f"odds_{y}.csv")
    if os.path.exists(f):
        odds = pd.read_csv(f)
        for c in ("home", "away"):
            odds[c] = odds[c].map(lambda a: ESPN_TO_NHL.get(a, a))
        odds = odds.drop_duplicates(["date", "home", "away"]).set_index(["date", "home", "away"])
    home = rows[rows["site"] == "home"].set_index("game_id")
    cache, out = {}, []
    for gid, g in home.sort_values("date").iterrows():
        day = g["date"]
        if day not in cache:
            tm = M.compute(rows, before=day)
            cache[day] = (tm, M.own_rank(tm) if len(tm) == 32 else {})
        tm, own = cache[day]
        h, a = g["team"], g["opp"]
        if h not in tm or a not in tm or not own:
            continue
        rh, ra = own[h], own[a]
        o = odds.loc[(day, h, a)] if len(odds) and (day, h, a) in odds.index else None
        hml = o["home_ml"] if o is not None else None
        aml = o["away_ml"] if o is not None else None
        ok_ml = hml is not None and aml is not None and pd.notna(hml) and pd.notna(aml)
        ph, pa = (implied(hml), implied(aml)) if ok_ml else (None, None)
        in_range = (rh <= S.TOP and ra >= S.BOTTOM_FROM) or (ra <= S.TOP and rh >= S.BOTTOM_FROM)
        # favorite: the top-7 team in range games, else the moneyline favorite
        if in_range:
            fav_home = rh <= S.TOP
        elif ok_ml:
            fav_home = ph >= pa
        else:
            fav_home = rh < ra
        fav, dog = (h, a) if fav_home else (a, h)
        win = bool(g["win"]) if fav_home else not bool(g["win"])
        fg = rows[(rows["game_id"] == gid) & (rows["team"] == fav)].iloc[0]
        dg = rows[(rows["game_id"] == gid) & (rows["team"] == dog)].iloc[0]
        ft, dt = dict(tm[fav]), dict(tm[dog])
        ft["power"], dt["power"] = {"rank": own[fav]}, {"rank": own[dog]}
        ft["b2b"], dt["b2b"] = M.b2b(rows, fav, day), M.b2b(rows, dog, day)
        ft["goalie_status"] = "regular" if str(int(fg["goalie"])) == ft.get("regular_goalie") else "backup"
        dt["goalie_status"] = "regular" if str(int(dg["goalie"])) == dt.get("regular_goalie") else "backup"
        ft["injuries_known"] = dt["injuries_known"] = True
        sc = S.score_game({"favorite": "home" if fav_home else "away"}, ft, dt, day, in_range=in_range)
        row = {"season": y, "game_id": gid, "date": day, "fav": fav, "dog": dog, "fav_home": fav_home, "fav_rank": own[fav],
               "dog_rank": own[dog], "in_range": in_range, "score": sc["score"], "tier": sc["tier"], "routes": sc["route"],
               "fav_goalie": ft["goalie_status"], "dog_goalie": dt["goalie_status"], "fav_b2b": ft["b2b"], "dog_b2b": dt["b2b"],
               "fav_games": ft["games"], "fav_won": win, "extra": bool(g["extra"]),
               "fav_price": (ph if fav_home else pa) if ok_ml else None,
               "fav_fair": ((ph if fav_home else pa) / (ph + pa)) if ok_ml else None,
               "ml_fav_is_fav": (((ph >= pa) == fav_home) if ok_ml else None)}
        if gid in lead.index:
            for k in MARKS:
                row[f"{k}_lead"] = int(lead.loc[gid, k]) * (1 if fav_home else -1)
        out.append(row)
    return pd.DataFrame(out)


def stat(x: pd.DataFrame) -> dict:
    n = len(x)
    if not n:
        return {"n": 0}
    p = x["fav_price"].dropna()
    r = {"n": n, "win": round(float(x["fav_won"].mean()), 3)}
    if len(p):
        y = x.dropna(subset=["fav_price"])
        r.update(price=round(float(p.mean()), 3), edge_vs_price=round(float(y["fav_won"].mean() - p.mean()), 3),
                 fair=round(float(y["fav_fair"].mean()), 3), priced_n=len(y))
    return r


def holds(df: pd.DataFrame) -> dict:
    out = {}
    for k in MARKS:
        c = f"{k}_lead"
        if c not in df:
            continue
        x = df.dropna(subset=[c])
        r = {}
        for lab, lo, hi in (("trail 1", -1, -1), ("tied", 0, 0), ("up 1", 1, 1), ("up 2", 2, 2), ("up 3+", 3, 99)):
            y = x[(x[c] >= lo) & (x[c] <= hi)]
            r[lab] = {"n": len(y), "win": round(float(y["fav_won"].mean()), 3) if len(y) else None,
                      "pregame_price": round(float(y["fav_price"].dropna().mean()), 3) if y["fav_price"].notna().any() else None}
        out[k] = r
    return out


def stability(d: str, y: int) -> dict:
    pbp = pd.read_parquet(os.path.join(d, f"pbp_{y}.parquet"), columns=M.PBP_COLS)
    rows = M.attach_results(M.game_rows(pbp), pd.read_parquet(os.path.join(d, f"sched_{y}.parquet")))
    final = M.own_rank(M.compute(rows))
    out = {}
    for day in pd.date_range(pd.Timestamp(rows["date"].min()) + pd.Timedelta(days=14), pd.Timestamp(rows["date"].max()), freq="7D"):
        ds = day.strftime("%Y-%m-%d")
        tm = M.compute(rows, before=ds)
        if len(tm) < 32:
            continue
        own = M.own_rank(tm)
        a, b = pd.Series([own[t] for t in final]), pd.Series([final[t] for t in final])
        top = [t for t in final if final[t] <= 7]
        bot = [t for t in final if final[t] >= 26]
        out[ds] = {"spearman": round(float(a.corr(b, method="spearman")), 3), "games": float(np.median([v["games"] for v in tm.values()])),
                   "top7_still_top7": sum(1 for t in top if own[t] <= 7), "bottom7_still_bottom7": sum(1 for t in bot if own[t] >= 26)}
    return out


def main(d: str) -> None:
    df = pd.concat([season(d, y) for y in SEASONS if os.path.exists(os.path.join(d, f"pbp_{y}.parquet"))], ignore_index=True)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "backtest_games.csv"), index=False)
    rng = df[df["in_range"]]
    res = {"seasons": SEASONS, "games": stat(df), "range": stat(rng), "outside_range": stat(df[~df["in_range"]]),
           "range_by_tier": {t: stat(rng[rng["tier"] == t]) for t in ("qualified", "watch", "out")},
           "range_by_score": {b: stat(rng[(rng["score"] >= lo) & (rng["score"] <= hi)]) for b, lo, hi in
                              (("0-13", 0, 13), ("14-17", 14, 17), ("18-21", 18, 21), ("22-25", 22, 25), ("26-29", 26, 29), ("30+", 30, 40))},
           "range_fav_goalie": {k: stat(rng[rng["fav_goalie"] == k]) for k in ("regular", "backup")},
           "range_dog_goalie": {k: stat(rng[rng["dog_goalie"] == k]) for k in ("regular", "backup")},
           "range_fav_b2b": {str(k): stat(rng[rng["fav_b2b"] == k]) for k in (True, False)},
           "range_dog_b2b": {str(k): stat(rng[rng["dog_b2b"] == k]) for k in (True, False)},
           "range_by_month": {m: stat(g) for m, g in rng.assign(m=pd.to_datetime(rng["date"]).dt.strftime("%m")).groupby("m")},
           "holds_range": holds(rng), "holds_all_favorites": holds(df),
           "by_season": {str(y): {"range": stat(g[g["in_range"]]), "all": stat(g)} for y, g in df.groupby("season")},
           "rank_stability": {str(y): stability(d, y) for y in SEASONS if os.path.exists(os.path.join(d, f"pbp_{y}.parquet"))}}
    with open(os.path.join(OUT, "backtest.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(json.dumps({k: res[k] for k in ("games", "range", "outside_range", "range_by_tier", "range_by_score")}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
