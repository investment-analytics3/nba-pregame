#!/usr/bin/env python3
"""Developer probe: save raw college football source responses (ESPN, Kalshi) to samples/cfb/.

Run by .github/workflows/cfb-probe.yml, which pushes them to the dev-cfb-samples branch so the
college parsers can be written against real data. Touches nothing on main.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba.http import get, FetchError  # noqa: E402

OUT = os.path.join("samples", "cfb")
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/football/college-football"
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
sb = save("scoreboard_fbs.json", lambda: get(f"{SITE}/scoreboard?groups=80&limit=300"))
save("scoreboard_week11.json", lambda: get(f"{SITE}/scoreboard?groups=80&seasontype=2&week=11&limit=300"))
save("scoreboard_week12.json", lambda: get(f"{SITE}/scoreboard?groups=80&seasontype=2&week=12&limit=300"))
save("rankings.json", lambda: get(f"{SITE}/rankings"))
save("fpi.json", lambda: get(f"{FITT}/powerindex?region=us&lang=en&limit=200"))
save("fpi_p2.json", lambda: get(f"{FITT}/powerindex?region=us&lang=en&limit=50&page=2"))
save("teams_fbs.json", lambda: get(f"{SITE}/teams?groups=80&limit=200"))
# a summary of a finished game and an upcoming one (injuries, predictor, pickcenter, winprobability)
evs = (sb or {}).get("events") or []
done = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("completed")]
pre = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("state") == "pre"]
if done:
    save("summary_final.json", lambda: get(f"{SITE}/summary?event={done[0]['id']}"))
if pre:
    save("summary_pre.json", lambda: get(f"{SITE}/summary?event={pre[0]['id']}"))
    for i, e in enumerate(pre[:12]):
        b = save(f"summary_pre_{i}.json", lambda e=e: get(f"{SITE}/summary?event={e['id']}"))
        if b and b.get("injuries"):
            print("  injuries present in", e.get("shortName"))
# Kalshi: which series covers college football games?
for s in ("KXNCAAFGAME", "KXCFBGAME", "KXNCAAF", "KXCOLLEGEFOOTBALLGAME"):
    save(f"kalshi_events_{s}.json",
         lambda s=s: get(f"{KALSHI}/events?series_ticker={s}&status=open&limit=100&with_nested_markets=true", headers=KH))
save("kalshi_series_search.json", lambda: get(f"{KALSHI}/series?category=Sports&limit=500", headers=KH))
