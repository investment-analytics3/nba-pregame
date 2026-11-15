"""One-off NFL matchup backtest (owner Nov 15, 2026). Regular seasons 2021-26 from nflverse (play_by_play_<y>.parquet as
pbp_<y>.parquet + games.csv in DIR). Ranks 1-32 as of each week (games before it only), season and last 3; favorite =
spread favorite (games.csv spread_line). Writes one row per game (ranks, spread, result, favorite lead at half / end of 3rd).

    python scripts/nfl_matchup_backtest.py DIR      -> DIR/nfl_matchups.csv

Results (1,216 games): EPA/play matchups add nothing once the spread is known (any of four 70% vs 71% spread-implied).
Last-3 success rate, favorite top 10 vs underdog 23-32, spread 7+: defense vs defense, defense vs offense or offense vs
defense won 140-18 (89%, spread predicts 82%) vs 109-36 (75%) without; leading at halftime 105/107, after the 3rd
118/120. Offense vs offense alone added nothing; under 7 points nothing separated. Information only: nfl/scoring.py
MATCHUP_PAIRS.
"""
import sys, os
import pandas as pd, numpy as np
D = sys.argv[1]
g = pd.read_csv(os.path.join(D, "games.csv"))
g = g[(g.game_type == "REG") & g.home_score.notna() & (g.season >= 2021)]
rows = []
cps = {}
for y in range(2021, 2027):
    p = pd.read_parquet(os.path.join(D, f"pbp_{y}.parquet"),
                        columns=["game_id", "season", "season_type", "week", "posteam", "defteam", "pass", "rush", "epa", "success",
                                 "qtr", "total_home_score", "total_away_score", "play_id"])
    p = p[p.season_type == "REG"]
    for gid, x in p.sort_values(["game_id", "play_id"]).groupby("game_id"):
        r = {}
        for k, q in (("half", 2), ("q3", 3)):
            z = x[x.qtr <= q]
            if len(z): r[k] = (int(z.total_home_score.iloc[-1]), int(z.total_away_score.iloc[-1]))
        cps[gid] = r
    s = p[((p["pass"] == 1) | (p["rush"] == 1)) & p.epa.notna() & p.posteam.notna()]
    o = s.groupby(["game_id", "week", "posteam"]).agg(op=("epa", "size"), oe=("epa", "sum"), os_=("success", "sum")).reset_index().rename(columns={"posteam": "team"})
    d = s.groupby(["game_id", "week", "defteam"]).agg(dp=("epa", "size"), de=("epa", "sum"), ds=("success", "sum")).reset_index().rename(columns={"defteam": "team"})
    t = o.merge(d, on=["game_id", "week", "team"])
    t["season"] = y
    rows.append(t)
tg = pd.concat(rows)

def stats(x):
    return {"oepa": x.oe.sum() / x.op.sum(), "osr": x.os_.sum() / x.op.sum(), "depa": x.de.sum() / x.dp.sum(), "dsr": x.ds.sum() / x.dp.sum()}
HIGH = {"oepa": True, "osr": True, "depa": False, "dsr": False, "net": True}

out = []
for y, gy in g.groupby("season"):
    ty = tg[tg.season == y]
    for w, gw in gy.groupby("week"):
        prior = ty[ty.week < w]
        recs = {}
        for team, tp in prior.groupby("team"):
            tp = tp.sort_values("week")
            if len(tp) < 3: continue
            a, b = stats(tp), stats(tp.tail(3))
            a["net"] = a["oepa"] - a["depa"]; b["net"] = b["oepa"] - b["depa"]
            recs[team] = {**{"s_" + k: v for k, v in a.items()}, **{"l3_" + k: v for k, v in b.items()}}
        if len(recs) < 28: continue
        R = pd.DataFrame(recs).T
        rk = {}
        for c in R.columns:
            m = c.split("_", 1)[1]
            rk[c] = R[c].rank(ascending=not HIGH[m], method="min")
        RK = pd.DataFrame(rk)
        for _, r in gw.iterrows():
            if r.spread_line == 0 or pd.isna(r.spread_line): continue
            fav, dog = (r.home_team, r.away_team) if r.spread_line > 0 else (r.away_team, r.home_team)
            if fav not in RK.index or dog not in RK.index: continue
            fh = fav == r.home_team
            fs, ds = (r.home_score, r.away_score) if fh else (r.away_score, r.home_score)
            row = {"season": y, "week": w, "gid": r.game_id, "fav": fav, "dog": dog, "spread": abs(r.spread_line), "fav_home": fh,
                   "win": int(fs > ds), "tie": int(fs == ds)}
            for c in RK.columns:
                row["f_" + c] = RK.at[fav, c]; row["d_" + c] = RK.at[dog, c]
            cp = cps.get(r.game_id, {})
            for k in ("half", "q3"):
                if k in cp:
                    h, a_ = cp[k]
                    row[k + "_lead"] = (h - a_) if fh else (a_ - h)
            out.append(row)
df = pd.DataFrame(out)
df.to_csv(os.path.join(D, "nfl_matchups.csv"), index=False)
print(len(df), df.groupby("season").size().to_dict())
