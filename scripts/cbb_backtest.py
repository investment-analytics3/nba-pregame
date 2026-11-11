"""One-off college basketball backtest (owner Nov 11, 2026): do the NBA-style tiers pick winners better than
the price, how soon in the season are the ranks trustworthy (the early-season cutover), and how do big leads at the
2nd-half 10:00 mark (the end-of-3rd equivalent) hold against ESPN's in-game win probability?

Data (all free): sportsdataverse team box scores + play-by-play (ESPN pregame and in-game win probability) and the
closing DraftKings line from each game's archived ESPN summary (scripts/cbb_odds_history.py).

    python scripts/cbb_backtest.py DIR      # DIR holds team_box_<y>.parquet, pbp_<y>.parquet, sched_<y>.parquet, odds_<y>.csv
                                            # (odds_2026.csv = data/cbb/backtest_odds_2026.csv; 2024-25 archives have no lines)
Writes data/cbb/backtest.json and data/cbb/backtest_games.csv.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cbb import metrics as M  # noqa: E402
from cbb import scoring as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "cbb")
SEASONS = (2025, 2026)


def implied(ml):
    ml = float(ml)
    return -ml / (-ml + 100) if ml < 0 else 100 / (ml + 100)


def checkpoints(pbp: pd.DataFrame) -> dict:
    """{game_id: {"half": (home, away, wp), "h2_10": ..., "h2_4": ...}} — score and home WP at each mark."""
    p = pbp[["game_id", "period_number", "end_game_seconds_remaining", "home_score", "away_score", "home_win_prob"]].copy()
    p = p.sort_values(["game_id", "end_game_seconds_remaining"], ascending=[True, False])
    out = {}
    for gid, g in p.groupby("game_id"):
        d = {}
        for key, sec in (("half", 1200), ("h2_10", 600), ("h2_4", 240)):
            x = g[g["end_game_seconds_remaining"] <= sec]
            x = x[x["period_number"] <= 2]
            if len(x):
                r = x.iloc[0]
                d[key] = (int(r.home_score), int(r.away_score), float(r.home_win_prob) if pd.notna(r.home_win_prob) else None)
        out[int(gid)] = d
    return out


def season_rows(d: str, y: int) -> list[dict]:
    tb = pd.read_parquet(os.path.join(d, f"team_box_{y}.parquet"))
    rows = M.box_rows(tb)
    d1 = M.d1_from_counts(rows)
    n = len(d1)
    odds = pd.read_csv(os.path.join(d, f"odds_{y}.csv"), dtype={"game_id": int}).dropna(subset=["spread"])
    odds = odds[odds["spread"].abs().between(S.SPREAD_MIN, S.SPREAD_MAX)]
    sch = pd.read_parquet(os.path.join(d, f"sched_{y}.parquet"), columns=["game_id", "neutral_site", "home_current_rank", "away_current_rank",
                                                                            "season_type", "home_id", "away_id"])
    sch["game_id"] = sch["game_id"].astype(int)
    sch = sch.drop_duplicates("game_id").set_index("game_id")
    pbp = pd.read_parquet(os.path.join(d, f"pbp_{y}.parquet"),
                          columns=["game_id", "period_number", "end_game_seconds_remaining", "home_score", "away_score", "home_win_prob"])
    pbp = pbp[pbp["game_id"].astype(int).isin(set(odds["game_id"]))]
    cps = checkpoints(pbp)
    games = rows[rows["team_home_away"] == "home"].set_index("game_id")
    cache, out = {}, []
    for o in odds.itertuples():
        gid = int(o.game_id)
        if gid not in games.index or gid not in sch.index:
            continue
        g = games.loc[gid]
        if isinstance(g, pd.DataFrame):
            g = g.iloc[0]
        h, a = int(g.team_id), int(g.opponent_team_id)
        if h not in d1 or a not in d1:
            continue
        s = sch.loc[gid]
        if int(s.season_type) != 2:          # regular season only (no conference/NCAA tournaments in the test)
            continue
        day = g.game_date
        if day not in cache:
            cache[day] = M.compute(rows, d1, before=day)
        tm = cache[day]
        if h not in tm or a not in tm:
            continue
        home_fav = o.spread < 0
        fav_id, dog_id = (h, a) if home_fav else (a, h)
        own = {t: v["season"]["ranks"].get("net") for t, v in tm.items()}
        ranks = {"home": s.home_current_rank, "away": s.away_current_rank}
        fav, dog = dict(tm[fav_id]), dict(tm[dog_id])
        for t, tid, side in ((fav, fav_id, "home" if home_fav else "away"), (dog, dog_id, "away" if home_fav else "home")):
            ap = ranks[side]
            t["power"] = {"rank": own.get(tid), "own_net": own.get(tid), "ap": int(ap) if pd.notna(ap) and 0 < ap <= 25 else None}
            t["b2b"] = M.b2b(rows, tid, day)
            t["injuries_known"] = True
        game = {"favorite": "home" if home_fav else "away", "neutral": bool(s.neutral_site)}
        sc = S.score_game(game, fav, dog, "2000-12-31", n)   # date past the cutover: raw tiers (the cap is studied below)
        fav_pts = int(g.pts) if home_fav else int(g.o_pts)
        dog_pts = int(g.o_pts) if home_fav else int(g.pts)
        fml, dml = (o.home_ml, o.away_ml) if home_fav else (o.away_ml, o.home_ml)
        try:
            pf, pd_ = implied(fml), implied(dml)
            fair = pf / (pf + pd_)
        except (TypeError, ValueError):
            pf = fair = None
        row = {"season": y, "game_id": gid, "date": day, "fav": fav["abbr"], "dog": dog["abbr"], "spread": abs(o.spread),
               "fav_home": home_fav and not bool(s.neutral_site), "neutral": bool(s.neutral_site),
               "fav_games": fav["games"], "dog_games": dog["games"], "score": sc["score"], "tier": sc["tier"],
               "routes": sc["route"], "power_fail": not sc["rules"][3]["pass"], "fav_ml": fml, "fav_price": pf, "fav_fair": fair,
               "fav_won": fav_pts > dog_pts, "margin": fav_pts - dog_pts, "cover": fav_pts - dog_pts > abs(o.spread)}
        for k, v in cps.get(gid, {}).items():
            hs, as_, wp = v
            row[f"{k}_lead"] = (hs - as_) if home_fav else (as_ - hs)
            row[f"{k}_wp"] = (wp if home_fav else (1 - wp)) if wp is not None else None
        out.append(row)
    return out


def summarize(df: pd.DataFrame) -> dict:
    def stat(x):
        n = len(x)
        if not n:
            return {"n": 0}
        return {"n": n, "win": round(x["fav_won"].mean(), 3), "fair": round(x["fav_fair"].mean(), 3) if x["fav_fair"].notna().any() else None,
                "edge": round(x["fav_won"].mean() - x["fav_fair"].mean(), 3) if x["fav_fair"].notna().any() else None,
                "cover": round(x["cover"].mean(), 3),
                "price": round(x["fav_price"].mean(), 3) if x["fav_price"].notna().any() else None,
                "edge_vs_price": round(x["fav_won"].mean() - x["fav_price"].mean(), 3) if x["fav_price"].notna().any() else None}
    res = {"games": stat(df), "by_tier": {t: stat(df[df.tier == t]) for t in ("qualified", "watch", "out")},
           "power_gated": stat(df[df.power_fail])}
    df = df.assign(month=pd.to_datetime(df["date"]).dt.strftime("%m"))
    res["qualified_by_month"] = {m: stat(g[g.tier == "qualified"]) for m, g in df.groupby("month")}
    res["all_by_month"] = {m: stat(g) for m, g in df.groupby("month")}
    df = df.assign(half_month=pd.to_datetime(df["date"]).apply(lambda d: f"{d:%m}-{'01' if d.day < 16 else '16'}"))
    res["qualified_by_half_month"] = {m: stat(g[g.tier == "qualified"]) for m, g in df.groupby("half_month")}
    bins = [0, 4, 7, 10, 13, 99]
    df = df.assign(gp=pd.cut(df[["fav_games", "dog_games"]].min(axis=1), bins, labels=["1-4", "5-7", "8-10", "11-13", "14+"]))
    res["qualified_by_min_games"] = {str(k): stat(g[g.tier == "qualified"]) for k, g in df.groupby("gp", observed=True)}
    res["score_bands"] = {b: stat(df[(df.score >= lo) & (df.score <= hi)]) for b, lo, hi in
                          (("0-17", 0, 17), ("18-23", 18, 23), ("24-27", 24, 27), ("28-31", 28, 31), ("32+", 32, 40))}
    res["route_b_only"] = stat(df[(df.tier == "qualified") & (df.routes == "Last-3 mismatch")])
    cp = {}
    for k in ("half", "h2_10", "h2_4"):
        if f"{k}_lead" not in df:
            continue
        x = df.dropna(subset=[f"{k}_lead"])
        c = {}
        for lab, lo, hi in (("trail", -99, -1), ("0-4", 0, 4), ("5-9", 5, 9), ("10-14", 10, 14), ("15+", 15, 99)):
            y = x[(x[f"{k}_lead"] >= lo) & (x[f"{k}_lead"] <= hi)]
            c[lab] = {"n": len(y), "win": round(y["fav_won"].mean(), 3) if len(y) else None,
                      "wp": round(y[f"{k}_wp"].mean(), 3) if len(y) and y[f"{k}_wp"].notna().any() else None}
        y = x[(x[f"{k}_lead"] >= 10) & x["tier"].isin(["qualified", "watch"])]
        c["10+_tracked"] = {"n": len(y), "win": round(y["fav_won"].mean(), 3) if len(y) else None,
                            "wp": round(y[f"{k}_wp"].mean(), 3) if len(y) else None}
        cp[k] = c
    res["checkpoints"] = cp
    return res


def rank_stability(d: str, y: int) -> dict:
    """How well a date's net-rating ranks predict the end-of-season ranks (Spearman), weekly.
    Decides the early-season cutover: the date the ranks settle."""
    rows = M.box_rows(pd.read_parquet(os.path.join(d, f"team_box_{y}.parquet")))
    d1 = M.d1_from_counts(rows)
    end = rows["game_date"].max()
    final = {t: v["season"]["ranks"]["net"] for t, v in M.compute(rows, d1, before="9999").items() if v["season"]["ranks"].get("net")}
    out = {}
    start = pd.Timestamp(rows["game_date"].min()) + pd.Timedelta(days=14)
    for day in pd.date_range(start, pd.Timestamp(end), freq="7D"):
        ds = day.strftime("%Y-%m-%d")
        tm = M.compute(rows, d1, before=ds)
        common = [t for t in tm if t in final and tm[t]["season"]["ranks"].get("net")]
        if len(common) < 100:
            continue
        a = pd.Series([tm[t]["season"]["ranks"]["net"] for t in common]).rank()
        b = pd.Series([final[t] for t in common]).rank()
        top = [t for t in common if final[t] <= 50]
        games = pd.Series([tm[t]["games"] for t in common])
        out[ds] = {"spearman": round(float(a.corr(b)), 3), "teams": len(common), "median_d1_games": float(games.median()),
                   "top50_final_in_top50_now": round(sum(1 for t in top if tm[t]["season"]["ranks"]["net"] <= 50) / max(len(top), 1), 3)}
    return out


def main(d: str) -> None:
    rows, stab = [], {}
    for y in SEASONS:
        f = os.path.join(d, f"odds_{y}.csv")
        if os.path.exists(f) and pd.read_csv(f)["spread"].notna().any():
            rows += season_rows(d, y)
        if os.path.exists(os.path.join(d, f"team_box_{y}.parquet")):
            stab[str(y)] = rank_stability(d, y)
    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "backtest_games.csv"), index=False)
    res = {"seasons": sorted(df["season"].unique().tolist()), "all": summarize(df),
           "by_season": {str(y): summarize(g) for y, g in df.groupby("season")}, "rank_stability": stab}
    with open(os.path.join(OUT, "backtest.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(json.dumps({k: res["all"][k] for k in ("games", "by_tier", "score_bands")}, indent=1, default=str))
    print(json.dumps(stab, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
