"""NFL early-blowout path backtest (spec: NFL Stat Edge v2 - Draft Spec).

Qualifier at a quarter-end checkpoint (end of Q1, halftime, end of Q3):
  - pregame betting favorite (nflverse spread_line) leads by 10+
  - favorite's defense is top 10 in BOTH defensive EPA/play allowed and defensive
    success rate allowed, for BOTH season-to-date and last 3 games, computed only
    from games played before that week (no hindsight)
  - control green: favorite wins >= 3 of 5 in-game stats through the checkpoint
    (EPA/play, success rate, 3rd-down conv rate, sacks+QB hits, avg start field pos)
  - QB edge holds: favorite's QB score >= opponent's QB score (2 elite & playing well,
    1 starter in, 0 backup in)
Outcomes: straight-up win (moneyline) and cover vs closing spread.
Price proxy: nflverse vegas_wp (spread-adjusted in-game win probability) at the
start of the next quarter, since historical Kalshi prices aren't available.

Usage: python3 scripts/nfl_backtest.py OUTDIR PBP.parquet [PBP2.parquet ...]  (one-off study, Nov 8 2026; not run by any workflow)
"""
import sys, os
import numpy as np
import pandas as pd

OUT = sys.argv[1]
FILES = sys.argv[2:]
os.makedirs(OUT, exist_ok=True)


def load(path):
    d = pd.read_parquet(path)
    d = d[d.season_type.isin(["REG", "POST"])].copy()
    return d


def team_game_def(d):
    """Per team-game defensive totals on scrimmage plays (pass or rush with EPA)."""
    s = d[((d["pass"] == 1) | (d["rush"] == 1)) & d.epa.notna() & d.defteam.notna()]
    g = (s.groupby(["season", "game_id", "week", "defteam"])
           .agg(plays=("epa", "size"), epa=("epa", "sum"), succ=("success", "sum"))
           .reset_index().rename(columns={"defteam": "team"}))
    return g


def weekly_def_ranks(tg):
    """For each (season, week, team): defensive ranks using only games before that week."""
    rows = []
    for season, sg in tg.groupby("season"):
        weeks = sorted(sg.week.unique())
        teams = sorted(sg.team.unique())
        for w in weeks:
            prior = sg[sg.week < w]
            recs = []
            for t in teams:
                tp = prior[prior.team == t].sort_values("week")
                if len(tp) < 3:
                    recs.append(dict(team=t, n_games=len(tp)))
                    continue
                l3 = tp.tail(3)
                recs.append(dict(
                    team=t, n_games=len(tp),
                    s_epa=tp.epa.sum() / tp.plays.sum(), s_sr=tp.succ.sum() / tp.plays.sum(),
                    l3_epa=l3.epa.sum() / l3.plays.sum(), l3_sr=l3.succ.sum() / l3.plays.sum()))
            r = pd.DataFrame(recs)
            for c in ["s_epa", "s_sr", "l3_epa", "l3_sr"]:
                if c in r:
                    r[c + "_rk"] = r[c].rank(method="min")  # lower allowed = better = rank 1
            r["season"], r["week"] = season, w
            rows.append(r)
    return pd.concat(rows, ignore_index=True)


def qb_season_table(d):
    """QB EPA per dropback by (season, week): cumulative before that week, min 100 dropbacks."""
    db = d[(d.qb_dropback == 1) & d.epa.notna() & d.passer_player_id.notna()]
    pq = (db.groupby(["season", "week", "passer_player_id"])
            .agg(n=("epa", "size"), epa=("epa", "sum")).reset_index())
    out = []
    for season, sg in pq.groupby("season"):
        for w in sorted(d[d.season == season].week.unique()):
            prior = sg[sg.week < w].groupby("passer_player_id")[["n", "epa"]].sum()
            prior = prior[prior.n >= 100]
            prior["epa_db"] = prior.epa / prior.n
            prior["rk"] = prior.epa_db.rank(ascending=False, method="min")
            prior = prior.reset_index()
            prior["season"], prior["week"] = season, w
            out.append(prior[["season", "week", "passer_player_id", "rk"]])
    return pd.concat(out, ignore_index=True)


def regular_starter(d):
    """Team's regular QB entering each game: most dropbacks over its prior 3 games."""
    db = d[(d.qb_dropback == 1) & d.passer_player_id.notna()]
    tq = db.groupby(["season", "week", "posteam", "passer_player_id"]).size().reset_index(name="n")
    res = {}
    for (season, team), g in tq.groupby(["season", "posteam"]):
        weeks = sorted(g.week.unique())
        for w in sorted(d[(d.season == season) & ((d.home_team == team) | (d.away_team == team))].week.unique()):
            prior_weeks = [x for x in weeks if x < w][-3:]
            p = g[g.week.isin(prior_weeks)].groupby("passer_player_id").n.sum()
            res[(season, w, team)] = p.idxmax() if len(p) else None
    return res


