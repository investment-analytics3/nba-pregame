"""Results pages for the NFL, college football, college basketball and NHL boards (owner Nov 24: mirror the NBA page).

Every game that appeared on a board with a favorite is graded from the LAST saved board that held it
(data/<sport>/boards/*.json: tiers lock before tip, so that is the tier going in). Scores come from the board when the
game shows final there; otherwise (the last games of a night are often still live when the board moves on) from the
ESPN scoreboard for that date, fetched by the build workflow. Graded games are kept in data/<sport>/results.json
(frozen once final), exported to docs/<sport>/results.csv (two rows per game) and shown on docs/<sport>/results.html.

    python -m sportres <sport> [--no-fetch]      # sport = nfl | cfb | cbb | nhl
"""
from __future__ import annotations

import csv
import glob
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"
PT = ZoneInfo("America/Los_Angeles")

f_epa = lambda v: f"{v:+.3f}"            # noqa: E731
f_pct1 = lambda v: f"{100 * v:.1f}%"     # noqa: E731  (success rate stored 0-1)
f_p = lambda v: f"{v:.1f}%"              # noqa: E731  (already a percent)
f_1 = lambda v: f"{v:.1f}"               # noqa: E731
f_2 = lambda v: f"{v:.2f}"               # noqa: E731
f_s2 = lambda v: f"{v:+.2f}"             # noqa: E731

FOOT_METRICS = [("off_epa", "Off EPA", f_epa), ("off_sr", "Off success", f_pct1),
                ("def_epa", "Def EPA", f_epa), ("def_sr", "Def success", f_pct1)]

# Colors match each board: favorite green <= g (deep green <= elite on football), yellow <= y, else red;
# the underdog is flipped (deep red = elite opponent). PR thresholds = the board's PR pill.
CONFIG = {
    "nfl": dict(name="NFL", title="NFL Results", board="nfl/", weekly=True, window="last3", window_label="Last 3 games",
                metrics=FOOT_METRICS, g=10, y=22, elite=5, pr=(10, 22), form_label="Last 2", rest_label="Rest",
                cover_label="Spread covered", line_label="Spread", flag_label="Matchup",
                buckets=[["s", "Under 3.5", 0, 3.49], ["m", "3.5 to 6.5", 3.5, 6.99], ["l", "7 or more", 7, 99]]),
    "cfb": dict(name="College football", title="College Football Results", board="cfb/", weekly=True, window="last3",
                window_label="Last 3 games", metrics=FOOT_METRICS, g=25, y=113, elite=10, pr=(25, 113), form_label="Last 2",
                rest_label="Rest", cover_label="Spread covered", line_label="Spread", flag_label="Top-10 D (L3)",
                buckets=[["s", "10 to 13.5", 0, 13.99], ["m", "14 to 16.5", 14, 16.99], ["l", "17 to 20", 17, 99]]),
    "cbb": dict(name="College basketball", title="College Basketball Results", board="cbb/", weekly=False, window="last3",
                window_label="Last 3 games",
                metrics=[("off", "Off Eff", f_1), ("efg", "eFG%", f_p), ("def", "Def Eff", f_1), ("opp_efg", "Opp eFG%", f_p)],
                g=50, y=150, elite=20, pr=(50, 150), form_label="Last 3", rest_label="B2B", cover_label="Spread covered",
                line_label="Spread", flag_label="Matchup",
                buckets=[["s", "10 to 13.5", 0, 13.99], ["m", "14 to 16.5", 14, 16.99], ["l", "17 to 20", 17, 99]]),
    "nhl": dict(name="NHL", title="NHL Results", board="nhl/", weekly=False, window="last5", window_label="Last 5 games",
                metrics=[("xgf60", "xGF/60", f_2), ("xga60", "xGA/60", f_2), ("xgf", "xGF%", f_p), ("gsax", "GSAx", f_s2),
                         ("st", "PP%+PK%", f_1)],
                g=10, y=22, elite=5, pr=(10, 22), form_label="Last 3", rest_label="B2B", cover_label="Puck line (-1.5)",
                line_label="Moneyline", flag_label="Matchup", goalie=True,
                buckets=[["s", "Under 60%", 0, 0.5999], ["m", "60% to 70%", 0.6, 0.6999], ["l", "70% or more", 0.7, 1]]),
}


