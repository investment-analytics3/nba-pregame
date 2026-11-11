"""Build the college football board for the current week: FBS vs FBS games with the spread
favorite at -10 to -20, scored, tiered and published (docs/cfb/).

Weekly board (owner Nov 10): the coming week's games from Sunday on (spreads settle by Tuesday),
refreshed twice a day and hourly on game days (scripts/cfb_scheduler.py). A game whose spread
leaves the -10 to -20 range drops off; one that enters it is added. One hour before kickoff a
game's tier LOCKS (data/cfb/locks.json): it stays on the board with that scoring, whatever the
line does, and hands off to live tracking.

    from cfb import board; b = board.build(); board.write(b)
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from nba.http import FetchError
from . import espn, kalshi, metrics, scoring, sources

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ROOT = sources.ROOT
DOCS = os.path.join(ROOT, "docs", "cfb")
DATA = sources.DATA
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"
LOCK_BEFORE = timedelta(hours=1)


def season_for(d: date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def target_week(sched: pd.DataFrame, now: datetime):
    """The week of the next FBS game still to be played (or in progress)."""
    s = sched.assign(kick=pd.to_datetime(sched["start_date"], utc=True))
    fbs = s[(s["home_division"] == "fbs") | (s["away_division"] == "fbs")]
    ahead = fbs[(fbs["completed"] != True) & (fbs["kick"] >= pd.Timestamp(now) - pd.Timedelta(hours=5))]  # noqa: E712
    return int(ahead["week"].min()) if len(ahead) else None


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def power_table(wm: dict, notes: list) -> dict:
    """{team_id: {"consensus", "fpi", "own_net", "ap", "cfp"}}. Consensus = average of FPI and our net EPA rank."""
    try:
        fpi = espn.fpi_ranks()
    except FetchError:
        fpi = {}
        notes.append("ESPN FPI unavailable; power rank uses our net EPA rank only.")
    try:
        polls = espn.rankings()
    except FetchError:
        polls = {"ap": {}, "cfp": {}}
        notes.append("AP / CFP rankings unavailable; the power gate checks FPI only.")
    own = {t: v["net_rank"] for t, v in wm.items()}
    teams = set(own) | set(fpi)
    avg = {}
    for t in teams:
        v = [x for x in (fpi.get(t), own.get(t)) if x is not None]
        if v:
            avg[t] = sum(v) / len(v)
    order = sorted(avg, key=lambda t: (avg[t], fpi.get(t, 999), t))
    out = {t: {"consensus": i + 1, "fpi": fpi.get(t), "own_net": own.get(t)} for i, t in enumerate(order)}
    for key in ("ap", "cfp"):
        for t, r in polls.get(key, {}).items():
            out.setdefault(t, {})[key] = r
    return out


def _team_block(tid: int, side: dict, wm: dict, recs: dict, pl: dict, inj: list | None, kick: datetime, pw: dict) -> dict:
    m = wm.get(tid) or {"season": {"ranks": {}}, "last3": {"ranks": {}}, "net": None, "net_rank": None}
    rec = recs.get(tid, {})
    last = rec.get("last_date")
    t = {"code": tid, "abbr": side["abbr"], "name": side.get("name"), "display": side.get("display"),
         "season": m["season"], "last3": m["last3"], "net": m.get("net"), "net_rank": m.get("net_rank"),
         "record": side.get("record") or rec.get("record", "0-0"), "last2_results": rec.get("last2", []),
         "last3_results": rec.get("last3", []), "rest": (kick - last.to_pydatetime()).days if last is not None else None,
         "qb": (pl.get(tid) or {}).get("qb"), "injuries_known": bool(inj), "power": pw.get(tid, {})}
    inj = inj or []
    key = (pl.get(tid) or {}).get("key", [])
    by = lambda st: {metrics.norm_name(i["name"]) for i in inj if i.get("status") in st}  # noqa: E731
    out_n, lt_n = by(scoring.OUT), by(scoring.LONG_TERM)
    q_n = {metrics.norm_name(i["name"]) for i in inj if i.get("status") == "Questionable"}
    t["key_out"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in out_n]
    t["key_long_term"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in lt_n]
    t["key_questionable"] = [k["name"] for k in key if metrics.norm_name(k["name"]) in q_n]
    qb = (t["qb"] or {}).get("regular")
    t["qb_out"] = [qb] if qb and metrics.norm_name(qb) in (out_n | lt_n) else []
    t["qb_active"] = not t["qb_out"]
    if t["qb_out"]:
        t["qb_reset"] = f"{qb} out"
    elif t["qb"] and t["qb"].get("dropbacks", 999) < 100:
        t["qb_reset"] = f"{qb}, {t['qb']['dropbacks']} dropbacks this season"
    t["injury_list"] = [i for i in inj if i.get("status") in (scoring.OUT | scoring.LONG_TERM | {"Questionable"})][:16]
    return t


def build(now: datetime | None = None, refresh: bool = True) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    season = season_for(now.astimezone(ET).date())
    board = {"season": season, "week": None, "built_at": now.astimezone(PT).isoformat(timespec="seconds"),
             "built_label": now.astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT"), "games": [], "notes": [],
             "spread_band": [scoring.SPREAD_MIN, scoring.SPREAD_MAX]}
    sched = sources.schedule(season, refresh)
    week = target_week(sched, now)
    board["week"] = week
    if week is None:
        board["notes"].append("No upcoming FBS games on the schedule.")
        board["summary"] = {"season": season, "week": None, "games": 0, "qualified": 0, "watch": 0, "built_label": board["built_label"]}
        return board
    fbs = sources.fbs_ids(sched)
    pbp = sources.pbp(season, refresh)
    tg = metrics.team_games(pbp)
    wm = metrics.week_metrics(tg, week, fbs)
    pl = metrics.players(pbp, week)
    board["data_through"] = f"Week {int(tg['week'].max())}" if len(tg) else "none"
    board["n_fbs"] = len(fbs)
    pw = power_table(wm, board["notes"])
    try:
        events = espn.parse_events(espn.scoreboard_week(week))
    except FetchError as e:
        events = []
        board["notes"].append(f"ESPN scoreboard unavailable ({e}).")
    try:
        k_events = kalshi.open_events()
    except FetchError:
        k_events = []
        board["notes"].append("Kalshi prices unavailable.")
    sched_by = {int(r.game_id): r for r in sched.itertuples()}
    locks = load_json(os.path.join(DATA, "locks.json"), {})
    other = 0
    for ev in events:
        sr = sched_by.get(int(ev["id"]))
        h_id, a_id = int(ev["home"]["id"]), int(ev["away"]["id"])
        if h_id not in fbs or a_id not in fbs:
            continue
        kick = datetime.fromisoformat(ev["start_utc"].replace("Z", "+00:00"))
        lock = locks.get(ev["id"])
        if lock:                                          # locked 1 h before kickoff: keep its scoring
            g = lock["game"]
            g.update(state=ev["state"], detail=ev.get("detail"), score={"home": ev["home"]["score"], "away": ev["away"]["score"]})
            g["locked_at"] = lock["at"]
            board["games"].append(g)
            continue
        if ev["state"] == "post":
            continue                                      # finished and never locked: not on this board
        odds = ev.get("odds") or {}
        if not odds.get("favorite") or not scoring.in_band(odds.get("spread")):
            other += 1
            continue
        g = _game(ev, sr, kick, odds, wm, pl, pw, k_events, board, sched)
        board["games"].append(g)
        if now >= kick - LOCK_BEFORE:
            at = now.astimezone(PT).isoformat(timespec="seconds")
            locks[ev["id"]] = {"at": at, "week": week, "game": g}
            g["locked_at"] = at
    board["other_fbs_games"] = other
    attach_readouts(board, DATA)
    # keep only this season's locks for the current and previous week
    board["_locks"] = {k: v for k, v in locks.items() if v.get("week", 0) >= week - 1}
    board["games"].sort(key=lambda g: (g["state"] == "post", g["kick_utc"]))
    ahead = [g for g in board["games"] if g["state"] != "post"]
    board["summary"] = {"season": season, "week": week, "games": len(board["games"]),
                        "upcoming": sum(1 for g in board["games"] if g["state"] == "pre"),
                        "qualified": sum(1 for g in ahead if g["scoring"]["tier"] == "qualified"),
                        "watch": sum(1 for g in ahead if g["scoring"]["tier"] == "watch"),
                        "other_fbs_games": other, "built_label": board["built_label"]}
    return board


def attach_readouts(board: dict, data_dir: str):
    """Analyst readouts (owner Nov 10), written on game-day mornings by the owner's Claude scheduled task
    "Football analyst readouts" into <data_dir>/readouts/<Eastern date>.json. Attached to tracked games
    (Qualified + Watch list) only; information only, never changes a tier or call."""
    cache = {}
    for g in board.get("games", []):
        if not (g.get("scoring") or {}).get("tracked"):
            g.pop("readout", None)
            continue
        day = g.get("day_et")
        if day not in cache:
            try:
                with open(os.path.join(data_dir, "readouts", f"{day}.json")) as f:
                    cache[day] = json.load(f)
            except (OSError, ValueError):
                cache[day] = {}
        raw = cache[day]
        r = (raw.get("games") or {}).get(str(g["id"]))
        if isinstance(r, dict) and r.get("summary"):
            g["readout"] = {**r, "updated": r.get("updated") or raw.get("updated")}
        else:
            g.pop("readout", None)


def _game(ev, sr, kick, odds, wm, pl, pw, k_events, board, sched) -> dict:
    fav_side = odds["favorite"]
    L = odds["lines"][fav_side]
    spread = L["cur"] if L["cur"] is not None else -abs(odds["spread"])
    g = {"id": ev["id"], "kick_utc": kick.isoformat(), "kick_pt": kick.astimezone(PT).strftime("%a %-I:%M %p PT"),
         "day_et": kick.astimezone(ET).date().isoformat(), "state": ev["state"], "detail": ev.get("detail"),
         "neutral": bool(ev.get("neutral")) or (bool(sr.neutral_site) if sr is not None else False), "venue": ev.get("venue"),
         "favorite": fav_side, "fav_home": fav_side == "home", "spread": spread, "open_spread": L.get("open"),
         "line_source": odds.get("provider") or "ESPN", "total": odds.get("total"), "fav_ml": L.get("ml")}
    g["movement"] = round(g["open_spread"] - spread, 1) if g["open_spread"] is not None else None
    if ev["state"] != "pre":
        g["score"] = {"home": ev["home"]["score"], "away": ev["away"]["score"]}
    summ = {}
    try:
        summ = espn.summary(ev["id"])
    except FetchError:
        board["notes"].append(f"{ev['short']}: ESPN game page unavailable.")
    inj = espn.parse_injuries(summ) if summ.get("injuries") else {}
    pred = espn.parse_predictor(summ) if summ else {}
    recs = metrics.records(sched, pd.Timestamp(kick))
    home = _team_block(int(ev["home"]["id"]), ev["home"], wm, recs, pl, inj.get(str(ev["home"]["id"])), kick, pw)
    away = _team_block(int(ev["away"]["id"]), ev["away"], wm, recs, pl, inj.get(str(ev["away"]["id"])), kick, pw)
    if pred:
        home["fpi_win"], away["fpi_win"] = round(pred["home"], 3), round(pred["away"], 3)
    g["home"], g["away"] = home, away
    fav, dog = (home, away) if fav_side == "home" else (away, home)
    g["fav"], g["dog"] = fav["code"], dog["code"]
    g["scoring"] = scoring.score_game(g, fav, dog, board.get("n_fbs", 138))
    dr = (wm.get(fav["code"]) or {})
    g["fav_defense_top25"] = bool(dr) and all((dr[w]["ranks"].get(m) or 999) <= scoring.STRONG
                                               for w in ("season", "last3") for m in ("def_epa", "def_sr"))
    if k_events:
        names = lambda t, s: [t.get("display"), t.get("name"), (s.get("display") or "").rsplit(" ", 1)[0]]  # noqa: E731
        a = {"abbr": ev["away"]["abbr"], "names": names(away, ev["away"])}
        h = {"abbr": ev["home"]["abbr"], "names": names(home, ev["home"])}
        e = kalshi.match(k_events, kick.astimezone(ET).date(), a, h)
        if e:
            p = kalshi.prices(e, a, h)
            fp = p.get(fav_side) or {}
            g["kalshi"] = {"event": p["event"], "fav_prob": fp.get("prob"), "bid": fp.get("bid"), "ask": fp.get("ask")}
    return g


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _clean(o):
    if isinstance(o, float):
        return None if o != o else round(o, 4)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def write(board: dict) -> dict:
    locks = board.pop("_locks", None)
    board = _clean(board)
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(os.path.join(DATA, "boards"), exist_ok=True)
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html", "j2"]))
    page = env.get_template("cfb_board.html.j2").render(data_json=json.dumps(board).replace("</", "<\\/"), pages_url=PAGES_URL)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    summary = dict(board.get("summary") or {})
    summary["built_at"] = board["built_at"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    if locks is not None:
        with open(os.path.join(DATA, "locks.json"), "w") as f:
            json.dump(_clean(locks), f, indent=1)
    ctx = {"season": board["season"], "week": board["week"], "built_at": board["built_at"], "games": [context_row(g) for g in board["games"]]}
    for p in (os.path.join(DATA, "context.json"), os.path.join(DOCS, "context.json")):
        with open(p, "w") as f:
            json.dump(ctx, f, indent=1)
    with open(os.path.join(DATA, "boards", f"{board['season']}-w{board['week']:02d}.json" if board["week"] else "none.json"), "w") as f:
        json.dump(board, f, indent=1)
    print(f"College board written: week {board['week']}, {len(board['games'])} games in range, page {len(page) // 1024} KB")
    return summary


def context_row(g: dict) -> dict:
    """What the checkpoint logger and the Live page need for one game."""
    sc = g["scoring"]
    fav, dog = (g["home"], g["away"]) if g["fav"] == g["home"]["code"] else (g["away"], g["home"])
    return {"id": g["id"], "kick_utc": g["kick_utc"], "kick_pt": g["kick_pt"], "day_et": g["day_et"],
            "away_abbr": g["away"]["abbr"], "home_abbr": g["home"]["abbr"], "fav_abbr": fav["abbr"], "dog_abbr": dog["abbr"],
            "fav_home": g["fav_home"], "spread": g["spread"],
            "away_names": [g["away"].get("display"), g["away"].get("name")], "home_names": [g["home"].get("display"), g["home"].get("name")],
            "tier": sc["tier"], "tier_label": sc["tier_label"], "tracked": sc["tracked"], "alerts": sc["alerts"],
            "call": sc["call"], "hero": sc["hero"]["score"], "fav_defense_top10": bool(g.get("fav_defense_top25")),
            "qbs": {t["abbr"]: {"regular": (t.get("qb") or {}).get("regular"), "rank": (t.get("qb") or {}).get("rank")}
                    for t in (g["away"], g["home"])},
            "kalshi_event": (g.get("kalshi") or {}).get("event"), "locked": bool(g.get("locked_at"))}
