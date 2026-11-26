"""Metrics, hero score, tiers and the readout line for one match (pure: data in, dict out).

Main tab (ATP/WTA/Challenger), 0-20, 5 points each:
  1. Elo edge        favorite's Elo minus the opponent's (surface blend 50/50 when the surface is known)
  2. Serve + return  2026 serve points won % + return points won % (tour + Challenger), favorite minus opponent
  3. Form            last two tournaments, wins weighted by opponent rank; under 4 matches = thin form (max 3)
  4. Fatigue         court time over the last 7 days (+60 min if played yesterday, +60 for a change of event):
                     3 + (opponent load - favorite load) / 90 min, 0-5
ITF tab, 0-10: Elo edge + form (form from Kalshi's settled matches; Tennis Abstract rarely covers ITF).
"""
from __future__ import annotations

from datetime import date, timedelta

from tennis import config as C
from tennis import sources as S

DEFAULT_MIN = 95      # minutes assumed for a match with no recorded time


def pts(v, table):
    if v is None:
        return 0
    for cut, p in table:
        if v >= cut:
            return p
    return 0


def tier_of(price: float):
    for key, label, lo, hi in C.TIERS:
        if lo - 1e-9 <= price < hi:
            return key, label
    return None, None


def band(score: int, tab: str) -> str:
    g, y = C.BANDS[tab]
    return "green" if score >= g else ("yellow" if score >= y else "red")


# ---- per-player pieces --------------------------------------------------------------------------
def player_info(name: str, elo: dict) -> dict:
    e = (elo.get("players") or {}).get(S.norm(name))
    if not e:
        return {"name": name, "rated": False, "elo_rank": None, "elo": None, "rank": None, "key": None}
    return {"name": name, "rated": True, **{k: e.get(k) for k in ("elo_rank", "elo", "hard", "clay", "grass", "rank", "key")}}


def comeback(p: dict) -> bool:
    r, mult, add = C.COMEBACK
    return bool(p.get("elo_rank") and p["elo_rank"] <= r and p.get("rank") and p["rank"] >= mult * p["elo_rank"] + add)


def surface_of(fav_ta: dict | None, day: str) -> str | None:
    """Surface of the favorite's current event, if Tennis Abstract already lists it (event started within 9 days)."""
    rec = (fav_ta or {}).get("recent") or []
    if rec and rec[0].get("date") and rec[0]["date"] >= (date.fromisoformat(day) - timedelta(days=9)).isoformat():
        return (rec[0].get("surface") or "").lower() or None
    return None


def elo_for(p: dict, surface: str | None, floor: float | None):
    if not p.get("rated"):
        return (floor - 50) if floor else None, True
    s = p.get(surface) if surface in ("hard", "clay", "grass") else None
    return (0.5 * p["elo"] + 0.5 * s) if s else p["elo"], False


def weight(rank) -> float:
    if rank is None:
        return 0.8
    for cut, w in C.FORM_WEIGHT:
        if rank <= cut:
            return w
    return 0.8


def form_from_ta(ta: dict | None) -> dict | None:
    rec = (ta or {}).get("recent") or []
    if not rec:
        return None
    events, out = [], []
    for r in rec:
        k = (r["date"], r["tourney"])
        if k not in events:
            if len(events) == 2:
                break
            events.append(k)
        out.append(r)
    return summarize_form([(r["won"], weight(r.get("opp_rank")), r["tourney"], r["round"]) for r in out], "Tennis Abstract")


def form_from_history(hist, name: str, day: str, elo: dict) -> dict | None:
    rows = hist.before(name, day)
    if not rows:
        return None
    events, use = [], []
    for r in rows:
        if r["t"] not in events:
            if len(events) == 2:
                break
            events.append(r["t"])
        use.append(r)
    items = []
    for r in use:
        opp = next((p for p in r["p"] if S.norm(p) != S.norm(name)), "")
        oe = (elo.get("players") or {}).get(S.norm(opp)) or {}
        items.append((S.norm(r["w"] or "") == S.norm(name), weight(oe.get("rank") or oe.get("elo_rank")), r["t"], r["r"]))
    return summarize_form(items, "Kalshi results")


def summarize_form(items, source) -> dict:
    n = len(items)
    w = sum(1 for x in items if x[0])
    tot = sum(x[1] for x in items)
    wpct = sum(x[1] for x in items if x[0]) / tot if tot else 0
    events = []
    for won, _, t, rnd in items:
        if t not in [e["t"] for e in events]:
            events.append({"t": t, "best": rnd, "won_last": won})
    return {"w": w, "l": n - w, "n": n, "weighted": round(wpct, 3), "thin": n < C.THIN_FORM, "source": source,
            "events": [f"{e['t']} ({'won ' if e['won_last'] and e['best'] in ('F', 'Final') else ''}{e['best']})" for e in events]}


