#!/usr/bin/env python3
"""Developer probe: save raw men's college basketball source responses (ESPN, Kalshi) to samples/cbb/.

Run by .github/workflows/cbb-probe.yml, which pushes them to the dev-cbb-samples branch so the
college basketball parsers can be written against real data. Touches nothing on main.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba.http import get, FetchError  # noqa: E402

OUT = os.path.join("samples", "cbb")
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/basketball/mens-college-basketball"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KH = {"User-Agent": "nba-pregame-dashboard/1.0"}


def save(name, fn):
    try:
        body = fn()
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(body, f, indent=1)
        print(f"ok   {name} ({len(json.dumps(body)) // 1024} KB)")
        return body
    except (FetchError, Exception) as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__} {str(e)[:200]}")
        return None


os.makedirs(OUT, exist_ok=True)
today = datetime.now(timezone.utc) - timedelta(hours=8)
ymd = lambda d: f"{d:%Y%m%d}"  # noqa: E731
sb = save("scoreboard_today.json", lambda: get(f"{SITE}/scoreboard?groups=50&dates={ymd(today)}&limit=400"))
y = save("scoreboard_yesterday.json", lambda: get(f"{SITE}/scoreboard?groups=50&dates={ymd(today - timedelta(days=1))}&limit=400"))
save("scoreboard_tomorrow.json", lambda: get(f"{SITE}/scoreboard?groups=50&dates={ymd(today + timedelta(days=1))}&limit=400"))
save("scoreboard_nogroup.json", lambda: get(f"{SITE}/scoreboard?dates={ymd(today)}&limit=400"))
save("rankings.json", lambda: get(f"{SITE}/rankings"))
save("bpi.json", lambda: get(f"{FITT}/powerindex?region=us&lang=en&limit=400"))
save("bpi_p2.json", lambda: get(f"{FITT}/powerindex?region=us&lang=en&limit=100&page=2"))
save("teams_d1.json", lambda: get(f"{SITE}/teams?groups=50&limit=500"))
save("injuries.json", lambda: get(f"{SITE}/injuries"))
evs = ((y or {}).get("events") or []) + ((sb or {}).get("events") or [])
done = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("completed")]
pre = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("state") == "pre"]
if done:
    save("summary_final.json", lambda: get(f"{SITE}/summary?event={done[0]['id']}"))
for i, e in enumerate(pre[:10]):
    b = save(f"summary_pre_{i}.json", lambda e=e: get(f"{SITE}/summary?event={e['id']}"))
    if b and b.get("injuries"):
        print("  injuries present in", e.get("shortName"), sum(len(t.get("injuries") or []) for t in b["injuries"]))
for s in ("KXNCAAMBGAME", "KXNCAABGAME"):
    save(f"kalshi_events_{s}.json",
         lambda s=s: get(f"{KALSHI}/events?series_ticker={s}&status=open&limit=200&with_nested_markets=true", headers=KH))
