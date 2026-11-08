"""ESPN public JSON: schedule, scores, odds, box scores, injuries, BPI.

These endpoints are unofficial (no key, no documentation) and could change.
Every parser here is written against real responses captured in Nov 2026.
"""
from __future__ import annotations

from datetime import date, datetime

from .http import get

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/basketball/nba"

REGULAR_SEASON = 2


# --------------------------------------------------------------------------- #
# Raw calls
# --------------------------------------------------------------------------- #
def scoreboard(d: date) -> dict:
    return get(f"{SITE}/scoreboard?dates={d:%Y%m%d}") or {}


def summary(event_id: str) -> dict:
    return get(f"{SITE}/summary?event={event_id}") or {}


def teams() -> list[dict]:
    body = get(f"{SITE}/teams") or {}
    out = []
    for t in body["sports"][0]["leagues"][0]["teams"]:
        t = t["team"]
        out.append({"id": t["id"], "abbr": t["abbreviation"], "name": t["name"],
                    "display": t["displayName"], "location": t.get("location", "")})
    return out


# --------------------------------------------------------------------------- #
# Scoreboard parsing
# --------------------------------------------------------------------------- #
def season_game_days(sb: dict) -> list[date]:
    """Dates with games this season, from the scoreboard's league calendar."""
    try:
        cal = sb["leagues"][0]["calendar"]
    except (KeyError, IndexError):
        return []
    days = []
    for c in cal:
        s = c if isinstance(c, str) else c.get("startDate", "")
        try:
            days.append(datetime.strptime(s[:10], "%Y-%m-%d").date())
        except ValueError:
            pass
    return sorted(set(days))


def _ml(side: dict | None) -> int | None:
    if not side:
        return None
    for k in ("current", "close", "open"):
        v = (side.get(k) or {}).get("odds")
        if v not in (None, "", "OFF", "EVEN"):
            try:
                return int(str(v).replace("+", ""))
            except ValueError:
                pass
        if v == "EVEN":
            return 100
    return None


def parse_odds(comp: dict, home_id: str, away_id: str) -> dict | None:
    """Spread and favorite. ESPN's spread number is unsigned; use the favorite flags."""
    for o in comp.get("odds") or []:
        ht, at = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        if o.get("spread") is None and not o.get("details"):
            continue
        spread = abs(float(o["spread"])) if o.get("spread") is not None else None
        details = o.get("details") or ""
        ml = o.get("moneyline") or {}
        ml_home, ml_away = _ml(ml.get("home")), _ml(ml.get("away"))
        fav = None
        if ht.get("favorite"):
            fav = home_id
        elif at.get("favorite"):
            fav = away_id
        if (spread == 0 or details.upper() in ("EVEN", "PK", "PICK")) and ml_home and ml_away:
            fav = home_id if ml_home < ml_away else away_id   # pick'em: moneyline breaks the tie
            spread = 0.0
        return {"favorite_id": fav, "spread": spread, "details": details,
                "provider": (o.get("provider") or {}).get("name"),
                "ml_home": ml_home, "ml_away": ml_away}
    return None


def parse_pickcenter(summ: dict, home_id: str, away_id: str) -> dict | None:
    """Closing line kept on a game's page after it ends (same shape as parse_odds)."""
    for o in summ.get("pickcenter") or []:
        if o.get("spread") is None and not o.get("details"):
            continue
        ht, at = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        spread = abs(float(o["spread"])) if o.get("spread") is not None else None
        ml_home, ml_away = ht.get("moneyLine"), at.get("moneyLine")
        fav = home_id if ht.get("favorite") else (away_id if at.get("favorite") else None)
        if spread == 0 and ml_home and ml_away:
            fav = home_id if ml_home < ml_away else away_id
        return {"favorite_id": fav, "spread": spread, "details": o.get("details") or "",
                "provider": (o.get("provider") or {}).get("name"), "ml_home": ml_home, "ml_away": ml_away,
                "closing": True}
    return None


def parse_events(sb: dict) -> list[dict]:
    games = []
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        side = {}
        for c in comp.get("competitors", []):
            recs = {r.get("name") or r.get("type"): r.get("summary") for r in c.get("records", [])}
            side[c["homeAway"]] = {
                "id": c["team"]["id"], "abbr": c["team"]["abbreviation"],
                "name": c["team"].get("name") or c["team"].get("shortDisplayName"),
                "display": c["team"].get("displayName"),
                "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None,
                "record": recs.get("overall") or recs.get("total"),
            }
        if "home" not in side or "away" not in side:
            continue
        st = ev.get("status", {}).get("type", {})
        games.append({
            "id": ev["id"], "start_utc": ev.get("date"), "short": ev.get("shortName"),
            "state": st.get("state"), "completed": bool(st.get("completed")),
            "season_year": (ev.get("season") or {}).get("year"),
            "season_type": (ev.get("season") or {}).get("type"),
            "neutral": bool(comp.get("neutralSite")),
            "home": side["home"], "away": side["away"],
            "odds": parse_odds(comp, side["home"]["id"], side["away"]["id"]),
        })
    return games


