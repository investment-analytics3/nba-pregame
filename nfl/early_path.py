"""Early blowout path: grade a game at halftime / end of the 3rd from nflverse play-by-play.

Same definitions as the 2024-25 backtest (spec section "Early blowout path"):
  - favorite = pregame spread favorite, leads by 10+ at the checkpoint
  - defense: favorite top 10 in BOTH defensive EPA/play allowed and defensive success rate
    allowed, for BOTH the season and the last 3 games, as of that week
  - control green: favorite wins at least 3 of 5 through the checkpoint (EPA/play, success
    rate, 3rd-down rate, sacks + QB hits generated, average starting field position)
  - QB edge: favorite's QB score >= opponent's (2 = top-10 EPA/dropback and playing well,
    1 = regular starter in, 0 = backup in or 2+ turnovers)
Full qualifier = all four. Shadow line (logged, never bet) = control + QB edge, any defense.
Window (decided Nov 8): halftime and end of the 3rd only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CHECKPOINTS = {"half": 2, "q3": 3}
CHECKPOINT_LABEL = {"half": "Halftime", "q3": "End of 3rd"}


def ingame_stats(gp: pd.DataFrame, team: str, opp: str):
    scr = gp[((gp["pass"] == 1) | (gp["rush"] == 1)) & gp.epa.notna()]
    o, x = scr[scr.posteam == team], scr[scr.posteam == opp]

    def rate(a, b):
        return a / b if b else np.nan

    def third(df):
        c, f = df.third_down_converted.sum(), df.third_down_failed.sum()
        return rate(c, c + f)

    st = {
        "epa": (o.epa.mean() if len(o) else np.nan, x.epa.mean() if len(x) else np.nan),
        "sr": (o.success.mean() if len(o) else np.nan, x.success.mean() if len(x) else np.nan),
        "third": (third(gp[gp.posteam == team]), third(gp[gp.posteam == opp])),
        "press": ((x.sack.fillna(0) + x.qb_hit.fillna(0)).sum(), (o.sack.fillna(0) + o.qb_hit.fillna(0)).sum()),
    }
    sp = gp[gp.play_type.isin(["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike"]) & gp.fixed_drive.notna()]
    first = sp.sort_values("play_id").groupby("fixed_drive").first()
    fo, fx = first[first.posteam == team], first[first.posteam == opp]
    st["field"] = ((100 - fo.yardline_100).mean() if len(fo) else np.nan,
                   (100 - fx.yardline_100).mean() if len(fx) else np.nan)
    wins = [k for k, (a, b) in st.items() if pd.notna(a) and pd.notna(b) and a > b]
    return st, wins


def qb_score(gp: pd.DataFrame, team: str, qbs: dict) -> tuple[int, str | None]:
    db = gp[(gp.posteam == team) & (gp.qb_dropback == 1) & gp.passer_player_id.notna()].sort_values("play_id")
    if db.empty:
        return 1, None
    cur = db.passer_player_id.iloc[-1]
    info = qbs.get(team) or {}
    name = db.passer_player_name.iloc[-1]
    if info.get("regular_id") and cur != info["regular_id"]:
        return 0, name                                   # backup in
    mine = db[db.passer_player_id == cur]
    tos = mine.interception.sum() + mine.fumble_lost.sum()
    if tos >= 2:
        return 0, name
    if mine.epa.mean() > 0 and info.get("rank") is not None and info["rank"] <= 10:
        return 2, name
    return 1, name


def defense_top10(team_metrics: dict) -> bool:
    try:
        s, l3 = team_metrics["season"]["ranks"], team_metrics["last3"]["ranks"]
        return all(r is not None and r <= 10 for r in (s["def_epa"], s["def_sr"], l3["def_epa"], l3["def_sr"]))
    except (KeyError, TypeError):
        return False


def grade(game_pbp: pd.DataFrame, checkpoint: str, fav: str, dog: str, wk_metrics: dict, qbs: dict) -> dict | None:
    """Exact grade of one checkpoint. None if the game hasn't reached it in the data."""
    q = CHECKPOINTS[checkpoint]
    gp = game_pbp.sort_values("play_id")
    if gp.qtr.max() <= q:
        return None
    cut = gp[gp.qtr <= q]
    home = gp.home_team.iloc[0]
    hs, as_ = cut.total_home_score.max(), cut.total_away_score.max()
    lead = int((hs - as_) if fav == home else (as_ - hs))
    _, wins = ingame_stats(cut, fav, dog)
    fq, fqn = qb_score(cut, fav, qbs)
    dq, dqn = qb_score(cut, dog, qbs)
    nxt = gp[(gp.qtr == q + 1) & gp.vegas_home_wp.notna()]
    model = None
    if len(nxt):
        w = float(nxt.vegas_home_wp.iloc[0])
        model = w if fav == home else 1 - w
    d_ok = defense_top10(wk_metrics.get(fav, {}))
    control = len(wins) >= 3
    qb_edge = fq >= dq
    return {"lead": lead, "up10": lead >= 10, "defense_top10": d_ok, "control_wins": wins, "control_green": control,
            "fav_qb": fqn, "fav_qb_score": fq, "dog_qb": dqn, "dog_qb_score": dq, "qb_edge": qb_edge,
            "full": bool(lead >= 10 and d_ok and control and qb_edge),
            "shadow_any_d": bool(lead >= 10 and control and qb_edge),
            "model_wp": round(model, 3) if model is not None else None}


# Backtest benchmark (2024 + 2025 seasons, first qualifying checkpoint per game), shown on the page.
BENCHMARK = {
    "full_half_plus": {"games": 31, "won": 31, "model": 0.957},
    "full_q1": {"games": 10, "won": 8, "model": 0.884},
    "shadow_half_plus": {"games": 165, "won": 161, "model": 0.952},
}
