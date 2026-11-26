"""Tennis pages: docs/tennis/index.html (board, two tabs), results.html, results.csv, summary.json (landing card)."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tennis import config as C
from tennis import ledger as L

ROOT = L.ROOT
DOCS = os.path.join(ROOT, "docs", "tennis")
BOARD = os.path.join(DOCS, "index.html")
PT = ZoneInfo("America/Los_Angeles")
CSV_COLS = ["start", "tab", "tour", "tourney", "round", "fav", "fav_elo_rank", "fav_rank", "opp", "opp_elo_rank", "opp_rank",
            "price", "ml", "tier_label", "section", "score", "max", "band", "logged_utc", "hours_before", "late",
            "status", "winner", "contracts", "cost", "pnl"]


def pt_label(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT")


def show(row: dict, status: str) -> dict:
    """Board card from a ledger row or a live scored match (both carry the same compact fields)."""
    r = dict(row)
    r["status"] = status
    r["start_pt"] = pt_label(r["start"]) if r.get("start") else ""
    return r


def band_of(r):
    s, mx = r["score"], r["max"]
    lo = {20: [(15, "15-20"), (10, "10-14"), (0, "0-9")], 10: [(8, "8-10"), (5, "5-7"), (0, "0-4")]}[mx]
    return next(lbl for cut, lbl in lo if s >= cut)


def breakdowns(rows: list[dict]) -> dict:
    """Stats for the Results page: by price tier, qualified, tour, hero band (per tab)."""
    out = {}
    for tab in C.TABS:
        tr = [r for r in rows if r["tab"] == tab]
        out[tab] = {
            "all": L.stats(tr),
            "tiers": [{"label": lbl, **L.stats([r for r in tr if r["tier"] == key])} for key, lbl, *_ in C.TIERS],
            "qualified": L.stats([r for r in tr if r["section"] == "qualified"]),
            "rank_match": L.stats([r for r in tr if r.get("rank_match")]),
            "tours": [{"label": t, **L.stats([r for r in tr if r["tour"] == t])} for t in sorted({r["tour"] for r in tr})],
            "bands": [{"label": b, **L.stats([r for r in tr if band_of(r) == b])}
                      for b in (["15-20", "10-14", "0-9"] if tab == "main" else ["8-10", "5-7", "0-4"])],
        }
    return out


def publish(led: dict, scored: list[dict], elo: dict, now: datetime):
    os.makedirs(DOCS, exist_ok=True)
    rows = led["matches"]
    by_event = {r["event"]: r for r in rows}
    cards = []
    seen = set()
    for sc in sorted(scored, key=lambda s: s["start"]):
        live = L.entry(sc, now, late=False)
        live.update({"hours": sc.get("hours"), "notes": sc.get("notes", []),
                     "fav_form_events": (sc["fav"].get("form") or {}).get("events", []),
                     "opp_form_events": (sc["opp"].get("form") or {}).get("events", [])})
        if sc["event"] in by_event:
            lg = by_event[sc["event"]]
            live.update({"logged_ml": lg["ml"], "logged_label": pt_label(lg["logged_utc"]), "late": lg["late"],
                         "section": lg["section"], "score": lg["score"], "band": lg["band"]})
        cards.append(show(live, "logged" if sc["event"] in by_event else "upcoming"))
        seen.add(sc["event"])
    for r in rows:            # logged matches no longer on the open list (under way, or price moved below -800)
        if r["status"] == "open" and r["event"] not in seen:
            cards.append(show({**r, "logged_ml": r["ml"], "logged_label": pt_label(r["logged_utc"])}, "logged"))
    recent_cut = (now - timedelta(hours=48)).isoformat()
    recent = [show(r, r["status"]) for r in rows if r["status"] in ("won", "lost", "void") and (r["graded_utc"] or "") >= recent_cut]
    recent.sort(key=lambda r: r["start"], reverse=True)
    data = {"built_label": now.astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT"), "tabs": C.TABS,
            "cards": cards, "recent": recent[:40], "max": C.MAX_SCORE, "bands": C.BANDS,
            "elo_updated": {g: (elo.get(g) or {}).get("updated") for g in ("m", "w")},
            "stats": {tab: L.stats([r for r in rows if r["tab"] == tab]) for tab in C.TABS}}
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html", "j2"]))
    with open(BOARD, "w") as f:
        f.write(env.get_template("tennis_board.html.j2").render(data_json=json.dumps(data).replace("</", "<\\/"),
                                                                pages_url=C.SITE + "/"))
    led_rows = sorted(rows, key=lambda r: r["start"], reverse=True)
    for r in led_rows:
        r["start_pt"] = pt_label(r["start"])
        r["hero_band"] = band_of(r)
    with open(os.path.join(DOCS, "results.html"), "w") as f:
        f.write(env.get_template("tennis_results.html.j2").render(
            built=data["built_label"], tabs=C.TABS, b=breakdowns(rows), rows=led_rows, pages_url=C.SITE + "/"))
    for r in led_rows:
        r.pop("start_pt", None)
        r.pop("hero_band", None)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["start"]):
        w.writerow({**r, "fav": r["fav"]["name"], "fav_elo_rank": r["fav"].get("elo_rank"), "fav_rank": r["fav"].get("rank"),
                    "opp": r["opp"]["name"], "opp_elo_rank": r["opp"].get("elo_rank"), "opp_rank": r["opp"].get("rank")})
    with open(os.path.join(DOCS, "results.csv"), "w") as f:
        f.write(buf.getvalue())
    main = [c for c in cards if c["tab"] == "main"]
    itf = [c for c in cards if c["tab"] == "itf"]
    st = data["stats"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump({"built_label": data["built_label"], "main": len(main), "itf": len(itf),
                   "qualified": sum(c["section"] == "qualified" for c in cards),
                   "won": st["main"]["won"] + st["itf"]["won"], "lost": st["main"]["lost"] + st["itf"]["lost"],
                   "open": st["main"]["open"] + st["itf"]["open"]}, f, indent=1)
