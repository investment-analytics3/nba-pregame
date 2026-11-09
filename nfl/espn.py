"""ESPN public JSON for the NFL: week scoreboard, DraftKings lines (open + current),
game-day injuries, FPI matchup projection, live status. Parsers written against real
responses captured Nov 8, 2026 (dev-nfl-samples branch).
"""
from __future__ import annotations

from nba.http import get

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"


def scoreboard_week(season_type: int, week: int) -> dict:
    return get(f"{SITE}/scoreboard?seasontype={season_type}&week={week}") or {}


def scoreboard_date(ymd: str) -> dict:
    return get(f"{SITE}/scoreboard?dates={ymd}") or {}


def summary(event_id: str) -> dict:
    return get(f"{SITE}/summary?event={event_id}") or {}


def _num(s):
    try:
        return float(str(s).replace("+", "").replace("o", "").replace("u", ""))
    except (TypeError, ValueError):
        return None


def parse_odds(comp: dict) -> dict:
    """Current and opening lines from the DraftKings entry (ESPN's first provider)."""
    for o in comp.get("odds") or comp.get("pickcenter") or []:
        if o.get("spread") is None and not o.get("details"):
            continue
        ps, tot, ml = o.get("pointSpread") or {}, o.get("total") or {}, o.get("moneyline") or {}
        line = {}
        for side in ("home", "away"):
            s = ps.get(side) or {}
            line[side] = {"cur": _num((s.get("close") or {}).get("line")), "open": _num((s.get("open") or {}).get("line")),
                          "ml": _num((ml.get(side) or {}).get("close", {}).get("odds")),
                          "ml_open": _num((ml.get(side) or {}).get("open", {}).get("odds"))}
        ht, at = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        fav = "home" if ht.get("favorite") else ("away" if at.get("favorite") else None)
        if fav is None and line["home"]["cur"] is not None:
            fav = "home" if line["home"]["cur"] < 0 else ("away" if line["home"]["cur"] > 0 else None)
        if fav is None and line["home"]["ml"] and line["away"]["ml"]:      # pick'em: moneyline decides
            fav = "home" if line["home"]["ml"] < line["away"]["ml"] else "away"
        over = tot.get("over") or {}
        return {"provider": (o.get("provider") or {}).get("name"), "details": o.get("details") or "",
                "favorite": fav, "spread": abs(float(o["spread"])) if o.get("spread") is not None else None,
                "lines": line, "total": o.get("overUnder"),
                "total_open": _num((over.get("open") or {}).get("line")),
                "fav_open_side": "home" if ht.get("favoriteAtOpen") else ("away" if at.get("favoriteAtOpen") else None)}
    return {}


def parse_events(sb: dict) -> list[dict]:
    out = []
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        side = {}
        for c in comp.get("competitors", []):
            recs = {r.get("type") or r.get("name"): r.get("summary") for r in c.get("records", [])}
            side[c["homeAway"]] = {"id": c["team"]["id"], "abbr": c["team"]["abbreviation"],
                                   "name": c["team"].get("shortDisplayName") or c["team"].get("name"),
                                   "display": c["team"].get("displayName"),
                                   "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None,
                                   "record": recs.get("total") or recs.get("overall")}
        if "home" not in side or "away" not in side:
            continue
        st = (ev.get("status") or {})
        out.append({"id": ev["id"], "start_utc": ev.get("date"), "short": ev.get("shortName"),
                    "state": st.get("type", {}).get("state"), "status": st.get("type", {}).get("name"),
                    "detail": st.get("type", {}).get("shortDetail"), "period": st.get("period"),
                    "clock": st.get("displayClock"), "completed": bool(st.get("type", {}).get("completed")),
                    "neutral": bool(comp.get("neutralSite")), "venue": (comp.get("venue") or {}).get("fullName"),
                    "home": side["home"], "away": side["away"], "odds": parse_odds(comp)})
    return out


def parse_injuries(summ: dict) -> dict:
    """{espn_team_id: [{"name", "pos", "status"}]} from a game summary (empty list = none listed)."""
    out = {}
    for t in summ.get("injuries") or []:
        tid = str((t.get("team") or {}).get("id"))
        out[tid] = [{"name": (i.get("athlete") or {}).get("displayName"),
                     "pos": ((i.get("athlete") or {}).get("position") or {}).get("abbreviation"),
                     "status": i.get("status")} for i in t.get("injuries") or []]
    return out


def parse_predictor(summ: dict) -> dict:
    p = summ.get("predictor") or {}
    try:
        return {"home": float(p["homeTeam"]["gameProjection"]) / 100, "away": float(p["awayTeam"]["gameProjection"]) / 100}
    except (KeyError, TypeError, ValueError):
        return {}