def ingame_stats(gp, team, opp):
    """In-game control stats for team vs opp on plays gp (already cut at checkpoint)."""
    scr = gp[((gp["pass"] == 1) | (gp["rush"] == 1)) & gp.epa.notna()]
    o, x = scr[scr.posteam == team], scr[scr.posteam == opp]

    def rate(a, b):
        return a / b if b else np.nan
    st = {}
    st["epa"] = (o.epa.mean() if len(o) else np.nan, x.epa.mean() if len(x) else np.nan)
    st["sr"] = (o.success.mean() if len(o) else np.nan, x.success.mean() if len(x) else np.nan)
    t3o = gp[gp.posteam == team]; t3x = gp[gp.posteam == opp]
    st["third"] = (rate(t3o.third_down_converted.sum(), t3o.third_down_converted.sum() + t3o.third_down_failed.sum()),
                   rate(t3x.third_down_converted.sum(), t3x.third_down_converted.sum() + t3x.third_down_failed.sum()))
    # pressure generated by each defense: sacks + QB hits on the other team's dropbacks
    st["press"] = ((x.sack.fillna(0) + x.qb_hit.fillna(0)).sum(), (o.sack.fillna(0) + o.qb_hit.fillna(0)).sum())
    # average starting field position (own yard line) on each drive's first scrimmage play
    sp = gp[gp.play_type.isin(["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike"]) & gp.fixed_drive.notna()]
    first = sp.sort_values("play_id").groupby("fixed_drive").first()
    fo, fx = first[first.posteam == team], first[first.posteam == opp]
    st["field"] = ((100 - fo.yardline_100).mean() if len(fo) else np.nan,
                   (100 - fx.yardline_100).mean() if len(fx) else np.nan)
    wins = sum(1 for a, b in st.values() if pd.notna(a) and pd.notna(b) and a > b)
    return st, wins


def qb_score(gp, team, season, week, reg, qbrk):
    db = gp[(gp.posteam == team) & (gp.qb_dropback == 1) & gp.passer_player_id.notna()].sort_values("play_id")
    if db.empty:
        return 1, None
    cur = db.passer_player_id.iloc[-1]
    regular = reg.get((season, week, team))
    if regular is not None and cur != regular:
        return 0, db.passer_player_name.iloc[-1]          # backup in
    mine = db[db.passer_player_id == cur]
    playing_well = mine.epa.mean() > 0 and (mine.interception.sum() + mine.fumble_lost.sum()) < 2
    r = qbrk.get((season, week, cur))
    if not playing_well:
        return 0 if (mine.interception.sum() + mine.fumble_lost.sum()) >= 2 else 1, mine.passer_player_name.iloc[-1]
    return (2 if (r is not None and r <= 10) else 1), mine.passer_player_name.iloc[-1]