def color(rank, role: str, c: dict) -> str:
    if rank is None:
        return "none"
    if role == "Favorite":
        return "green" if rank <= c["g"] else ("yellow" if rank <= c["y"] else "red")
    if rank <= c["elite"]:
        return "deep-red"
    return "red" if rank <= c["g"] else ("yellow" if rank <= c["y"] else "green")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------- one board game -> one result row (teams going in) ----------

def _teams(sport: str, g: dict):
    if sport in ("nfl", "cfb"):
        if not g.get("fav"):
            return None
        fav_home = g["home"].get("code") == g["fav"]
    else:
        if g.get("favorite") not in ("home", "away"):
            return None
        fav_home = g["favorite"] == "home"
    return (g["home"], g["away"], "home") if fav_home else (g["away"], g["home"], "away")


def _team_info(sport: str, t: dict, cfg: dict, role: str) -> dict:
    if sport in ("nfl", "cfb"):
        rest = t.get("rest")
        info = {"record": t.get("record") or "", "form": "".join(t.get("last2_results") or []),
                "pr": (t.get("power") or {}).get("consensus"), "rest": f"{rest} days" if rest is not None else "",
                "rest_days": rest}
    else:
        info = {"record": t.get("record") or "", "form": t.get("l3_record") or "",
                "pr": (t.get("power") or {}).get("rank"), "rest": "Yes" if t.get("b2b") else "No"}
    if cfg.get("goalie"):
        st = t.get("goalie_status")
        gl = t.get("goalie")
        info["goalie"] = (gl.get("name") if isinstance(gl, dict) else gl) or ""
        info["goalie_role"] = {"regular": "Starter", "backup": "Backup"}.get(st, "Not posted")
    return info


def _metrics(t: dict, cfg: dict, role: str) -> dict:
    out = {}
    for w in ("season", cfg["window"]):
        blk = t.get(w) or {}
        ranks = blk.get("ranks") or {}
        out[w] = {}
        for m, _, fmt in cfg["metrics"]:
            v, r = _num(blk.get(m)), ranks.get(m)
            out[w][m] = {"v": fmt(v) if v is not None else None, "r": r, "c": color(r, role, cfg)}
    return out


def row(sport: str, g: dict, period: str, period_label: str) -> dict | None:
    cfg = CONFIG[sport]
    tm = _teams(sport, g)
    sc = g.get("scoring") or {}
    if not tm or not sc:
        return None
    fav, dog, site = tm
    if g.get("neutral"):
        site = "neutral"
    hero = sc.get("hero") or {}
    score = sc.get("score") if isinstance(sc.get("score"), (int, float)) else hero.get("score")
    band = sc.get("band") or hero.get("band") or ""
    # the game line
    if sport == "nhl":
        ln = g.get("line") or {}
        ml = ln.get("ml_home") if site == "home" else ln.get("ml_away")
        if ml is None:
            ml = ln.get("ml_home") if ln.get("ml_home") is not None and ln.get("ml_home") < 0 else ln.get("ml_away")
        line, line_num = (f"{ml:+d}" if isinstance(ml, int) else (ln.get("details") or "")), ln.get("fav_price")
    else:
        sp = g.get("spread")
        sp = sp.get("spread") if isinstance(sp, dict) else sp
        line_num = abs(float(sp)) if sp is not None else None
        line = (f"-{line_num:g}" if line_num else "PK") if line_num is not None else ""
    # information-only flag
    if sport == "cfb":
        r = ((fav.get("last3") or {}).get("ranks") or {}).get("def_epa")
        flag = "Yes" if r is not None and r <= 10 else "No"
    else:
        flag = "Yes" if any(m.get("met") for m in sc.get("matchups") or []) else "No"
    rec = {"id": str(g["id"]), "period": period, "period_label": period_label, "date": g.get("day_et") or "",
           "start_utc": g.get("kick_utc") or g.get("tip_utc") or "",
           "favorite": fav.get("abbr") or "", "opponent": dog.get("abbr") or "", "fav_site": site,
           "fav_side": "home" if fav is g["home"] else "away",
           "line": line, "line_num": line_num, "tier": sc.get("tier") or "out",
           "tier_label": sc.get("tier_label") or "", "route": sc.get("route") or "", "score": score, "band": band,
           "flag": flag, "fav_team": _team_info(sport, fav, cfg, "Favorite"), "opp_team": _team_info(sport, dog, cfg, "Opponent"),
           "fav_metrics": _metrics(fav, cfg, "Favorite"), "opp_metrics": _metrics(dog, cfg, "Opponent"),
           "live_page": f"{cfg['board']}game.html?g={g['id']}"}
    s = g.get("score") or {}
    if g.get("state") == "post" and s.get("home") is not None and s.get("away") is not None:
        finish(rec, int(s["home"]), int(s["away"]), sport)
    return rec


