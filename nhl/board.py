"""Build the NHL board: every game of the day, scored; only top-7 vs bottom-7 games can qualify.

Daily (owner Nov 12, as the NBA): today's games (Eastern date) until every game today is final (from 12 PM PT),
then tomorrow's games as a preview. Rebuilt through the day (scripts/nhl_scheduler.py); the day-of update comes
after starting goalies are posted. One hour before puck drop a game's tier LOCKS (data/nhl/locks.json).

    from nhl import board; b = board.build(); board.write(b)
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from nba.http import FetchError
from . import espn, kalshi, metrics, power, scoring, sources

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ROOT = sources.ROOT
DOCS = os.path.join(ROOT, "docs", "nhl")
DATA = sources.DATA
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"
LOCK_BEFORE = timedelta(hours=1)
NEXT_DAY_FROM_PT = 12


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def implied(ml):
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    return -ml / (-ml + 100) if ml < 0 else 100 / (ml + 100)


def target_day(now: datetime, today_events: list, locks: dict) -> tuple[date, str]:
    today = now.astimezone(ET).date()
    left = [e for e in today_events if e["state"] != "post" and e.get("season_type") in (2, None)]
    if now.astimezone(PT).hour >= NEXT_DAY_FROM_PT and not left:
        return today + timedelta(days=1), "preview"
    return today, "board"


def _last(name: str | None) -> str:
    return (str(name or "").split(".")[-1].split(" ")[-1]).strip().lower()


def _team(side: dict, tm: dict, pw: dict, rows: pd.DataFrame, pb: pd.DataFrame, inj: dict, day: str, is_fav: bool) -> dict:
    code = side["abbr"]
    m = tm.get(code) or {"season": {"ranks": {}}, "last5": {"ranks": {}}, "games": 0, "l3_record": None, "last_games": []}
    people = sources.key_people(pb, code) if len(pb) else {"skaters": [], "goalies": []}
    reg = people["goalies"][0] if people["goalies"] else None
    g = side.get("goalie")
    if not g or not g.get("name"):
        status = "unknown"
    elif reg and _last(g["name"]) == _last(reg["name"]):
        status = "regular"
    elif reg:
        status = "backup"
    else:
        status = "unknown"
    key_last = {_last(s["name"]): s["name"] for s in people["skaters"]}
    key_out, key_q, key_lt, injuries = [], [], [], []
    for i in inj.get(code, []):
        injuries.append(i)
        k = _last(i.get("last") or i.get("name"))
        if k not in key_last:
            continue
        if i.get("status") in scoring.LONG_TERM:
            key_lt.append(i["name"])
        elif i.get("status") in scoring.OUT:
            key_out.append(i["name"])
        elif i.get("status") == "Day-To-Day":
            key_q.append(i["name"])
    games = [{**x, "opp_power_rank": (pw.get(x["opp"]) or {}).get("rank")} for x in m.get("last_games") or []]
    return {"id": side["espn_id"], "abbr": code, "espn_abbr": side["espn_abbr"], "name": side["name"], "display": side.get("display"),
            "record": side.get("record") or m.get("record"), "l3_record": m.get("l3_record"), "games": m.get("games", 0),
            "pts_pct": m.get("pts_pct"), "season": m["season"], "last5": m["last5"], "last3_games": games, "trend_games": m.get("trend_games") or [], "is_fav": is_fav,
            "b2b": metrics.b2b(rows, code, day) if len(rows) else False, "power": pw.get(code, {}),
            "goalie": g or {}, "goalie_status": status, "regular_goalie_name": reg["name"] if reg else None,
            "goalies": people["goalies"][:3], "key_skaters": [s["name"] for s in people["skaters"]],
            "injuries": injuries, "injuries_known": True, "key_out": key_out, "key_questionable": key_q, "key_long_term": key_lt}


def summary_text(g: dict) -> tuple[str, str]:
    sc = g["scoring"]
    fav, dog = (g["home"], g["away"]) if g["favorite"] == "home" else (g["away"], g["home"])
    fp, dp = (fav.get("power") or {}).get("rank"), (dog.get("power") or {}).get("rank")
    parts = []
    if not sc["in_range"]:
        parts.append(f"Not a top-{scoring.TOP} vs bottom-{scoring.N - scoring.BOTTOM_FROM + 1} matchup ({fav['abbr']} #{fp or '–'}, {dog['abbr']} #{dp or '–'}).")
    elif sc["tier"] == "qualified":
        parts.append(f"{fav['name']} (#{fp}) against {dog['name']} (#{dp}): a top-{scoring.TOP} vs bottom-{scoring.N - scoring.BOTTOM_FROM + 1} game, Analysis Score {sc['score']}.")
    else:
        parts.append(sc["fails"][0].rstrip(".") + ".")
    gs = fav.get("goalie") or {}
    if fav["goalie_status"] == "backup":
        parts.append(f"{fav['abbr']} start their backup goalie, {gs.get('name')} (regular: {fav.get('regular_goalie_name') or '?'}).")
    elif fav["goalie_status"] == "regular":
        parts.append(f"{gs.get('name')} in goal for {fav['abbr']} ({'confirmed' if gs.get('confirmed') else 'probable'}).")
    best = None
    for m in metrics.METRICS:
        fr, dr = (fav["last5"].get("ranks") or {}).get(m), (dog["last5"].get("ranks") or {}).get(m)
        if fr and dr and (best is None or dr - fr > best[0]):
            best = (dr - fr, m, fr, dr)
    if best and best[0] >= 15:
        parts.append(f"Biggest edge over the last 5: {metrics.LABELS[best[1]]} ({fav['abbr']} #{best[2]}, {dog['abbr']} #{best[3]}).")
    form = f"{fav['abbr']} {fav.get('l3_record') or '?'} in its last 3, {dog['abbr']} {dog.get('l3_record') or '?'}"
    tired = [t["abbr"] for t in (fav, dog) if t.get("b2b")]
    if tired:
        form += f"; {' and '.join(tired)} played yesterday"
    parts.append(form + ".")
    lean = f"Lean: {fav['abbr']}" if sc["tier"] == "qualified" else (f"Lean: {fav['abbr']} (watch list)" if sc["tier"] == "watch" else "No lean")
    return " ".join(parts), lean


def build(now: datetime | None = None, refresh: bool = True, day: date | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    notes = []
    locks = load_json(os.path.join(DATA, "locks.json"), {})
    today = now.astimezone(ET).date()
    try:
        today_events = espn.parse_events(espn.scoreboard(f"{today:%Y%m%d}"))
    except FetchError as e:
        today_events = []
        notes.append(f"ESPN scoreboard unavailable ({e}).")
    if day is None:
        day, kind = target_day(now, today_events, locks)
    else:
        kind = "board" if day == today else ("preview" if day > today else "rebuilt")
    events = today_events if day == today else []
    if day != today:
        try:
            events = espn.parse_events(espn.scoreboard(f"{day:%Y%m%d}"))
        except FetchError as e:
            notes.append(f"ESPN scoreboard unavailable ({e}).")
    season = sources.season_for(day)
    rows = sources.game_rows(season, refresh)
    pb = sources.players(season, refresh)
    ds = day.isoformat()
    tm = metrics.compute(rows, before=ds) if len(rows) else {}
    own = metrics.own_rank(tm) if len(tm) == 32 else {}
    pub = power.published(day)
    pw = power.consensus(own, pub)
    if not pub:
        notes.append("No published power rankings saved yet: the consensus is our own rank only.")
    try:
        inj = espn.injuries()
    except FetchError:
        inj = {}
        notes.append("ESPN injury list unavailable: key skaters not checked.")
    try:
        k_events = kalshi.open_events()
    except FetchError:
        k_events = []
        notes.append("Kalshi prices unavailable.")
    through = rows["date"].max() if len(rows) else None
    built_label = now.astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT")
    board = {"date": ds, "date_label": datetime.combine(day, datetime.min.time()).strftime("%A, %B %-d"), "kind": kind,
             "season": season, "built_at": now.astimezone(PT).isoformat(timespec="seconds"), "built_label": built_label,
             "metrics_through": through, "metrics_through_label": (datetime.fromisoformat(through).strftime("%b %-d") if through else "–"),
             "max": 40, "top": scoring.TOP, "bottom_from": scoring.BOTTOM_FROM, "power_sources": {k: {"date": v["date"], "url": v["url"]} for k, v in pub.items()},
             "games": [], "notes": notes}
    for ev in events:
        if ev.get("season_type") not in (2, None):
            continue
        lock = locks.get(ev["id"])
        if lock:
            g = lock["game"]
            g.update(state=ev["state"], detail=ev.get("detail"))
            if ev["state"] != "pre":
                g["score"] = {"home": ev["home"]["score"], "away": ev["away"]["score"]}
            g["locked_at"] = lock["at"]
            board["games"].append(g)
            continue
        tip = datetime.fromisoformat(ev["start_utc"].replace("Z", "+00:00"))
        g = _game(ev, tip, tm, pw, rows, pb, inj, ds, k_events)
        board["games"].append(g)
        if ev["state"] == "pre" and now >= tip - LOCK_BEFORE:
            at = now.astimezone(PT).isoformat(timespec="seconds")
            locks[ev["id"]] = {"at": at, "day": ds, "game": g}
            g["locked_at"] = at
    attach_readouts(board, DATA)
    keep_from = (day - timedelta(days=1)).isoformat()
    board["_locks"] = {k: v for k, v in locks.items() if v.get("day", "") >= keep_from}
    board["games"] = scoring.order_games(board["games"])
    ahead = [g for g in board["games"] if g.get("state") != "post"]
    tier = lambda g: g["scoring"]["tier"]  # noqa: E731
    board["summary"] = {"date": ds, "date_label": board["date_label"], "kind": kind, "games": len(board["games"]),
                        "in_range": sum(1 for g in board["games"] if g["scoring"]["in_range"]),
                        "qualified": sum(1 for g in ahead if tier(g) == "qualified"), "watch": sum(1 for g in ahead if tier(g) == "watch"),
                        "built_label": built_label}
    board["power_table"] = sorted(({"abbr": t, **v} for t, v in pw.items()), key=lambda r: r["rank"])
    log = load_json(os.path.join(DATA, "power_log.json"), {})
    log[ds] = {t: v["rank"] for t, v in pw.items()}
    board["_power_log"] = dict(sorted(log.items())[-120:])
    return board


def _game(ev, tip, tm, pw, rows, pb, inj, ds, k_events) -> dict:
    h, a = ev["home"]["abbr"], ev["away"]["abbr"]
    rh, ra = (pw.get(h) or {}).get("rank"), (pw.get(a) or {}).get("rank")
    o = ev.get("odds") or {}
    ph, pa = implied(o.get("ml_home")), implied(o.get("ml_away"))
    rng_home = scoring.in_range(rh, ra)
    rng_away = scoring.in_range(ra, rh)
    if rng_home or rng_away:
        fav_side = "home" if rng_home else "away"
    elif o.get("favorite"):
        fav_side = o["favorite"]
    elif rh and ra:
        fav_side = "home" if rh <= ra else "away"
    else:
        fav_side = "home"
    g = {"id": ev["id"], "tip_utc": tip.isoformat(), "tip_pt": tip.astimezone(PT).strftime("%-I:%M %p PT"),
         "day_et": tip.astimezone(ET).date().isoformat(), "state": ev["state"], "detail": ev.get("detail"),
         "neutral": ev["neutral"], "venue": ev.get("venue"), "favorite": fav_side,
         "line": {"details": o.get("details"), "provider": o.get("provider"), "ml_home": o.get("ml_home"), "ml_away": o.get("ml_away"),
                  "puck_line": o.get("puck_line"), "total": o.get("total"), "fav_price": ph if fav_side == "home" else pa}}
    g["spread"] = {"details": o.get("details") or "no line yet", "provider": o.get("provider") or "ESPN", "puck_line": o.get("puck_line")}
    for s in ("home", "away"):
        g[s] = _team(ev[s], tm, pw, rows, pb, inj, ds, s == fav_side)
        ml = o.get(f"ml_{s}")
        g[s]["spread_text"] = (f"{'+' if ml > 0 else ''}{int(ml)}" if isinstance(ml, (int, float)) else "")
    if ev["state"] != "pre":
        g["score"] = {"home": ev["home"]["score"], "away": ev["away"]["score"]}
    fav, dog = g[fav_side], g["away" if fav_side == "home" else "home"]
    g["scoring"] = scoring.score_game(g, fav, dog, ds, in_range=bool(rng_home or rng_away))
    g["summary"], g["lean"] = summary_text(g)
    if k_events:
        names = lambda s: [ev[s].get("name"), ev[s].get("display")]  # noqa: E731
        ka, kh = {"abbr": ev["away"]["espn_abbr"], "names": names("away")}, {"abbr": ev["home"]["espn_abbr"], "names": names("home")}
        e = kalshi.match(k_events, tip.astimezone(ET).date(), ka, kh)
        if e:
            p = kalshi.prices(e, ka, kh)
            fp = p.get(fav_side) or {}
            g["kalshi"] = {"event": p["event"], "fav_prob": fp.get("prob"), "bid": fp.get("bid"), "ask": fp.get("ask")}
    return g


def attach_readouts(board: dict, data_dir: str):
    cache = {}
    for g in board.get("games", []):
        if not (g.get("scoring") or {}).get("tracked"):
            g.pop("readout", None)
            continue
        day = g.get("day_et")
        if day not in cache:
            cache[day] = load_json(os.path.join(data_dir, "readouts", f"{day}.json"), {})
        r = (cache[day].get("games") or {}).get(str(g["id"]))
        if isinstance(r, dict) and r.get("summary"):
            g["readout"] = {**r, "updated": r.get("updated") or cache[day].get("updated")}
        else:
            g.pop("readout", None)


def _clean(o):
    if isinstance(o, float):
        return None if o != o else round(o, 4)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    return o


def context_row(g: dict) -> dict:
    sc = g["scoring"]
    fav, dog = (g["home"], g["away"]) if g["favorite"] == "home" else (g["away"], g["home"])
    return {"id": g["id"], "tip_utc": g["tip_utc"], "tip_pt": g["tip_pt"], "day_et": g["day_et"],
            "away_abbr": g["away"]["abbr"], "home_abbr": g["home"]["abbr"], "away_espn": g["away"]["espn_abbr"], "home_espn": g["home"]["espn_abbr"],
            "fav_abbr": fav["abbr"], "fav_espn": fav["espn_abbr"], "dog_abbr": dog["abbr"], "fav_home": g["favorite"] == "home",
            "away_names": [g["away"].get("name"), g["away"].get("display")], "home_names": [g["home"].get("name"), g["home"].get("display")],
            "tier": sc["tier"], "tier_label": sc["tier_label"], "tracked": sc["tracked"], "alerts": sc["alerts"], "score": sc["score"],
            "in_range": sc["in_range"], "fav_rank": (fav.get("power") or {}).get("rank"), "dog_rank": (dog.get("power") or {}).get("rank"),
            "fav_def_rank": ((fav.get("season") or {}).get("ranks") or {}).get("xga60"),
            "dog_def_rank": ((dog.get("season") or {}).get("ranks") or {}).get("xga60"),
            "fav_b2b": bool(fav.get("b2b")), "fav_goalie": (fav.get("goalie") or {}).get("name"), "fav_goalie_status": fav.get("goalie_status"),
            "kalshi_event": (g.get("kalshi") or {}).get("event"), "locked": bool(g.get("locked_at"))}


def write(board: dict) -> dict:
    locks = board.pop("_locks", None)
    plog = board.pop("_power_log", None)
    board = _clean(board)
    os.makedirs(DOCS, exist_ok=True)
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html", "j2"]))
    page = env.get_template("nhl_board.html.j2").render(data_json=json.dumps(board).replace("</", "<\\/"), pages_url=PAGES_URL)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    summary = dict(board.get("summary") or {})
    summary["built_at"] = board["built_at"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    os.makedirs(DATA, exist_ok=True)
    if locks is not None:
        with open(os.path.join(DATA, "locks.json"), "w") as f:
            json.dump(_clean(locks), f, indent=1)
    if plog is not None:
        with open(os.path.join(DATA, "power_log.json"), "w") as f:
            json.dump(plog, f)
    ctx = {"date": board["date"], "season": board["season"], "built_at": board["built_at"],
           "games": [context_row(g) for g in board["games"]]}
    for p in (os.path.join(DATA, "context.json"), os.path.join(DOCS, "context.json")):
        with open(p, "w") as f:
            json.dump(ctx, f, indent=1)
    os.makedirs(os.path.join(DATA, "boards"), exist_ok=True)
    with open(os.path.join(DATA, "boards", f"{board['date']}.json"), "w") as f:
        json.dump(board, f, indent=1)
    print(f"NHL board written: {board['date']} ({board['kind']}), {len(board['games'])} games, "
          f"{board['summary']['in_range']} top-{scoring.TOP} vs bottom-{scoring.N - scoring.BOTTOM_FROM + 1}, page {len(page) // 1024} KB")
    return summary
