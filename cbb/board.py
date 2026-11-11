"""Build the men's college basketball board: Division I games with the spread favorite at -10 to -20,
scored like the NBA board (college cutoffs), tiered and published to docs/cbb/.

Daily board (owner Nov 11): today's games (Eastern date) until every in-range game today is final,
then tomorrow's games as a preview. Rebuilt through the day (scripts/cbb_scheduler.py). One hour
before tip a game's tier LOCKS (data/cbb/locks.json): it stays on the board with that scoring,
whatever the line does, and hands off to live tracking.

    from cbb import board; b = board.build(); board.write(b)
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
DOCS = os.path.join(ROOT, "docs", "cbb")
DATA = sources.DATA
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"
LOCK_BEFORE = timedelta(hours=1)
NEXT_DAY_FROM_PT = 12          # once today's in-range games are all final (and it's afternoon PT), show tomorrow


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def power_table(tm: dict, notes: list) -> dict:
    """{team_id: {"rank" (consensus), "bpi", "own_net", "ap"}}. Consensus = average of BPI and our net-rating rank."""
    try:
        bpi = espn.bpi_ranks()
    except FetchError:
        bpi = {}
        notes.append("ESPN BPI unavailable; power rank uses our net rating only and the power gate checks the AP poll only.")
    try:
        ap = espn.rankings()["ap"]
    except FetchError:
        ap = {}
        notes.append("AP poll unavailable; the power gate checks BPI only.")
    own = {t: v["season"]["ranks"].get("net") for t, v in tm.items() if v["season"]["ranks"].get("net")}
    avg = {}
    for t in set(own) | set(bpi):
        v = [x for x in (bpi.get(t), own.get(t)) if x is not None]
        avg[t] = sum(v) / len(v)
    order = sorted(avg, key=lambda t: (avg[t], bpi.get(t, 999), t))
    out = {t: {"rank": i + 1, "bpi": bpi.get(t), "own_net": own.get(t)} for i, t in enumerate(order)}
    for t, r in ap.items():
        out.setdefault(t, {})["ap"] = r
    return out


def target_day(now: datetime, today_events: list, locks: dict) -> tuple[date, str]:
    """(day, kind): today's board, or tomorrow's preview once today's in-range games are all final."""
    today = now.astimezone(ET).date()
    ids = {e["id"] for e in today_events if in_range(e)} | {k for k, v in locks.items() if v.get("day") == today.isoformat()}
    left = [e for e in today_events if e["id"] in ids and e["state"] != "post"]
    if now.astimezone(PT).hour >= NEXT_DAY_FROM_PT and not left:
        return today + timedelta(days=1), "preview"
    return today, "board"


def in_range(ev: dict) -> bool:
    o = ev.get("odds") or {}
    return bool(o.get("favorite_id")) and scoring.in_band(o.get("spread"))


def _team(side: dict, tm: dict, pw: dict, rows: pd.DataFrame, pb: pd.DataFrame, day: str, is_fav: bool, spread_txt: str) -> dict:
    tid = int(side["id"])
    m = tm.get(tid) or {"season": {"ranks": {}}, "last3": {"ranks": {}}, "games": 0, "l3_record": None, "last3_games": []}
    games = []
    for x in m.get("last3_games") or []:
        games.append({**x, "opp_power_rank": (pw.get(x["opp_id"]) or {}).get("rank")})
    kp = metrics.key_players(pb, tid) if len(pb) else []
    scorers = []
    if len(pb):
        p = pb[(pb["team_id"].astype(int) == tid) & (pb["did_not_play"] != True)]  # noqa: E712
        if len(p):
            g = p.groupby("athlete_display_name").agg(pts=("points", "sum"), gp=("game_id", "nunique"))
            g = g[g["gp"] >= 1].assign(ppg=lambda d: d["pts"] / d["gp"]).sort_values("ppg", ascending=False).head(2)
            scorers = [{"name": n, "ppg": round(float(r.ppg), 1)} for n, r in g.iterrows()]
    return {"id": side["id"], "abbr": side["abbr"], "name": side["name"], "display": side.get("display"), "mascot": side.get("mascot"),
            "record": side.get("record") or m.get("record"), "l3_record": m.get("l3_record"), "games": m.get("games", 0),
            "season": m["season"], "last3": m["last3"], "last3_games": games, "is_fav": is_fav, "spread_text": spread_txt,
            "b2b": metrics.b2b(rows, tid, day), "power": pw.get(tid, {}), "key_players": [k["name"] for k in kp][:7],
            "top_scorers": scorers, "injuries": [], "injuries_known": False, "key_out": [], "key_questionable": []}


