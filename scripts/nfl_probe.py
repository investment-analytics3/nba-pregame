#!/usr/bin/env python3
"""Developer probe: save raw NFL source responses (ESPN, Kalshi) to samples/nfl/.

Run by .github/workflows/nfl-probe.yml, which pushes them to the dev-nfl-samples
branch so parsers can be written against real data. Touches nothing on main.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba.http import get, FetchError  # noqa: E402

OUT = os.path.join("samples", "nfl")
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KH = {"User-Agent": "nba-pregame-dashboard/1.0"}


def save(name, fn):
    try:
        body = fn()
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(body, f, indent=1)
        print(f"ok   {name} ({len(json.dumps(body)) // 1024} KB)")
        return body
    except FetchError as e:
        print(f"FAIL {name}: {e}")
        return None


os.makedirs(OUT, exist_ok=True)
sb = save("scoreboard_current.json", lambda: get(f"{SITE}/scoreboard"))
past = save("scoreboard_20261108.json", lambda: get(f"{SITE}/scoreboard?dates=20261108"))
save("scoreboard_week9.json", lambda: get(f"{SITE}/scoreboard?seasontype=2&week=9"))
for nm, body in (("upcoming", sb), ("final", past)):
    evs = (body or {}).get("events") or []
    if evs:
        save(f"summary_{nm}.json", lambda: get(f"{SITE}/summary?event={evs[0]['id']}"))
save("injuries.json", lambda: get(f"{SITE}/injuries"))
save("fpi.json", lambda: get("https://site.web.api.espn.com/apis/fitt/v3/sports/football/nfl/powerindex?region=us&lang=en"))
evs = (sb or {}).get("events") or []
if evs:
    save("core_odds_upcoming.json", lambda: get(f"{CORE}/events/{evs[0]['id']}/competitions/{evs[0]['id']}/odds"))
    save("core_predictor_upcoming.json", lambda: get(f"{CORE}/events/{evs[0]['id']}/competitions/{evs[0]['id']}/predictor"))
save("kalshi_events_open.json", lambda: get(f"{KALSHI}/events?series_ticker=KXNFLGAME&status=open&with_nested_markets=true&limit=40", headers=KH))
save("kalshi_markets_settled.json", lambda: get(f"{KALSHI}/markets?series_ticker=KXNFLGAME&status=settled&limit=20", headers=KH))
save("kalshi_series.json", lambda: get(f"{KALSHI}/series/KXNFLGAME", headers=KH))
