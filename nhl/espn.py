"""ESPN public JSON for the NHL (checked against the dev-nhl-samples probe, Nov 13 2026).

Scoreboard: DraftKings moneyline + puck line + total, and each team's PROBABLE STARTING GOALIE (`probables`).
Injuries: one league-wide list (statuses Out / Day-To-Day / Injured Reserve). No win probability for hockey.
Team codes: ESPN writes LA, NJ, SJ, TB (and UTAH on the teams list); the NHL data writes LAK, NJD, SJS, TBL, UTA.
Everything inside the board uses the NHL codes; `nhl_code` converts.
"""
from __future__ import annotations

from nba.espn import _int  # noqa: F401
from nba.http import get

SITE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
TO_NHL = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"}


def nhl_code(a: str | None) -> str | None:
    return TO_NHL.get(a, a)


def scoreboard(ymd: str) -> dict:
    return get(f"{SITE}/scoreboard?dates={ymd}&limit=50") or {}


def summary(event_id: str) -> dict:
    return get(f"{SITE}/summary?event={event_id}") or {}


def injuries() -> dict:
    """{NHL team code: [{"name", "status", "date", "comment"}]} from ESPN's league-wide list."""
    body = get(f"{SITE}/injuries") or {}
    out = {}
    for t in body.get("injuries") or []:
        code = None
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            code = code or nhl_code(((ath.get("team") or {}).get("abbreviation")))
        name = t.get("displayName") or ""
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            c = nhl_code(((ath.get("team") or {}).get("abbreviation"))) or code
            out.setdefault(c or name, []).append({"name": ath.get("displayName") or f"{ath.get('firstName', '')} {ath.get('lastName', '')}".strip(),
                                                  "last": ath.get("lastName"), "status": i.get("status"), "date": i.get("date"),
                                                  "comment": i.get("shortComment")})
    return out


def _odds(comp: dict) -> dict:
    for o in comp.get("odds") or []:
        h, a = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        ml_h, ml_a = h.get("moneyLine"), a.get("moneyLine")
        fav = "home" if h.get("favorite") else ("away" if a.get("favorite") else None)
        fav_ml = None
        try:                                    # the scoreboard often has only "CAR -325"
            fav_ml = int(str(o.get("details") or "").split()[-1])
        except (ValueError, IndexError):
            pass
        if fav and fav_ml is not None:
            if fav == "home" and ml_h is None:
                ml_h = fav_ml
            if fav == "away" and ml_a is None:
                ml_a = fav_ml
        return {"provider": (o.get("provider") or {}).get("name") or "ESPN", "details": o.get("details"), "ml_home": ml_h, "ml_away": ml_a,
                "favorite": fav, "puck_line": o.get("spread"), "total": o.get("overUnder")}
    return {}


def parse_events(sb: dict) -> list[dict]:
    games = []
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        side = {}
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            recs = [r.get("summary") for r in c.get("records") or []]
            prob = next((p for p in c.get("probables") or [] if p.get("name") == "probableStartingGoalie"), None)
            ath = (prob or {}).get("athlete") or {}
            side[c["homeAway"]] = {
                "espn_id": str(t.get("id")), "espn_abbr": t.get("abbreviation"), "abbr": nhl_code(t.get("abbreviation")),
                "name": t.get("location") or t.get("shortDisplayName"), "mascot": t.get("name"), "display": t.get("displayName"),
                "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None, "record": recs[0] if recs else None,
                "goalie": {"name": ath.get("displayName") or ath.get("fullName"), "last": (ath.get("fullName") or "").split(" ")[-1] or None,
                           "espn_id": ath.get("id"), "confirmed": bool(prob and (prob.get("status") or {}).get("name") == "Confirmed")} if prob else None}
        if "home" not in side or "away" not in side:
            continue
        st = (ev.get("status") or {}).get("type") or {}
        games.append({"id": ev["id"], "start_utc": ev.get("date"), "short": ev.get("shortName"), "state": st.get("state"),
                      "completed": bool(st.get("completed")), "detail": st.get("shortDetail"), "status": st.get("name"),
                      "period": (ev.get("status") or {}).get("period"), "clock": (ev.get("status") or {}).get("displayClock"),
                      "neutral": bool(comp.get("neutralSite")), "venue": (comp.get("venue") or {}).get("fullName"),
                      "season_type": (ev.get("season") or {}).get("type"), "home": side["home"], "away": side["away"], "odds": _odds(comp)})
    return games