def summary_text(g: dict) -> tuple[str, str]:
    sc = g["scoring"]
    fav, dog = (g["home"], g["away"]) if g["favorite"] == "home" else (g["away"], g["home"])
    parts = []
    if sc["tier"] == "qualified":
        why = []
        if any(r.startswith("Score") for r in sc["routes"]):
            why.append(f"an Analysis Score of {sc['score']}")
        if "Last-3 mismatch" in sc["routes"]:
            why.append(f"the last-3 mismatch ({fav['abbr']} top 50 and {dog['abbr']} bottom 50 in at least 3 of 4 metrics over the last 3)")
        parts.append(f"{fav['name']} qualify on {' and '.join(why)}.")
    elif sc["tier"] == "watch":
        parts.append(sc["fails"][0].rstrip(".") + ".")
    else:
        parts.append(f"No track: {sc['fails'][0][0].lower() + sc['fails'][0][1:]}.".replace("..", "."))
    best = None
    for fm, dm in (("efg", "opp_efg"), ("off", "def"), ("opp_efg", "efg"), ("def", "off")):
        fr = (fav["last3"].get("ranks") or {}).get(fm)
        dr = (dog["last3"].get("ranks") or {}).get(dm)
        if fr and dr and (best is None or dr - fr > best[0]):
            best = (dr - fr, fm, fr, dm, dr)
    if best and best[0] >= 100:
        _, fm, fr, dm, dr = best
        parts.append(f"Biggest edge over the last 3: {fav['abbr']} {metrics.LABELS[fm]} (#{fr}) against {dog['abbr']} {metrics.LABELS[dm]} (#{dr}).")
    elif best and best[0] <= -50:
        _, fm, fr, dm, dr = best
        parts.append(f"Watch the {metrics.LABELS[fm]} matchup: {dog['abbr']} (#{dr}) has the better of {fav['abbr']} (#{fr}) lately.")
    form = f"{fav['abbr']} {fav.get('l3_record') or '?'} in its last 3, {dog['abbr']} {dog.get('l3_record') or '?'}"
    tired = [t["abbr"] for t in (fav, dog) if t.get("b2b")]
    if tired:
        form += f"; {' and '.join(tired)} played yesterday"
    parts.append(form + ".")
    if min(fav.get("games") or 0, dog.get("games") or 0) < 5:
        parts.append("Early in the season: fewer than 5 D-I games for a team, so the ranks move a lot.")
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
    if day == today:
        events = today_events
    else:
        try:
            events = espn.parse_events(espn.scoreboard(f"{day:%Y%m%d}"))
        except FetchError as e:
            events = []
            notes.append(f"ESPN scoreboard unavailable ({e}).")
    season = sources.season_for(day)
    d1_info = sources.d1_teams(refresh)
    d1 = set(d1_info)
    rows_raw, through = sources.season_rows(season, today, d1, notes, refresh)
    rows = metrics.box_rows(rows_raw) if len(rows_raw) else rows_raw
    pb = sources.player_box(season, refresh)
    ds = day.isoformat()
    tm = metrics.compute(rows, d1, before=ds) if len(rows) else {}
    n = len(d1) or scoring.N_D1
    pw = power_table(tm, notes)
    built_label = now.astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT")
    board = {"date": ds, "date_label": datetime.combine(day, datetime.min.time()).strftime("%A, %B %-d"), "kind": kind,
             "season": season, "built_at": now.astimezone(PT).isoformat(timespec="seconds"), "built_label": built_label,
             "metrics_through": through, "metrics_through_label": (datetime.fromisoformat(through).strftime("%b %-d") if through else "–"),
             "n_d1": n, "max": 40, "early": scoring.early_season(ds), "cutover": scoring._cut_label(),
             "spread_band": [scoring.SPREAD_MIN, scoring.SPREAD_MAX], "games": [], "notes": notes}
    try:
        k_events = kalshi.open_events()
    except FetchError:
        k_events = []
        notes.append("Kalshi prices unavailable.")
    other = no_line = 0
    for ev in events:
        h_id, a_id = int(ev["home"]["id"]), int(ev["away"]["id"])
        lock = locks.get(ev["id"])
        if lock:                                          # locked 1 h before tip: keep its scoring
            g = lock["game"]
            g.update(state=ev["state"], detail=ev.get("detail"), score={"home": ev["home"]["score"], "away": ev["away"]["score"]})
            g["locked_at"] = lock["at"]
            board["games"].append(g)
            continue
        if h_id not in d1 or a_id not in d1:
            continue
        if ev["state"] == "post":
            continue
        if not ev.get("odds") or not ev["odds"].get("favorite_id"):
            no_line += 1
            continue
        if not in_range(ev):
            other += 1
            continue
        tip = datetime.fromisoformat(ev["start_utc"].replace("Z", "+00:00"))
        g = _game(ev, tip, tm, pw, rows, pb, ds, n, k_events)
        board["games"].append(g)
        if now >= tip - LOCK_BEFORE:
            at = now.astimezone(PT).isoformat(timespec="seconds")
            locks[ev["id"]] = {"at": at, "day": ds, "game": g}
            g["locked_at"] = at
    attach_readouts(board, DATA)
    keep_from = (day - timedelta(days=1)).isoformat()
    board["_locks"] = {k: v for k, v in locks.items() if v.get("day", "") >= keep_from}
    board["games"] = scoring.order_games(board["games"])
    ahead = [g for g in board["games"] if g.get("state") != "post"]
    tier = lambda g: (g.get("scoring") or {}).get("tier")  # noqa: E731
    board["other_games"], board["no_line_games"] = other, no_line
    board["summary"] = {"date": ds, "date_label": board["date_label"], "kind": kind, "games": len(board["games"]),
                        "qualified": sum(1 for g in ahead if tier(g) == "qualified"),
                        "watch": sum(1 for g in ahead if tier(g) == "watch"),
                        "would_qualify": sum(1 for g in ahead if (g.get("scoring") or {}).get("would_qualify") and tier(g) == "watch"),
                        "early": board["early"], "cutover": board["cutover"], "other_games": other, "built_label": built_label}
    board["league"] = {m: sorted(v["season"][m] for v in tm.values() if v["season"].get(m) is not None) for m in ("opp_efg", "def")}
    return board


