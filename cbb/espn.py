"""ESPN public JSON for men's college basketball (checked against the dev-cbb-samples probe, Nov 11 2026).

groups=50 = Division I. Same response shapes as the NBA (odds, box scores) with college team names.
ESPN carries no college injury lists (0 of 11 sampled game pages; the injuries endpoint is empty),
so injuries are usually unknown and the board warns instead of blocking (owner decision Nov 11).
"""
from __future__ import annotations

from nba.espn import _int, _pair, parse_odds, parse_pickcenter  # noqa: F401  (same shapes)
from nba.http import get

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/basketball/mens-college-basketball"


def scoreboard(ymd: str) -> dict:
    return get(f"{SITE}/scoreboard?groups=50&dates={ymd}&limit=400") or {}


def summary(event_id: str) -> dict:
    return get(f"{SITE}/summary?event={event_id}") or {}


def d1_teams() -> dict:
    """{team_id: {"abbr", "location", "name", "display"}} for every Division I team (365 in 2026-27)."""
    body = get(f"{SITE}/teams?groups=50&limit=500") or {}
    out = {}
    for t in ((body.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []:
        t = t.get("team") or {}
        if t.get("id"):
            out[int(t["id"])] = {"abbr": t.get("abbreviation"), "location": t.get("location") or t.get("shortDisplayName"),
                                 "name": t.get("name"), "display": t.get("displayName")}
    return out


def parse_events(sb: dict) -> list[dict]:
    games = []
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        side = {}
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            recs = [r.get("summary") for r in c.get("records") or []]
            rank = (c.get("curatedRank") or {}).get("current")
            side[c["homeAway"]] = {
                "id": str(t.get("id")), "abbr": t.get("abbreviation"), "name": t.get("location") or t.get("shortDisplayName"),
                "mascot": t.get("name"), "display": t.get("displayName"),
                "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None,
                "record": recs[0] if recs else None, "ap": rank if rank and rank <= 25 else None}
        if "home" not in side or "away" not in side:
            continue
        st = (ev.get("status") or {}).get("type") or {}
        stt = ev.get("status") or {}
        games.append({
            "id": ev["id"], "start_utc": ev.get("date"), "short": ev.get("shortName"),
            "state": st.get("state"), "completed": bool(st.get("completed")), "detail": st.get("shortDetail"),
            "status": st.get("name"), "period": stt.get("period"), "clock": stt.get("displayClock"),
            "neutral": bool(comp.get("neutralSite")), "venue": (comp.get("venue") or {}).get("fullName"),
            "home": side["home"], "away": side["away"],
            "odds": parse_odds(comp, side["home"]["id"], side["away"]["id"]),
        })
    return games


def box_rows(summ: dict, game_date: str) -> list[dict]:
    """Two team rows (sportsdataverse team_box column names) for a finished game, for the catch-up."""
    comp = summ["header"]["competitions"][0]
    score = {str(c["team"]["id"]): _int(c.get("score")) for c in comp["competitors"]}
    site = {str(c["team"]["id"]): c["homeAway"] for c in comp["competitors"]}
    teams = (summ.get("boxscore") or {}).get("teams") or []
    if len(teams) != 2:
        raise ValueError("box score does not have two teams")
    rows = []
    for t in teams:
        s = {x["name"]: x.get("displayValue") for x in t.get("statistics", [])}
        fgm, fga = _pair(s["fieldGoalsMade-fieldGoalsAttempted"])
        fg3m, _ = _pair(s["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
        _, fta = _pair(s["freeThrowsMade-freeThrowsAttempted"])
        tid = str(t["team"]["id"])
        rows.append({"game_id": int(summ["header"]["id"]), "game_date": game_date, "team_id": int(tid),
                     "team_home_away": "neutral" if comp.get("neutralSite") else site.get(tid, ""),
                     "team_abbreviation": t["team"].get("abbreviation"),
                     "field_goals_made": fgm, "field_goals_attempted": fga, "three_point_field_goals_made": fg3m,
                     "free_throws_attempted": fta, "offensive_rebounds": _int(s.get("offensiveRebounds")),
                     "total_turnovers": _int(s.get("totalTurnovers", s.get("turnovers"))), "team_score": score.get(tid, 0)})
    rows[0]["opponent_team_id"], rows[1]["opponent_team_id"] = rows[1]["team_id"], rows[0]["team_id"]
    return rows


def rankings() -> dict:
    """{"ap": {team_id: rank}} (empty when the poll isn't out)."""
    body = get(f"{SITE}/rankings") or {}
    out = {"ap": {}}
    for r in body.get("rankings") or []:
        if "ap" not in f"{r.get('name', '')} {r.get('shortName', '')}".lower().split():
            continue
        for x in r.get("ranks") or []:
            tid = (x.get("team") or {}).get("id")
            if tid and x.get("current"):
                out["ap"][int(tid)] = int(x["current"])
    return out


def bpi_ranks(body: dict | None = None) -> dict:
    """{team_id: BPI rank} for every D-I team (one page of 400)."""
    body = body if body is not None else (get(f"{FITT}/powerindex?region=us&lang=en&limit=400") or {})
    names = next((c.get("names") or [] for c in body.get("categories", []) if c.get("name") == "bpi"), [])
    i = names.index("bpirank") if "bpirank" in names else None
    out = {}
    for t in body.get("teams", []):
        tid = (t.get("team") or {}).get("id")
        for c in t.get("categories", []):
            v = c.get("values") or []
            if c.get("name") == "bpi" and i is not None and len(v) > i and v[i] is not None and tid:
                out[int(tid)] = int(v[i])
    return out


def parse_predictor(summ: dict) -> dict:
    p = summ.get("predictor") or {}
    try:
        return {"home": float(p["homeTeam"]["gameProjection"]) / 100, "away": float(p["awayTeam"]["gameProjection"]) / 100}
    except (KeyError, TypeError, ValueError):
        return {}


def home_wp(summ: dict):
    wp = summ.get("winprobability") or []
    try:
        return float(wp[-1]["homeWinPercentage"]) if wp else None
    except (KeyError, TypeError, ValueError):
        return None