def run(d):
    tg = team_game_def(d)
    ranks = weekly_def_ranks(tg).set_index(["season", "week", "team"])
    qbt = qb_season_table(d)
    qbrk = {(r.season, r.week, r.passer_player_id): r.rk for r in qbt.itertuples()}
    reg = regular_starter(d)
    rows = []
    for gid, gp in d.groupby("game_id"):
        gp = gp.sort_values("play_id")
        h, a = gp.home_team.iloc[0], gp.away_team.iloc[0]
        season, week = int(gp.season.iloc[0]), int(gp.week.iloc[0])
        spread, result = gp.spread_line.iloc[0], gp.result.iloc[0]  # both home-perspective
        if pd.isna(spread) or spread == 0:
            continue
        fav, dog = (h, a) if spread > 0 else (a, h)
        sgn = 1 if fav == h else -1
        fav_margin = sgn * result
        try:
            fr = ranks.loc[(season, week, fav)]
        except KeyError:
            fr = None
        for q in (1, 2, 3):
            cut = gp[gp.qtr <= q]
            nxt = gp[(gp.qtr == q + 1) & gp.vegas_home_wp.notna()]
            if cut.empty or nxt.empty:
                continue
            hs, as_ = cut.total_home_score.max(), cut.total_away_score.max()
            lead = sgn * (hs - as_)
            if lead < 10:
                continue
            wp = nxt.vegas_home_wp.iloc[0]
            wp = wp if fav == h else 1 - wp
            st, cwins = ingame_stats(cut, fav, dog)
            fq, fqn = qb_score(cut, fav, season, week, reg, qbrk)
            dq, dqn = qb_score(cut, dog, season, week, reg, qbrk)
            has = fr is not None and pd.notna(fr.get("s_epa_rk", np.nan))
            def_top10 = bool(has and fr.s_epa_rk <= 10 and fr.s_sr_rk <= 10 and fr.l3_epa_rk <= 10 and fr.l3_sr_rk <= 10)
            rows.append(dict(
                season=season, week=week, game_id=gid, fav=fav, dog=dog, home=h,
                fav_spread=abs(spread), checkpoint={1: "End Q1", 2: "Halftime", 3: "End Q3"}[q], q=q,
                score=f"{fav} {int(hs if fav == h else as_)}-{int(as_ if fav == h else hs)} {dog}", lead=int(lead),
                def_ranks_known=has,
                def_s_epa_rk=fr.s_epa_rk if has else np.nan, def_s_sr_rk=fr.s_sr_rk if has else np.nan,
                def_l3_epa_rk=fr.l3_epa_rk if has else np.nan, def_l3_sr_rk=fr.l3_sr_rk if has else np.nan,
                def_top10=def_top10, control_wins=cwins, control_green=cwins >= 3,
                fav_qb=fqn, fav_qb_score=fq, dog_qb=dqn, dog_qb_score=dq, qb_edge=fq >= dq,
                model_wp=wp, final_margin=fav_margin, won=fav_margin > 0, tie=fav_margin == 0,
                covered=fav_margin > abs(spread), push=fav_margin == abs(spread),
            ))
    return pd.DataFrame(rows)


def summarize(df, label):
    if df.empty:
        return dict(group=label, n=0)
    return dict(group=label, n=len(df), games=df.game_id.nunique(), wins=int(df.won.sum()),
                win_rate=df.won.mean(), blown=int((~df.won).sum()),
                cover_rate=df.covered.sum() / max(1, (~df.push).sum()),
                avg_model_wp=df.model_wp.mean(), edge_vs_model=df.won.mean() - df.model_wp.mean())


if __name__ == "__main__":
    allrows = pd.concat([run(load(f)) for f in FILES], ignore_index=True)
    allrows.to_csv(os.path.join(OUT, "checkpoints.csv"), index=False)
    known = allrows[allrows.def_ranks_known]
    full = known[known.def_top10 & known.control_green & known.qb_edge]
    first = full.sort_values("q").groupby("game_id").head(1)
    out = []
    out.append(summarize(known, "All favorites up 10+ (ranks available)"))
    out.append(summarize(known[known.def_top10], "+ both D metrics top 10 (season & L3)"))
    out.append(summarize(known[known.def_top10 & known.control_green], "+ control green"))
    out.append(summarize(full, "+ QB edge (FULL QUALIFIER), all checkpoints"))
    out.append(summarize(first, "FULL QUALIFIER, first checkpoint per game"))
    out.append(summarize(known[~known.def_top10], "Favorites up 10+, defense NOT top 10"))
    for cp in ["End Q1", "Halftime", "End Q3"]:
        out.append(summarize(known[known.checkpoint == cp], f"All 10+ leads @ {cp}"))
        out.append(summarize(full[full.checkpoint == cp], f"Full qualifier @ {cp}"))
    for lo, hi, nm in [(10, 13, "10-13"), (14, 16, "14-16"), (17, 99, "17+")]:
        out.append(summarize(known[known.lead.between(lo, hi)], f"All 10+ leads, lead {nm}"))
        out.append(summarize(full[full.lead.between(lo, hi)], f"Full qualifier, lead {nm}"))
    for lo, hi, nm in [(0, 3.4, "fav < 3.5"), (3.5, 6.9, "fav 3.5-6.5"), (7, 99, "fav 7+")]:
        out.append(summarize(known[known.fav_spread.between(lo, hi)], f"All 10+ leads, {nm}"))
        out.append(summarize(full[full.fav_spread.between(lo, hi)], f"Full qualifier, {nm}"))
    s = pd.DataFrame(out)
    s.to_csv(os.path.join(OUT, "summary.csv"), index=False)
    pd.set_option("display.width", 220)
    print(s.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    full.to_csv(os.path.join(OUT, "qualifiers.csv"), index=False)
    print("\nBlown full-qualifier checkpoints:")
    print(full[~full.won][["season", "week", "fav", "dog", "checkpoint", "score", "final_margin", "model_wp"]].to_string(index=False))