# --------------------------------------------------------------------------- #
# Box score parsing
# --------------------------------------------------------------------------- #
def _pair(s: str) -> tuple[int, int]:
    a, b = str(s).split("-")
    return int(a), int(b)


def _int(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def parse_boxscore(summ: dict, game_date: date) -> tuple[list[dict], list[dict]]:
    """Return (team_rows, player_rows) for a finished game. Raises on missing data."""
    comp = summ["header"]["competitions"][0]
    neutral = bool(comp.get("neutralSite"))
    score = {c["team"]["id"]: _int(c.get("score")) for c in comp["competitors"]}
    site = {c["team"]["id"]: c["homeAway"] for c in comp["competitors"]}
    teams_box = summ["boxscore"]["teams"]
    if len(teams_box) != 2:
        raise ValueError("box score does not have two teams")
    lines = {}
    for t in teams_box:
        tid = t["team"]["id"]
        s = {x["name"]: x.get("displayValue") for x in t.get("statistics", [])}
        fgm, fga = _pair(s["fieldGoalsMade-fieldGoalsAttempted"])
        fg3m, fg3a = _pair(s["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
        ftm, fta = _pair(s["freeThrowsMade-freeThrowsAttempted"])
        tov = s.get("totalTurnovers", s.get("turnovers"))
        lines[tid] = {
            "team_id": tid, "team": t["team"]["abbreviation"],
            "fgm": fgm, "fga": fga, "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta,
            "oreb": _int(s.get("offensiveRebounds")), "dreb": _int(s.get("defensiveRebounds")),
            "tov": _int(tov), "ast": _int(s.get("assists")),
        }
    ids = list(lines)
    rows = []
    for tid in ids:
        oid = ids[1] if tid == ids[0] else ids[0]
        r = {"game_id": summ["header"]["id"], "game_date": game_date.isoformat(),
             "site": "neutral" if neutral else site.get(tid, ""),
             **lines[tid], "opp_id": oid, "opp": lines[oid]["team"],
             "pts": score[tid], "opp_pts": score[oid]}
        rows.append(r)

    players = []
    for grp in summ["boxscore"].get("players", []) or []:
        tid = grp["team"]["id"]
        for stat in grp.get("statistics", []) or []:
            labels = stat.get("labels") or []
            i_min = labels.index("MIN") if "MIN" in labels else None
            i_pts = labels.index("PTS") if "PTS" in labels else None
            for a in stat.get("athletes", []) or []:
                vals = a.get("stats") or []
                players.append({
                    "game_id": summ["header"]["id"], "game_date": game_date.isoformat(),
                    "team_id": tid, "team": lines[tid]["team"],
                    "player_id": a["athlete"]["id"], "player": a["athlete"]["displayName"],
                    "starter": 1 if a.get("starter") else 0,
                    "dnp": 1 if a.get("didNotPlay") else 0,
                    "minutes": _int(vals[i_min]) if (i_min is not None and len(vals) > i_min) else 0,
                    "pts": _int(vals[i_pts]) if (i_pts is not None and len(vals) > i_pts) else 0,
                })
    return rows, players


# --------------------------------------------------------------------------- #
# Injuries and BPI
# --------------------------------------------------------------------------- #
def injuries() -> list[dict]:
    body = get(f"{SITE}/injuries") or {}
    out = []
    for grp in body.get("injuries", []):
        for i in grp.get("injuries", []):
            a = i.get("athlete") or {}
            team = (a.get("team") or {})
            out.append({
                "team_id": str(grp.get("id") or team.get("id") or ""),
                "team_name": grp.get("displayName"),
                "player": a.get("displayName"), "status": i.get("status"),
                "detail": i.get("shortComment") or "",
                "updated": i.get("date"),
            })
    return out


def bpi() -> dict[str, dict]:
    """{team_id: {"bpi": float, "rank": int}} from ESPN's Basketball Power Index."""
    body = get(f"{FITT}/powerindex?region=us&lang=en") or {}
    labels = []
    for c in body.get("categories", []):
        if c.get("name") == "bpi":
            labels = c.get("names") or []
    i_bpi = labels.index("bpi") if "bpi" in labels else 0
    i_rank = labels.index("bpirank") if "bpirank" in labels else 1
    out = {}
    for t in body.get("teams", []):
        for c in t.get("categories", []):
            if c.get("name") == "bpi":
                v = c.get("values") or []
                if len(v) > max(i_bpi, i_rank) and v[i_rank] is not None:
                    out[t["team"]["id"]] = {"bpi": v[i_bpi], "rank": int(v[i_rank])}
    return {"updated": body.get("lastUpdated"), "teams": out}
