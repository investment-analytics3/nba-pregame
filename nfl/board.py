"""Build the NFL pregame board for the current week: data, scoring, page, summary.

    from nfl import board; b = board.build()      # returns the board dict and writes files
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from nba.http import FetchError
from . import espn, kalshi, metrics, scoring, sources
from .early_path import BENCHMARK, defense_top10

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
ROOT = sources.ROOT
DOCS = os.path.join(ROOT, "docs", "nfl")
DATA = sources.DATA
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"


def season_for(d: date) -> int:
    return d.year if d.month >= 8 else d.year - 1


def target_week(sched: pd.DataFrame, season: int, today_et: date):
    s = sched[sched.season == season].copy()
    s["day"] = pd.to_datetime(s.gameday).dt.date
    upcoming = s[s.day >= today_et]
    if upcoming.empty:
        return None, s.iloc[0:0]
    wk = int(upcoming.week.min())
    return wk, s[s.week == wk].sort_values(["gameday", "gametime"])


def _kick_utc(row) -> datetime:
    t = datetime.strptime(f"{row.gameday} {row.gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return t.astimezone(ZoneInfo("UTC"))


def _team_block(code: str, wm: dict, recs: dict, qbs: dict, keys: dict, inj: list | None, rest) -> dict:
    m = wm.get(code) or {"season": {"ranks": {}}, "last3": {"ranks": {}}, "net": None}
    rec = recs.get(code, {})
    t = {"code": code, "abbr": sources.espn_code(code), "season": m["season"], "last3": m["last3"],
         "net": m.get("net"), "net_rank": m.get("net_rank"), "adjusted": m.get("adjusted"),
         "record": rec.get("record", "0-0"), "last2_results": rec.get("last2", []), "last3_results": rec.get("last3", []),
         "rest": None if pd.isna(rest) else int(rest), "qb": qbs.get(code), "injuries_known": inj is not None}
    inj = inj or []
    out_names = {metrics.norm_name(i["name"]): i for i in inj if i["status"] in scoring.OUT}
    q_names = {metrics.norm_name(i["name"]): i for i in inj if i["status"] == "Questionable"}
    key = keys.get(code, [])
    t["key_out"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in out_names]
    t["key_questionable"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in q_names]
    t["interior_ol_out"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in out_names and
                            (k["pos"] in scoring.INTERIOR_OL or out_names[metrics.norm_name(k["name"])]["pos"] in scoring.INTERIOR_OL)]
    qb = t["qb"] or {}
    # nflverse QB names are "J.Goff"; match ESPN's "Jared Goff" on first initial + last name.
    qb_out = [i["name"] for i in inj if i["status"] in scoring.OUT and i.get("pos") == "QB"
              and _same_qb(i["name"], qb.get("regular", ""))]
    t["qb_out"] = qb_out
    t["qb_active"] = not qb_out
    if qb_out:
        t["key_out"] = sorted(set(t["key_out"]) | set(qb_out))
        t["qb_reset"] = f"{qb_out[0]} out"
    elif qb and qb.get("dropbacks", 999) < 100:
        t["qb_reset"] = f"{qb.get('regular')}, {qb.get('dropbacks')} dropbacks this season"
    t["injury_list"] = [i for i in inj if i["status"] in scoring.OUT or i["status"] == "Questionable"][:12]
    return t


def _same_qb(espn_name: str, nflverse_name: str) -> bool:
    a, b = metrics.norm_name(espn_name).split(), metrics.norm_name(nflverse_name.replace(".", ". ")).split()
    return bool(a and b) and a[-1] == b[-1] and a[0][:1] == b[0][:1]


def build(today_et: date | None = None, refresh: bool = True) -> dict:
    now = datetime.now(PT)
    today_et = today_et or datetime.now(ET).date()
    season = season_for(today_et)
    sched = sources.schedule(refresh)
    week, games = target_week(sched, season, today_et)
    board = {"season": season, "week": week, "built_at": now.isoformat(timespec="seconds"),
             "built_label": now.strftime("%a %b %-d, %-I:%M %p PT"), "games": [], "notes": [],
             "benchmark": BENCHMARK, "large_favorite_cutoff": scoring.LARGE_FAVORITE}
    if week is None:
        board["notes"].append("No upcoming NFL games on the schedule.")
        return board
    pbp = sources.pbp(season, refresh)
    snaps = sources.snaps(season, refresh)
    tg = metrics.team_games(pbp)
    wm = metrics.week_metrics(pbp, week, tg)
    recs = metrics.records(sched, season, week)
    qbs = metrics.quarterbacks(pbp, week)
    keys = metrics.key_starters(snaps, season, week)
    board["data_through"] = f"Week {int(tg.week.max())}" if len(tg) else "none"
    if snaps is None or snaps.empty:
        board["notes"].append("Snap counts unavailable: key starters unknown.")

    st, ew = (2, week) if week <= 18 else (3, week - 18)
    try:
        events = {e["id"]: e for e in espn.parse_events(espn.scoreboard_week(st, ew))}
    except FetchError as e:
        events = {}
        board["notes"].append(f"ESPN scoreboard unavailable ({e}); lines from nflverse.")

    for row in games.itertuples():
        board["games"].append(_game(row, events, wm, recs, qbs, keys, board))
    board["games"].sort(key=lambda g: (g["state"] == "post", g["kick_utc"]))
    tracked = [g for g in board["games"] if g["scoring"].get("qualified") and g["state"] != "post"]   # still to play
    board["summary"] = {"season": season, "week": week, "games": len(board["games"]),
                        "upcoming": sum(1 for g in board["games"] if g["state"] == "pre"),
                        "tracked": len(tracked), "green": sum(1 for g in tracked if g["scoring"]["hero"]["band"] == "green"),
                        "edge_clears": sum(1 for g in board["games"] if g["scoring"].get("edge", {}).get("clears")),
                        "built_label": board["built_label"]}
    return board


def _game(row, events, wm, recs, qbs, keys, board) -> dict:
    ev = events.get(str(row.espn)) or next((e for e in events.values() if sources.nfl_code(e["home"]["abbr"]) == row.home_team
                                             and sources.nfl_code(e["away"]["abbr"]) == row.away_team), None)
    kick = _kick_utc(row)
    g = {"id": str(row.espn or row.game_id), "nflverse_id": row.game_id, "kick_utc": kick.isoformat(),
         "kick_pt": kick.astimezone(PT).strftime("%a %-I:%M %p PT"), "day_et": row.gameday,
         "state": (ev or {}).get("state") or ("post" if pd.notna(row.result) else "pre"),
         "detail": (ev or {}).get("detail"), "neutral": row.location == "Neutral" or bool((ev or {}).get("neutral")),
         "venue": (ev or {}).get("venue") or row.stadium, "home_code": row.home_team, "away_code": row.away_team}
    if ev:
        g["score"] = {"home": ev["home"]["score"], "away": ev["away"]["score"]}
    odds = (ev or {}).get("odds") or {}
    summ = {}
    if ev:
        try:
            summ = espn.summary(ev["id"])
        except FetchError:
            board["notes"].append(f"{row.away_team}@{row.home_team}: ESPN game page unavailable.")
    if not odds and summ.get("pickcenter"):
        odds = espn.parse_odds({"pickcenter": summ["pickcenter"]})
    inj = espn.parse_injuries(summ) if summ else None
    pred = espn.parse_predictor(summ) if summ else {}

    # favorite (spread), lines and movement
    if odds.get("favorite"):
        fav_side = odds["favorite"]
        L = odds["lines"][fav_side]
        spread_cur = L["cur"] if L["cur"] is not None else (-odds["spread"] if odds.get("spread") is not None else None)
        g["line_source"] = odds.get("provider") or "ESPN"
        g["open_spread"] = L["open"]
        g["total"], g["total_open"] = odds.get("total"), odds.get("total_open")
        g["fav_ml"] = L.get("ml")
    elif pd.notna(row.spread_line) and row.spread_line != 0:
        fav_side = "home" if row.spread_line > 0 else "away"
        spread_cur, g["line_source"], g["open_spread"] = -abs(row.spread_line), "nflverse", None
        g["total"], g["total_open"], g["fav_ml"] = row.total_line, None, None
    else:
        fav_side, spread_cur = None, None
        g["line_source"] = None
    g["favorite"] = fav_side
    g["spread"] = spread_cur
    g["movement"] = round(g["open_spread"] - spread_cur, 1) if (g.get("open_spread") is not None and spread_cur is not None) else None
    g["total_move"] = round(g["total"] - g["total_open"], 1) if (g.get("total") is not None and g.get("total_open") is not None) else None
    g["fav_home"] = fav_side == "home"

    home_inj = inj.get(str(ev["home"]["id"]), []) if (inj is not None and ev) else None
    away_inj = inj.get(str(ev["away"]["id"]), []) if (inj is not None and ev) else None
    home = _team_block(row.home_team, wm, recs, qbs, keys, home_inj, row.home_rest)
    away = _team_block(row.away_team, wm, recs, qbs, keys, away_inj, row.away_rest)
    g["home"], g["away"] = home, away
    if pred:
        home["fpi_win"], away["fpi_win"] = round(pred["home"], 3), round(pred["away"], 3)
    if not fav_side:
        g["scoring"] = {"qualified": False, "call": "No line", "hero": {"score": None, "band": None, "parts": {}, "max": 40},
                        "edge": {"gap": None}, "flags": [], "rules": [], "failed": ["No betting line yet"], "under": {}}
        return g
    fav, dog = (home, away) if fav_side == "home" else (away, home)
    g["fav"], g["dog"] = fav["code"], dog["code"]
    g["scoring"] = scoring.score_game(g, fav, dog)
    g["fav_defense_top10"] = defense_top10(wm.get(fav["code"], {}))

    if g["state"] == "pre":
        try:
            day = datetime.fromisoformat(g["kick_utc"]).astimezone(ET).date()
            k = kalshi.game_prices(day, sources.kalshi_code(row.away_team), sources.kalshi_code(row.home_team))
            if k:
                p = k.get(sources.kalshi_code(fav["code"])) or {}
                g["kalshi"] = {"event": k["event"], "fav_prob": p.get("prob"), "bid": p.get("bid"), "ask": p.get("ask")}
        except Exception as e:     # price is context only; never fail the board for it
            board["notes"].append(f"Kalshi price unavailable for {row.away_team}@{row.home_team} ({type(e).__name__}).")
    return g


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _env():
    return Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html", "j2"]))


def _clean(o):
    if isinstance(o, float):
        return None if o != o else round(o, 4)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    return o


def write(board: dict) -> dict:
    board = _clean(board)
    os.makedirs(DOCS, exist_ok=True)
    data_json = json.dumps(board).replace("</", "<\\/")
    page = _env().get_template("nfl_board.html.j2").render(data_json=data_json, pages_url=PAGES_URL)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    summary = dict(board.get("summary") or {"season": board["season"], "week": board["week"], "games": 0, "tracked": 0})
    summary["built_at"] = board["built_at"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    # Context for the checkpoint logger: favorites, defense status, kickoffs, Kalshi codes.
    ctx = {"season": board["season"], "week": board["week"], "built_at": board["built_at"], "games": [
        {"id": g["id"], "nflverse_id": g["nflverse_id"], "kick_utc": g["kick_utc"], "day_et": g["day_et"],
         "home": g["home_code"], "away": g["away_code"], "fav": g.get("fav"), "dog": g.get("dog"), "spread": g.get("spread"),
         "fav_defense_top10": g.get("fav_defense_top10"), "tracked": bool(g["scoring"].get("qualified")),
         "kalshi_away": sources.kalshi_code(g["away_code"]), "kalshi_home": sources.kalshi_code(g["home_code"]),
         # for the live engine (ESPN abbreviations) and the Live page
         "away_abbr": g["away"]["abbr"], "home_abbr": g["home"]["abbr"],
         "fav_abbr": sources.espn_code(g["fav"]) if g.get("fav") else None,
         "dog_abbr": sources.espn_code(g["dog"]) if g.get("dog") else None,
         "qbs": {t["abbr"]: {"regular": (t.get("qb") or {}).get("regular"), "rank": (t.get("qb") or {}).get("rank")}
                 for t in (g["away"], g["home"])},
         "hero": (g["scoring"].get("hero") or {}).get("score"), "band": (g["scoring"].get("hero") or {}).get("band"),
         "call": g["scoring"].get("call"), "kick_pt": g.get("kick_pt")}
        for g in board["games"]]}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "context.json"), "w") as f:
        json.dump(ctx, f, indent=1)
    with open(os.path.join(DOCS, "context.json"), "w") as f:      # the Live page reads this one
        json.dump(_clean(ctx), f, indent=1)
    os.makedirs(os.path.join(DATA, "boards"), exist_ok=True)
    with open(os.path.join(DATA, "boards", f"{board['season']}-w{board['week']:02d}.json" if board["week"] else "none.json"), "w") as f:
        json.dump(board, f, indent=1)
    print(f"NFL board written: week {board['week']}, {len(board['games'])} games, page {len(page) // 1024} KB")
    return summary