def finish(rec: dict, home: int, away: int, sport: str):
    fp, op = (home, away) if rec["fav_side"] == "home" else (away, home)
    margin = fp - op
    rec.update(fav_pts=fp, opp_pts=op, final=True, winner=rec["favorite"] if margin > 0 else rec["opponent"],
               fav_won="Yes" if margin > 0 else ("Tie" if margin == 0 else "No"))
    if sport == "nhl":
        rec["covered"] = "Yes" if margin >= 2 else "No"
    elif rec.get("line_num") is None:
        rec["covered"] = ""
    else:
        rec["covered"] = "Yes" if margin > rec["line_num"] else ("Push" if margin == rec["line_num"] else "No")


# ---------- collect, finals, save ----------

def collect(sport: str) -> dict:
    """{game id: row} from every saved board; a later board replaces an earlier one, but never a final with a non-final."""
    cfg = CONFIG[sport]
    out = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", sport, "boards", "*.json"))):
        try:
            b = json.load(open(f))
        except Exception:
            continue
        if cfg["weekly"]:
            period, label = f"{b.get('season')}-w{int(b.get('week') or 0):02d}", f"Week {b.get('week')}"
        for g in b.get("games") or []:
            if not cfg["weekly"]:
                period = g.get("day_et") or b.get("date") or ""
                label = period
            r = row(sport, g, period, label)
            if not r:
                continue
            old = out.get(r["id"])
            if old and old.get("final") and not r.get("final"):
                continue
            out[r["id"]] = r
    return out


