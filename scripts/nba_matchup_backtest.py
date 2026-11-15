"""One-off NBA matchup backtest (owner Nov 15, 2026): regular seasons 2023-24..2025-26 from the sportsdataverse
release (espn_nba_team_boxscores team_box_<y>.parquet, espn_nba_pbp play_by_play_<y>.parquet as pbp_<y>.parquet in DIR).
Ranks as of the day before (season, last 3, last 2); favorite = better season net rating (no past closing lines).

    python scripts/nba_matchup_backtest.py DIR OUT.csv

Results (top 10 vs bottom 10 net-rating games, last-3 matchups top 10 / bottom 10): any matchup 391-83 (82%)
vs 219-63 (78%); halftime lead held 92% vs 86%, end-of-3rd lead 95% vs 91%. See nba/scoring.py MATCHUP_PAIRS.
"""
import sys, os
import pandas as pd, numpy as np
D = sys.argv[1]
out = []
for y in (2024, 2025, 2026):
    t = pd.read_parquet(os.path.join(D, f"team_box_{y}.parquet"))
    t = t[t.season_type == 2]
    t = t[["game_id", "game_date", "team_id", "opponent_team_id", "team_home_away", "team_abbreviation", "team_score", "opponent_team_score",
           "field_goals_made", "field_goals_attempted", "three_point_field_goals_made", "free_throws_attempted", "offensive_rebounds", "total_turnovers"]].copy()
    t.columns = ["gid", "date", "tid", "oid", "site", "abbr", "pts", "opp_pts", "fgm", "fga", "fg3m", "fta", "oreb", "tov"]
    t["date"] = t["date"].astype(str).str[:10]
    o = t[["gid", "tid", "fgm", "fga", "fg3m", "fta", "oreb", "tov"]].rename(columns={"tid": "oid", **{c: "o_" + c for c in ["fgm", "fga", "fg3m", "fta", "oreb", "tov"]}})
    t = t.merge(o, on=["gid", "oid"]).sort_values(["date", "gid"])
    t["poss"] = 0.5 * ((t.fga - t.oreb + t.tov + 0.44 * t.fta) + (t.o_fga - t.o_oreb + t.o_tov + 0.44 * t.o_fta))
    cps = {}
    if os.path.exists(os.path.join(D, f"pbp_{y}.parquet")):
        p = pd.read_parquet(os.path.join(D, f"pbp_{y}.parquet"), columns=["game_id", "end_game_seconds_remaining", "home_score", "away_score"])
        p = p.sort_values(["game_id", "end_game_seconds_remaining"], ascending=[True, False])
        for gid, g in p.groupby("game_id"):
            r = {}
            for k, sec in (("half", 1440), ("q3", 720)):
                x = g[g.end_game_seconds_remaining >= sec]
                if len(x): r[k] = (int(x.home_score.iloc[-1]), int(x.away_score.iloc[-1]))
            cps[int(gid)] = r
    def calc(g):
        s = g[["pts", "opp_pts", "fgm", "fga", "fg3m", "o_fgm", "o_fga", "o_fg3m", "poss"]].sum()
        return {"off": 100 * s.pts / s.poss, "def": 100 * s.opp_pts / s.poss, "efg": (s.fgm + .5 * s.fg3m) / s.fga,
                "opp_efg": (s.o_fgm + .5 * s.o_fg3m) / s.o_fga, "net": 100 * (s.pts - s.opp_pts) / s.poss}
    HIGH = {"off": True, "def": False, "efg": True, "opp_efg": False, "net": True}
    home = t[t.site == "home"]
    for day, games in home.groupby("date"):
        prior = t[t.date < day]
        rk = {}
        for w, k in (("season", None), ("last3", 3), ("last2", 2)):
            vals = {int(tid): calc(g if k is None else g.tail(k)) for tid, g in prior.groupby("tid") if len(g) >= 5}
            for m in HIGH:
                s = pd.Series({tid: v[m] for tid, v in vals.items()}).rank(ascending=not HIGH[m], method="min")
                for tid, r in s.items(): rk.setdefault(tid, {})[f"{m}_{w}"] = int(r)
        for g in games.itertuples():
            h, a = int(g.tid), int(g.oid)
            if h not in rk or a not in rk: continue
            home_fav = rk[h]["net_season"] < rk[a]["net_season"] or (rk[h]["net_season"] == rk[a]["net_season"])
            f, d_ = (h, a) if home_fav else (a, h)
            fp, dp = (g.pts, g.opp_pts) if home_fav else (g.opp_pts, g.pts)
            row = {"season": y, "gid": int(g.gid), "date": day, "fav_home": home_fav, "fav_won": fp > dp, "margin": fp - dp, "n": len(rk)}
            for kk, v in rk[f].items(): row[f"fav_{kk}"] = v
            for kk, v in rk[d_].items(): row[f"dog_{kk}"] = v
            for kk, (hs, as_) in cps.get(int(g.gid), {}).items():
                row[f"{kk}_lead"] = (hs - as_) if home_fav else (as_ - hs)
            out.append(row)
    print(y, len(out), flush=True)
pd.DataFrame(out).to_csv(sys.argv[2], index=False)
