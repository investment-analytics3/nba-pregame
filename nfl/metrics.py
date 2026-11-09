"""Team metrics as they stood before a given week (no hindsight).

Four core metrics (the NFL versions of the NBA four), season-to-date and last 3 games:
    off_epa   offensive EPA per play            higher is better
    off_sr    offensive success rate            higher is better
    def_epa   defensive EPA per play allowed    lower is better
    def_sr    defensive success rate allowed    lower is better

Official stat edge (unchanged): net EPA = off EPA/play - def EPA/play allowed, all
scrimmage plays (pass or rush with EPA). Computed from nflverse play-by-play, which is
the RBSDM method (checked against the Week 9 snapshot: within ~0.005 per team).

Adjusted edge (SHADOW ONLY, never used for a bet until approved): plays with win
probability 10-90%, opponent-adjusted, weighted net = 0.6 x offense - 0.4 x defense
allowed, plus a recency line (last 4 games 60%, earlier games 40%).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

METRICS = ["off_epa", "off_sr", "def_epa", "def_sr"]
HIGHER_BETTER = {"off_epa": True, "off_sr": True, "def_epa": False, "def_sr": False}
LABELS = {"off_epa": "Off EPA/play", "off_sr": "Off success", "def_epa": "Def EPA allowed", "def_sr": "Def success allowed"}


def scrimmage(d: pd.DataFrame) -> pd.DataFrame:
    return d[((d["pass"] == 1) | (d["rush"] == 1)) & d.epa.notna() & d.posteam.notna() & d.defteam.notna()]


def team_games(d: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game: offense and defense totals (all plays and the 10-90% filter)."""
    s = scrimmage(d).copy()
    s["filt"] = s.wp.between(0.10, 0.90)
    s["is_pass"] = (s["pass"] == 1).astype(int)
    rows = []
    for side, team_col, opp_col in (("off", "posteam", "defteam"), ("def", "defteam", "posteam")):
        g = s.groupby(["game_id", "week", team_col, opp_col])
        a = g.agg(plays=("epa", "size"), epa=("epa", "sum"), succ=("success", "sum")).reset_index()
        f = s[s.filt].groupby(["game_id", team_col]).agg(f_plays=("epa", "size"), f_epa=("epa", "sum")).reset_index()
        p = s[s.is_pass == 1].groupby(["game_id", team_col]).agg(p_plays=("epa", "size"), p_epa=("epa", "sum")).reset_index()
        r = s[s.is_pass == 0].groupby(["game_id", team_col]).agg(r_plays=("epa", "size"), r_epa=("epa", "sum")).reset_index()
        a = a.merge(f, on=["game_id", team_col], how="left").merge(p, on=["game_id", team_col], how="left") \
             .merge(r, on=["game_id", team_col], how="left").fillna(0)
        a = a.rename(columns={team_col: "team", opp_col: "opp"})
        a.columns = [c if c in ("game_id", "week", "team", "opp") else f"{side}_{c}" for c in a.columns]
        rows.append(a)
    tg = rows[0].merge(rows[1], on=["game_id", "week", "team", "opp"], how="outer").fillna(0)
    return tg.sort_values(["team", "week"])


def _rates(g: pd.DataFrame) -> dict:
    def div(a, b):
        return float(a / b) if b else None
    return {
        "games": int(len(g)),
        "off_epa": div(g.off_epa.sum(), g.off_plays.sum()), "off_sr": div(g.off_succ.sum(), g.off_plays.sum()),
        "def_epa": div(g.def_epa.sum(), g.def_plays.sum()), "def_sr": div(g.def_succ.sum(), g.def_plays.sum()),
        "off_pass_epa": div(g.off_p_epa.sum(), g.off_p_plays.sum()), "off_rush_epa": div(g.off_r_epa.sum(), g.off_r_plays.sum()),
        "def_pass_epa": div(g.def_p_epa.sum(), g.def_p_plays.sum()), "def_rush_epa": div(g.def_r_epa.sum(), g.def_r_plays.sum()),
    }


def _rank(values: dict, metric: str) -> dict:
    """{team: rank} 1 = best; ties share the better rank."""
    v = pd.Series({t: x for t, x in values.items() if x is not None}, dtype=float)
    return v.rank(ascending=not HIGHER_BETTER[metric], method="min").astype(int).to_dict()


def week_metrics(d: pd.DataFrame, week: int, tg: pd.DataFrame | None = None) -> dict:
    """{team: {"season": {...}, "last3": {...}, "net": x, "adjusted": {...}}} using games before `week`."""
    tg = team_games(d) if tg is None else tg
    prior = tg[tg.week < week]
    out = {}
    for team, g in prior.groupby("team"):
        g = g.sort_values("week")
        season, last3 = _rates(g), _rates(g.tail(3))
        out[team] = {"season": season, "last3": last3,
                     "net": (season["off_epa"] - season["def_epa"]) if season["off_epa"] is not None else None}
    for window in ("season", "last3"):
        for m in METRICS:
            ranks = _rank({t: v[window][m] for t, v in out.items()}, m)
            for t in out:
                out[t][window].setdefault("ranks", {})[m] = ranks.get(t)
    net_r = _rank({t: v["net"] for t, v in out.items()}, "off_epa")
    for t in out:
        out[t]["net_rank"] = net_r.get(t)
    _adjusted(prior, out)
    return out