def _game(ev, tip, tm, pw, rows, pb, ds, n, k_events) -> dict:
    o = ev["odds"]
    fav_side = "home" if o["favorite_id"] == ev["home"]["id"] else "away"
    sp = f"{o['spread']:g}"
    g = {"id": ev["id"], "tip_utc": tip.isoformat(), "tip_pt": tip.astimezone(PT).strftime("%-I:%M %p PT"),
         "day_et": tip.astimezone(ET).date().isoformat(), "state": ev["state"], "detail": ev.get("detail"),
         "neutral": ev["neutral"], "venue": ev.get("venue"), "favorite": fav_side,
         "spread": {"spread": o["spread"], "details": o.get("details"), "provider": o.get("provider") or "ESPN",
                    "ml_home": o.get("ml_home"), "ml_away": o.get("ml_away")}}
    for s in ("home", "away"):
        g[s] = _team(ev[s], tm, pw, rows, pb, ds, s == fav_side, f"-{sp}" if s == fav_side else f"+{sp}")
        if ev[s].get("ap") and not g[s]["power"].get("ap"):
            g[s]["power"]["ap"] = ev[s]["ap"]
    if ev["state"] != "pre":
        g["score"] = {"home": ev["home"]["score"], "away": ev["away"]["score"]}
    fav, dog = g[fav_side], g["away" if fav_side == "home" else "home"]
    g["scoring"] = scoring.score_game(g, fav, dog, ds, n)
    g["summary"], g["lean"] = summary_text(g)
    if k_events:
        names = lambda s: [ev[s].get("name"), ev[s].get("display"), (ev[s].get("display") or "").replace(ev[s].get("mascot") or "", "").strip()]  # noqa: E731
        a, h = {"abbr": ev["away"]["abbr"], "names": names("away")}, {"abbr": ev["home"]["abbr"], "names": names("home")}
        e = kalshi.match(k_events, tip.astimezone(ET).date(), a, h)
        if e:
            p = kalshi.prices(e, a, h)
            fp = p.get(fav_side) or {}
            g["kalshi"] = {"event": p["event"], "fav_prob": fp.get("prob"), "bid": fp.get("bid"), "ask": fp.get("ask")}
    return g