def fetch_finals(sport: str, pending: list[dict]) -> int:
    """Fill scores for games the boards never saw finish, from ESPN's scoreboard for their date (workflow only)."""
    import importlib
    espn = importlib.import_module(f"{sport}.espn")
    get_sb = getattr(espn, "scoreboard_date", None) or getattr(espn, "scoreboard")
    now = datetime.now(timezone.utc)
    by_day = {}
    for r in pending:
        try:
            start = datetime.fromisoformat(r["start_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        if now - start < timedelta(hours=3):
            continue
        day = start.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(r)
    n = 0
    for day, rows in by_day.items():
        try:
            sb = get_sb(day) or {}
        except Exception as e:
            print(f"! {sport} scoreboard {day}: {e}")
            continue
        scores = {}
        for ev in sb.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            if ((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("state") != "post":
                continue
            side = {c.get("homeAway"): _num(c.get("score")) for c in comp.get("competitors", [])}
            if side.get("home") is not None and side.get("away") is not None:
                scores[str(ev.get("id"))] = (int(side["home"]), int(side["away"]))
        for r in rows:
            if r["id"] in scores:
                finish(r, *scores[r["id"]], sport)
                n += 1
    return n


def update(sport: str, fetch: bool = True) -> str:
    cfg = CONFIG[sport]
    path = os.path.join(ROOT, "data", sport, "results.json")
    saved = {}
    if os.path.exists(path):
        try:
            saved = {r["id"]: r for r in json.load(open(path)).get("games", [])}
        except Exception:
            saved = {}
    rows = collect(sport)
    for gid, r in saved.items():          # a graded game is frozen; keep games whose board file is gone
        if r.get("final") or gid not in rows:
            rows[gid] = r
    pending = [r for r in rows.values() if not r.get("final")]
    got = fetch_finals(sport, pending) if (fetch and pending) else 0
    games = sorted(rows.values(), key=lambda r: (r["date"], r["start_utc"], r["id"]), reverse=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"sport": sport, "games": games}, f, indent=1)
    graded = [r for r in games if r.get("final")]
    docs = os.path.join(ROOT, "docs", sport)
    os.makedirs(docs, exist_ok=True)
    export_csv(sport, graded, os.path.join(docs, "results.csv"))
    write_page(sport, graded)
    q = [r for r in graded if r["tier"] == "qualified"]
    qw = sum(r["fav_won"] == "Yes" for r in q)
    return (f"{cfg['name']} results: {len(graded)} graded ({got} finals from ESPN, "
            f"{len(games) - len(graded)} pending). Qualified favorites {qw}-{len(q) - qw}.")


def export_csv(sport: str, games: list[dict], path: str):
    cfg = CONFIG[sport]
    mcols = [(w, m) for w in ("season", cfg["window"]) for m, _, _ in cfg["metrics"]]
    cols = ["date", "period", "game_id", "role", "team", "record", "form", "power_rank", "rest"]
    if cfg.get("goalie"):
        cols += ["goalie", "goalie_role"]
    cols += ["favorite", "opponent", "fav_site", "line", "fav_pts", "opp_pts", "winner", "fav_won", "covered", "tier",
             "analysis_score", "analysis_color", "flag"]
    for w, m in mcols:
        cols += [f"{w}_{m}", f"{w}_{m}_rank", f"{w}_{m}_color"]
    cols += ["game_page"]
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for g in games:
            for role, tk, mk, team in (("Favorite", "fav_team", "fav_metrics", g["favorite"]),
                                       ("Opponent", "opp_team", "opp_metrics", g["opponent"])):
                t = g.get(tk) or {}
                rw = {"date": g["date"], "period": g["period_label"], "game_id": g["id"], "role": role, "team": team,
                      "record": t.get("record"), "form": t.get("form"), "power_rank": t.get("pr"), "rest": t.get("rest"),
                      "favorite": g["favorite"], "opponent": g["opponent"], "fav_site": g["fav_site"], "line": g["line"],
                      "fav_pts": g.get("fav_pts"), "opp_pts": g.get("opp_pts"), "winner": g.get("winner"),
                      "fav_won": g.get("fav_won"), "covered": g.get("covered"), "tier": g["tier_label"] or g["tier"],
                      "analysis_score": g.get("score"), "analysis_color": g.get("band"), "flag": g.get("flag"),
                      "game_page": PAGES_URL + g["live_page"]}
                if cfg.get("goalie"):
                    rw.update(goalie=t.get("goalie"), goalie_role=t.get("goalie_role"))
                for w, m in mcols:
                    c = ((g.get(mk) or {}).get(w) or {}).get(m) or {}
                    rw.update({f"{w}_{m}": c.get("v"), f"{w}_{m}_rank": c.get("r"), f"{w}_{m}_color": c.get("c")})
                wr.writerow(rw)


def write_page(sport: str, games: list[dict]):
    import jinja2
    cfg = CONFIG[sport]
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=True)
    page_cfg = {k: v for k, v in cfg.items() if k != "metrics"}
    page_cfg["metrics"] = [[m, label] for m, label, _ in cfg["metrics"]]
    page_cfg["sport"] = sport
    data = {"games": games, "cfg": page_cfg, "updated": datetime.now(PT).strftime("%b %-d, %-I:%M %p PT")}
    html = env.get_template("sport_results.html.j2").render(
        cfg=page_cfg, updated=data["updated"], data_json=json.dumps(data).replace("</", "<\\/"), pages_url=PAGES_URL)
    with open(os.path.join(ROOT, "docs", sport, "results.html"), "w") as f:
        f.write(html)
