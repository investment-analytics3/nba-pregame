"""Turn a scored slate into the board: page data, written summaries, change list.

No AI is used: every sentence is assembled from the numbers by these rules.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import scoring, store

PT = ZoneInfo("America/Los_Angeles")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")
PAGES_URL = "https://investment-analytics3.github.io/nba-pregame/"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fav_dog(game: dict):
    side = game.get("favorite")
    if side not in ("home", "away"):
        return None, None
    return game[side], game["home" if side == "away" else "away"]


def spread_text(game: dict, team: dict) -> str:
    sp = game.get("spread") or {}
    if sp.get("spread") is None:
        return ""
    if sp["spread"] == 0:
        return "PK"
    fav, _ = _fav_dog(game)
    v = f"{sp['spread']:g}"
    return f"-{v}" if fav is team else f"+{v}"


def top_scorers(player_rows: list[dict], team_id: str, out_names: set[str], n: int = 2) -> list[dict]:
    agg = defaultdict(lambda: {"pts": 0, "games": 0, "name": ""})
    for r in player_rows:
        if r["team_id"] != team_id or r["dnp"] or r["minutes"] <= 0:
            continue
        a = agg[r["player_id"]]
        a["name"] = r["player"]
        a["pts"] += r["pts"]
        a["games"] += 1
    best = sorted((a for a in agg.values() if a["games"] >= 3 and a["name"] not in out_names),
                  key=lambda a: -a["pts"] / a["games"])[:n]
    return [{"name": a["name"], "ppg": round(a["pts"] / a["games"], 1)} for a in best]


# --------------------------------------------------------------------------- #
# Written summary (rule-based)
# --------------------------------------------------------------------------- #
def summary_text(game: dict) -> tuple[str, str]:
    sc = game["scoring"]
    if not sc.get("scored"):
        return "No spread has been posted yet, so there is no favorite to score.", "No lean"
    fav, dog = _fav_dog(game)
    parts = []
    if sc["qualified"]:
        parts.append(f"{fav['name']} qualify on {', '.join(sc['qualifiers'])}: top 10 for the season "
                     f"and over their last 3.")
    else:
        parts.append(f"No track: {sc['fails'][0][0].lower() + sc['fails'][0][1:]}.")
    # biggest last-3 mismatch among the four matchup columns
    best = None
    for fm, dm in scoring.MATCHUPS:
        fr = ((fav.get("last3") or {}).get("ranks") or {}).get(fm)
        dr = ((dog.get("last3") or {}).get("ranks") or {}).get(dm)
        if fr and dr and (best is None or dr - fr > best[0]):
            best = (dr - fr, fm, fr, dm, dr)
    if best and best[0] >= 8:
        _, fm, fr, dm, dr = best
        parts.append(f"Biggest edge over the last 3: {fav['abbr']} {scoring.NAMES[fm]} (#{fr}) against "
                     f"{dog['abbr']} {scoring.NAMES[dm]} (#{dr}).")
    elif best and best[0] <= -8:
        _, fm, fr, dm, dr = best
        parts.append(f"Watch the {scoring.NAMES[fm]} matchup: {dog['abbr']} (#{dr}) has the better of "
                     f"{fav['abbr']} (#{fr}) lately.")
    form = f"{fav['abbr']} {fav.get('l3_record') or '?'} in its last 3, {dog['abbr']} {dog.get('l3_record') or '?'}"
    rest = [t["abbr"] for t in (fav, dog) if t.get("b2b")]
    if rest:
        form += f"; {' and '.join(rest)} on a back-to-back"
    parts.append(form + ".")
    for t in (fav, dog):
        if t.get("key_out"):
            parts.append(f"{t['abbr']} will be without {', '.join(t['key_out'])}".rstrip(".") + ".")
    if fav.get("key_questionable"):
        parts.append(f"{fav['abbr']} is waiting on {', '.join(fav['key_questionable'])} (questionable)".rstrip(".") + ".")
    if sc["qualified"]:
        lean = f"Lean: {fav['abbr']}"
    elif sc["score"] >= 18:
        lean = f"Lean: {fav['abbr']} (not tracked)"
    else:
        lean = "No lean"
    return " ".join(parts), lean


# --------------------------------------------------------------------------- #
# Snapshots and changes (for the "Final" email)
# --------------------------------------------------------------------------- #
def team_metrics(t: dict | None) -> dict:
    """Value and rank of the four metrics, season and last 3, as shown on the board."""
    out = {}
    for w in ("season", "last3"):
        blk = (t or {}).get(w) or {}
        out[w] = {m: {"v": blk.get(m), "r": (blk.get("ranks") or {}).get(m)} for m in scoring.METRICS}
    return out


def team_info(t: dict | None) -> dict:
    t = t or {}
    p = t.get("power") or {}
    return {"record": t.get("record"), "l3": t.get("l3_record"), "b2b": bool(t.get("b2b")),
            "pr": p.get("rank"), "pr_split": bool(p.get("split"))}


def snapshot(slate: dict) -> dict:
    snap = {}
    for g in slate["games"]:
        sc = g["scoring"]
        fav, dog = _fav_dog(g)
        snap[g["id"]] = {
            "label": f"{g['away']['abbr']} @ {g['home']['abbr']}",
            "fav": sc.get("fav"), "dog": sc.get("dog"), "fav_side": g.get("favorite"),
            "spread_num": (g.get("spread") or {}).get("spread"), "tip_utc": g.get("tip_utc"),
            "neutral": g.get("neutral", False),
            "qualified": sc.get("qualified"), "score": sc.get("score"), "band": sc.get("band"),
            "qualifiers": sc.get("qualifiers", []), "fails": sc.get("fails", []),
            "fav_metrics": team_metrics(fav), "dog_metrics": team_metrics(dog),
            "fav_team": team_info(fav), "dog_team": team_info(dog),
            "spread": (g.get("spread") or {}).get("details"),
            "key_out": sorted((fav or {}).get("key_out", []) + (dog or {}).get("key_out", [])),
            "key_q": sorted((fav or {}).get("key_questionable", []) + (dog or {}).get("key_questionable", [])),
        }
    return snap


def changes(before: dict, after: dict) -> list[str]:
    out = []
    for gid, a in after.items():
        b = before.get(gid)
        if not b:
            out.append(f"{a['label']}: new on the board")
            continue
        if b["qualified"] != a["qualified"]:
            if a["qualified"]:
                out.append(f"{a['label']}: now QUALIFIED ({a['fav']})")
            else:
                out.append(f"{a['label']}: moved to No track ({a['fails'][0] if a['fails'] else 'see board'})")
        if b["score"] != a["score"] and a["score"] is not None and b["score"] is not None:
            out.append(f"{a['label']}: score {b['score']} → {a['score']}")
        if b["spread"] != a["spread"]:
            out.append(f"{a['label']}: line {b['spread'] or 'none'} → {a['spread'] or 'none'}")
        newly_out = set(a["key_out"]) - set(b["key_out"])
        if newly_out:
            out.append(f"{a['label']}: ruled out: {', '.join(sorted(newly_out))}")
        back = set(b["key_out"]) - set(a["key_out"])
        if back:
            out.append(f"{a['label']}: no longer out: {', '.join(sorted(back))}")
        newly_q = set(a["key_q"]) - set(b["key_q"])
        if newly_q:
            out.append(f"{a['label']}: now questionable: {', '.join(sorted(newly_q))}")
    return out


# --------------------------------------------------------------------------- #
# Page data
# --------------------------------------------------------------------------- #
def page_data(slate: dict, kind: str, change_list: list[str] | None, earlier_label: str | None) -> dict:
    players = [p for p in store.load_player_games() if p["game_date"] < slate["date"]]
    closers = store.read_json("closers.json", {}) or {}
    games = []
    for g in slate["games"]:
        sc = g["scoring"]
        fav, dog = _fav_dog(g)
        text, lean = summary_text(g)
        for t in (g["away"], g["home"]):
            t["spread_text"] = spread_text(g, t)
            t["is_fav"] = t is fav
            out = {i["player"] for i in t.get("injuries", []) if i.get("counts_as_out")}
            t["top_scorers"] = top_scorers(players, t["id"], out)
            t["closers"] = closers.get(t["abbr"], [])
        games.append({**g, "summary": text, "lean": lean})
    built = datetime.fromisoformat(slate["built_at"])
    return {
        "date": slate["date"], "date_label": datetime.fromisoformat(slate["date"]).strftime("%A, %b %-d"),
        "kind": kind, "built_label": built.strftime("%-I:%M %p PT"),
        "metrics_through": slate.get("metrics_through"),
        "metrics_through_label": (datetime.fromisoformat(slate["metrics_through"]).strftime("%b %-d")
                                  if slate.get("metrics_through") else None),
        "injury_source": slate.get("injury_source"), "injury_report_time": slate.get("injury_report_time"),
        "changes": change_list, "earlier_label": earlier_label,
        "games": games, "max": scoring.MAX_SCORE,
    }


def _env():
    return Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


def render_page(data: dict) -> str:
    data_json = json.dumps(data).replace("</", "<\\/")  # safe inside <script>
    return _env().get_template("board.html.j2").render(data=data, data_json=data_json, pages_url=PAGES_URL)


def render_email(data: dict) -> str:
    return _env().get_template("email.html.j2").render(d=data, pages_url=PAGES_URL)


def alert_text(data: dict) -> tuple[str, str]:
    q = [g for g in data["games"] if g["scoring"].get("qualified")]
    n = len(data["games"]) - len(q)
    title = f"NBA Pregame Board{' - Final' if data['kind'] == 'final' else ''} - {data['date_label']}"
    if q:
        body = f"{len(q)} qualified: " + ", ".join(f"{g['scoring']['fav']} {g['scoring']['score']}" for g in q)
    else:
        body = "No games qualified"
    body += f" | {n} no track"
    if data.get("changes"):
        body += f" | {len(data['changes'])} change(s) since {data['earlier_label']}"
    elif data["kind"] == "final" and data.get("earlier_label"):
        body += f" | no changes since {data['earlier_label']}"
    return title, body