def attach_readouts(board: dict, data_dir: str):
    """Analyst readouts, written each morning by the owner's Claude scheduled task into
    <data_dir>/readouts/<Eastern date>.json. Attached to tracked games (Qualified + Watch list) only;
    information only, never changes a tier."""
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
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    return o


def context_row(g: dict, board: dict) -> dict:
    """What the checkpoint logger and the Live page need for one game."""
    sc = g["scoring"]
    fav, dog = (g["home"], g["away"]) if g["favorite"] == "home" else (g["away"], g["home"])
    fd = lambda w: {m: {"v": fav[w].get(m), "r": (fav[w].get("ranks") or {}).get(m)} for m in ("opp_efg", "def")}  # noqa: E731
    return {"id": g["id"], "tip_utc": g["tip_utc"], "tip_pt": g["tip_pt"], "day_et": g["day_et"],
            "away_abbr": g["away"]["abbr"], "home_abbr": g["home"]["abbr"], "fav_abbr": fav["abbr"], "dog_abbr": dog["abbr"],
            "fav_home": g["favorite"] == "home", "spread": -abs(g["spread"]["spread"]),
            "away_names": [g["away"].get("name"), g["away"].get("display")], "home_names": [g["home"].get("name"), g["home"].get("display")],
            "tier": sc["tier"], "tier_label": sc["tier_label"], "tracked": sc["tracked"], "alerts": sc["alerts"],
            "score": sc["score"], "fav_b2b": bool(fav.get("b2b")), "fav_defense": {"last3": fd("last3"), "season": fd("season")},
            "kalshi_event": (g.get("kalshi") or {}).get("event"), "locked": bool(g.get("locked_at"))}


def write(board: dict) -> dict:
    locks = board.pop("_locks", None)
    league = board.pop("league", {})
    board = _clean(board)
    os.makedirs(DOCS, exist_ok=True)
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html", "j2"]))
    page = env.get_template("cbb_board.html.j2").render(data_json=json.dumps(board).replace("</", "<\\/"), pages_url=PAGES_URL)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    summary = dict(board.get("summary") or {})
    summary["built_at"] = board["built_at"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    if locks is not None:
        with open(os.path.join(DATA, "locks.json"), "w") as f:
            json.dump(_clean(locks), f, indent=1)
    ctx = {"date": board["date"], "season": board["season"], "built_at": board["built_at"], "league": _clean(league),
           "games": [context_row(g, board) for g in board["games"] if g.get("scoring")]}
    for p in (os.path.join(DATA, "context.json"), os.path.join(DOCS, "context.json")):
        with open(p, "w") as f:
            json.dump(ctx, f, indent=1)
    os.makedirs(os.path.join(DATA, "boards"), exist_ok=True)
    with open(os.path.join(DATA, "boards", f"{board['date']}.json"), "w") as f:
        json.dump(board, f, indent=1)
    print(f"College basketball board written: {board['date']} ({board['kind']}), {len(board['games'])} games in range, "
          f"page {len(page) // 1024} KB")
    return summary