def load_of(ta: dict | None, hist, name: str, day: str, tourney: str) -> dict:
    """Court time over the 7 days before `day`. Match days come from Kalshi; minutes from Tennis Abstract when it
    has the match (same opponent), else 95 minutes a match."""
    d0 = date.fromisoformat(day)
    since = (d0 - timedelta(days=7)).isoformat()
    played = [r for r in hist.before(name, day) if r["d"] >= since] if hist else []
    mins_by_opp = {}
    for r in (ta or {}).get("recent") or []:
        if r.get("minutes") and r.get("date") and r["date"] >= (d0 - timedelta(days=16)).isoformat():
            mins_by_opp[S.last_name(r.get("opp", ""))] = r["minutes"]
    minutes = 0
    for r in played:
        opp = next((p for p in r["p"] if S.norm(p) != S.norm(name)), "")
        minutes += mins_by_opp.get(S.last_name(opp), DEFAULT_MIN)
    yday = any(r["d"] == (d0 - timedelta(days=1)).isoformat() for r in played)
    recent3 = [r for r in played if r["d"] >= (d0 - timedelta(days=3)).isoformat()]
    travel = bool(recent3) and all(S.norm(r["t"]) != S.norm(tourney) for r in recent3)
    load = minutes + (60 if yday else 0) + (60 if travel else 0)
    return {"matches": len(played), "minutes": minutes, "yday": yday, "travel": travel, "load": load}