def _adjusted(prior: pd.DataFrame, out: dict):
    """Shadow adjusted edge: 10-90% win-probability plays, opponent-adjusted, 0.6/0.4 weights."""
    if prior.empty:
        return
    tot = prior.groupby("team")[["off_f_epa", "off_f_plays", "def_f_epa", "def_f_plays"]].sum()
    off_raw = (tot.off_f_epa / tot.off_f_plays.replace(0, np.nan)).to_dict()
    def_raw = (tot.def_f_epa / tot.def_f_plays.replace(0, np.nan)).to_dict()
    lg = prior.off_f_epa.sum() / max(1, prior.off_f_plays.sum())
    p = prior.copy()
    p["adj_off"] = p.off_f_epa / p.off_f_plays.replace(0, np.nan) - (p.opp.map(def_raw) - lg)
    p["adj_def"] = p.def_f_epa / p.def_f_plays.replace(0, np.nan) - (p.opp.map(off_raw) - lg)

    def wmean(g, col, w):
        ok = g[col].notna() & (g[w] > 0)
        return float(np.average(g.loc[ok, col], weights=g.loc[ok, w])) if ok.any() else None

    for team, g in p.groupby("team"):
        if team not in out:
            continue
        g = g.sort_values("week")
        so, sd = wmean(g, "adj_off", "off_f_plays"), wmean(g, "adj_def", "def_f_plays")
        last4, early = g.tail(4), g.iloc[:-4]
        lo, ld = wmean(last4, "adj_off", "off_f_plays"), wmean(last4, "adj_def", "def_f_plays")
        eo, ed = wmean(early, "adj_off", "off_f_plays"), wmean(early, "adj_def", "def_f_plays")
        net = 0.6 * so - 0.4 * sd if so is not None and sd is not None else None
        recent_net = 0.6 * lo - 0.4 * ld if lo is not None and ld is not None else None
        if recent_net is not None and eo is not None and ed is not None:
            recent_net = 0.6 * recent_net + 0.4 * (0.6 * eo - 0.4 * ed)
        out[team]["adjusted"] = {"off": so, "def": sd, "net": net, "recent_net": recent_net,
                                 "sr_off": out[team]["season"]["off_sr"], "sr_def": out[team]["season"]["def_sr"]}


# --------------------------------------------------------------------------- #
# Records, quarterbacks and key starters
# --------------------------------------------------------------------------- #
def records(sched: pd.DataFrame, season: int, week: int) -> dict:
    """{team: {"record": "W-L(-T)", "last2": [..], "last3": [..]}} from finished games before `week`."""
    g = sched[(sched.season == season) & (sched.week < week) & sched.result.notna()]
    res = {}
    for r in g.sort_values("week").itertuples():
        for team, sign in ((r.home_team, 1), (r.away_team, -1)):
            m = sign * r.result
            res.setdefault(team, []).append("W" if m > 0 else ("L" if m < 0 else "T"))
    out = {}
    for t, seq in res.items():
        w, l, ti = seq.count("W"), seq.count("L"), seq.count("T")
        out[t] = {"record": f"{w}-{l}" + (f"-{ti}" if ti else ""),
                  "last2": seq[-2:][::-1], "last3": seq[-3:][::-1]}
    return out


def wl(seq: list[str]) -> str:
    return f"{seq.count('W')}-{seq.count('L')}" + (f"-{seq.count('T')}" if "T" in seq else "")


def quarterbacks(d: pd.DataFrame, week: int) -> dict:
    """{team: {"regular": name, "regular_id": id, "season_leader": name, "epa_db": x, "rank": r, "dropbacks": n}}.

    Regular QB = most dropbacks over the team's last 3 games. Rank = EPA per dropback this
    season among QBs with 100+ dropbacks (1 = best).
    """
    db = d[(d.week < week) & (d.qb_dropback == 1) & d.epa.notna() & d.passer_player_id.notna()]
    if db.empty:
        return {}
    per = db.groupby("passer_player_id").agg(n=("epa", "size"), epa=("epa", "sum"),
                                             name=("passer_player_name", "last"))
    per["epa_db"] = per.epa / per.n
    q = per[per.n >= 100].copy()
    q["rank"] = q.epa_db.rank(ascending=False, method="min")
    out = {}
    for team, g in db.groupby("posteam"):
        weeks = sorted(g.week.unique())[-3:]
        reg = g[g.week.isin(weeks)].passer_player_id.value_counts().idxmax()
        lead = g.passer_player_id.value_counts().idxmax()
        out[team] = {"regular": per.loc[reg, "name"], "regular_id": reg,
                     "season_leader": per.loc[lead, "name"], "reset": bool(reg != lead),
                     "dropbacks": int(per.loc[reg, "n"]), "epa_db": float(per.loc[reg, "epa_db"]),
                     "rank": int(q.loc[reg, "rank"]) if reg in q.index else None}
    return out


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z ]", "", str(s).lower().replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return " ".join(s.split())


def key_starters(snaps: pd.DataFrame, season: int, week: int) -> dict:
    """{team: [{"name", "pos"}]}: players at 50%+ of offense or defense snaps in at least half the team's games."""
    if snaps is None or snaps.empty:
        return {}
    s = snaps[(snaps.season == season) & (snaps.week < week)]
    out = {}
    for team, g in s.groupby("team"):
        n_games = g.game_id.nunique()
        big = g[(g.offense_pct >= 0.5) | (g.defense_pct >= 0.5)]
        cnt = big.groupby(["player", "position"]).game_id.nunique().reset_index()
        keep = cnt[cnt.game_id >= max(1, n_games / 2)]
        out[team] = [{"name": r.player, "pos": r.position} for r in keep.itertuples()]
    return out
