"""ESPN public JSON for college football. Same response shapes as the NFL (checked against the
dev-cfb-samples probe, Nov 10 2026), so the NFL parsers are reused.

Note: ESPN's college game pages carry no injury list (12 of 12 sampled had none), so injuries
are usually unknown; the board warns instead of blocking (owner decision Nov 10).
"""
from __future__ import annotations

from nba.http import get
from nfl.espn import parse_events, parse_injuries, parse_odds, parse_predictor  # noqa: F401  (same shapes)

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/football/college-football"


def scoreboard_week(week: int, season_type: int = 2) -> dict:
    return get(f"{SITE}/scoreboard?groups=80&seasontype={season_type}&week={week}&limit=300") or {}


def scoreboard_date(ymd: str) -> dict:
    return get(f"{SITE}/scoreboard?groups=80&dates={ymd}&limit=300") or {}


def summary(event_id: str) -> dict:
    return get(f"{SITE}/summary?event={event_id}") or {}


def rankings() -> dict:
    """{"ap": {team_id: rank}, "cfp": {team_id: rank}} (empty dicts when a poll isn't out)."""
    body = get(f"{SITE}/rankings") or {}
    out = {"ap": {}, "cfp": {}}
    for r in body.get("rankings") or []:
        name = f"{r.get('name', '')} {r.get('shortName', '')}".lower()
        key = "cfp" if ("playoff" in name or "cfp" in name) else ("ap" if "ap " in name + " " and "coaches" not in name else None)
        if not key:
            continue
        for x in r.get("ranks") or []:
            tid = (x.get("team") or {}).get("id")
            if tid and x.get("current"):
                out[key][int(tid)] = int(x["current"])
    return out


def fpi_ranks(body: dict | None = None) -> dict:
    """{team_id: FPI rank} for every FBS team."""
    body = body if body is not None else (get(f"{FITT}/powerindex?region=us&lang=en&limit=200") or {})
    names = next((c.get("names") or [] for c in body.get("categories", []) if c.get("name") == "fpi"), [])
    i = names.index("fpirank") if "fpirank" in names else None
    out = {}
    for t in body.get("teams", []):
        tid = (t.get("team") or {}).get("id")
        for c in t.get("categories", []):
            v = c.get("values") or []
            if c.get("name") == "fpi" and i is not None and len(v) > i and v[i] is not None and tid:
                out[int(tid)] = int(v[i])
    return out