# ---- the match -----------------------------------------------------------------------------------
def score_match(m: dict, fav_i: int, ctx: dict) -> dict:
    """m = sources.match_from_event(...); ctx = {"elo": {"m":..., "w":...}, "ta": {key: parsed frags}, "hist": History,
    "previews": [urls]}."""
    tab, tour, gender = C.SERIES[m["series"]]
    fav_p, opp_p = m["players"][fav_i], m["players"][1 - fav_i]
    price = S.price_of(fav_p)
    opp_price = S.price_of(opp_p)
    day = (m.get("start") or "")[:10]
    elo = ctx["elo"].get(gender) or {}
    elos = [v["elo"] for v in (elo.get("players") or {}).values() if v.get("elo")]
    floor = min(elos) if elos else None
    fav, opp = player_info(fav_p["name"], elo), player_info(opp_p["name"], elo)
    ta = ctx.get("ta") or {}
    fta, ota = ta.get(fav.get("key")), ta.get(opp.get("key"))
    surface = surface_of(fta, day) if tab == "main" else None
    fe, fest = elo_for(fav, surface, floor)
    oe, oest = elo_for(opp, surface, floor)
    gap = round(fe - oe) if fe is not None and oe is not None else None
    metrics, notes = [], []
    if fav["rated"]:
        p_elo = pts(gap, C.ELO_PTS)
        enote = f"{'+' if gap >= 0 else ''}{gap} Elo{' (' + surface + ')' if surface else ''}" + (" · opponent unrated (estimated)" if oest else "")
    else:
        p_elo, enote = 0, "favorite has no Elo rating"
    metrics.append({"key": "elo", "label": "Elo edge", "fav": round(fe) if fe and not fest else None,
                    "opp": round(oe) if oe and not oest else None, "pts": p_elo, "max": 5, "note": enote})
    hist = ctx.get("hist")
    if tab == "main":
        fs, os_ = (fta or {}).get("season") or {}, (ota or {}).get("season") or {}
        if fs.get("sr") is not None and os_.get("sr") is not None:
            sgap = round(fs["sr"] - os_["sr"], 1)
            p_sr, snote = pts(sgap, C.SR_PTS), f"{fs['sr']:.1f} vs {os_['sr']:.1f} ({'+' if sgap >= 0 else ''}{sgap})"
        elif fs.get("sr") is not None:
            p_sr, snote = pts(fs["sr"], C.SR_ABS_PTS), f"{fs['sr']:.1f}, opponent has no 2026 stats"
        else:
            p_sr, snote = 0, "no 2026 serve/return stats for the favorite"
        metrics.append({"key": "sr", "label": "Serve + return", "fav": fs.get("sr"), "opp": os_.get("sr"), "pts": p_sr,
                        "max": 5, "note": snote + " · 2026 serve + return points won %"})
        ff = form_from_ta(fta) or (form_from_history(hist, fav["name"], day, elo) if hist else None)
        of = form_from_ta(ota) or (form_from_history(hist, opp["name"], day, elo) if hist else None)
    else:
        ff = form_from_history(hist, fav["name"], day, elo) if hist else None
        of = form_from_history(hist, opp["name"], day, elo) if hist else None
    p_form = min(pts(ff["weighted"], C.FORM_PTS), 3 if ff["thin"] else 5) if ff else 0
    metrics.append({"key": "form", "label": "Form (last 2 events)", "fav": f"{ff['w']}-{ff['l']}" if ff else None,
                    "opp": f"{of['w']}-{of['l']}" if of else None, "pts": p_form, "max": 5,
                    "note": (("; ".join(ff["events"]) + f" · weighted {ff['weighted']:.0%}" + (" · thin form" if ff["thin"] else ""))
                             if ff else "no recent results found")})
    fl = ol = None
    if tab == "main":
        fl = load_of(fta, hist, fav["name"], day, m["tourney"])
        ol = load_of(ota, hist, opp["name"], day, m["tourney"])
        p_fat = max(0, min(5, round(C.FATIGUE_BASE + (ol["load"] - fl["load"]) / 90)))
        metrics.append({"key": "fat", "label": "Fatigue (7 days)", "fav": fl["minutes"], "opp": ol["minutes"], "pts": p_fat,
                        "max": 5, "note": f"court time {fl['minutes']} vs {ol['minutes']} min"
                        + (" · fav played yesterday" if fl["yday"] else "") + (" · opp played yesterday" if ol["yday"] else "")
                        + (" · fav changed events" if fl["travel"] else "") + (" · opp changed events" if ol["travel"] else "")})
    score = sum(x["pts"] for x in metrics)
    tkey, tlabel = tier_of(price) if price else (None, None)
    fr, orr = fav.get("elo_rank"), opp.get("elo_rank")
    rank_match = bool(fr and fr <= C.TOP and (orr is None or orr > C.MID))
    section = "qualified" if (tkey == "t1000" and rank_match) else ("short" if tkey == "t2000" else "watch")
    flags = []
    for p, who in ((fav, "Favorite"), (opp, "Opponent")):
        if comeback(p):
            flags.append(f"{who} Elo well above ranking: #{p['elo_rank']} Elo vs #{p['rank']} official (injury comeback?)")
    if ff and ff["thin"]:
        flags.append(f"Thin form: favorite has {ff['n']} match{'es' if ff['n'] != 1 else ''} over the last two events")
    if not opp["rated"]:
        notes.append("Opponent not in the Elo ratings (estimated below the list)")
    links = {"preview": S.preview_link(ctx.get("previews") or [], fav["name"], opp["name"]),
             "search": "https://www.google.com/search?q=" + "+".join((fav["name"] + " vs " + opp["name"] + " prediction").split()),
             "fav_ta": C.PLAYER_URL[gender].format(key=fav["key"]) if fav.get("key") else None,
             "opp_ta": C.PLAYER_URL[gender].format(key=opp["key"]) if opp.get("key") else None}
    return {
        "event": m["event"], "series": m["series"], "tab": tab, "tour": tour, "tourney": m["tourney"], "round": m["round"],
        "start": m["start"], "surface": surface, "price": price, "ml": S.moneyline(price) if price else None,
        "opp_price": opp_price, "opp_ml": S.moneyline(opp_price) if opp_price else None,
        "tier": tkey, "tier_label": tlabel, "section": section, "rank_match": rank_match,
        "fav": {**fav, "form": ff, "load": fl, "sr": ((fta or {}).get("season") or {}).get("sr")},
        "opp": {**opp, "form": of, "load": ol, "sr": ((ota or {}).get("season") or {}).get("sr")},
        "metrics": metrics, "score": score, "max": C.MAX_SCORE[tab], "band": band(score, tab),
        "flags": flags, "notes": notes, "links": links, "readout": readout(fav, opp, metrics, ff, fl, ol),
    }


def readout(fav, opp, metrics, ff, fl, ol) -> str:
    by = {m["key"]: m for m in metrics}
    parts = []
    e = by.get("elo")
    if e and fav.get("rated"):
        parts.append(e["note"].split(" · ")[0])
    s = by.get("sr")
    if s and s["fav"] is not None:
        parts.append(f"serve+return {s['fav']:.1f}" + (f" vs {s['opp']:.1f}" if s["opp"] is not None else ""))
    if ff:
        parts.append(f"{ff['w']}-{ff['l']} over the last two events")
    if ol and ol["yday"]:
        parts.append("opponent played yesterday")
    if fl and fl["yday"]:
        parts.append("favorite played yesterday")
    return f"{S.last_name(fav['name']).title()}: " + ", ".join(parts) + "." if parts else ""
