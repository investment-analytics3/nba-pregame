#!/usr/bin/env python3
"""Developer probe for hockey: raw ESPN + Kalshi samples, and (one-off, for the backtest) two seasons of ESPN
closing moneylines with ESPN's in-game win probability at the period checkpoints. Writes samples/nhl/,
which .github/workflows/nhl-probe.yml pushes to the dev-nhl-samples branch. Touches nothing on main.

    python scripts/nhl_probe.py [--odds]
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba.http import get, FetchError  # noqa: E402

OUT = os.path.join("samples", "nhl")
SITE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
CORE = "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl"
NHL = "https://api-web.nhle.com/v1"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KH = {"User-Agent": "nba-pregame-dashboard/1.0"}
SEASONS = {"2025": (date(2024, 10, 4), date(2025, 4, 17)), "2026": (date(2025, 10, 7), date(2026, 4, 16))}
CHECKS = {"end1": (1, 0), "end2": (2, 0), "p3_10": (3, 600), "p3_8": (3, 480), "p3_5": (3, 300), "p3_3": (3, 180)}


def save(name, fn):
    try:
        body = fn()
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(body, f, indent=1)
        print(f"ok   {name} ({len(json.dumps(body)) // 1024} KB)")
        return body
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__} {str(e)[:200]}")
        return None


def secs(clock: str) -> int:
    try:
        m, s = str(clock).split(":")
        return int(m) * 60 + int(float(s))
    except ValueError:
        return 0


def wp_at(summ: dict) -> dict:
    """Home win probability at each checkpoint (first play at or after the mark), if ESPN has it."""
    wp = {w.get("playId"): w.get("homeWinPercentage") for w in summ.get("winprobability") or []}
    plays = summ.get("plays") or []
    out = {}
    for key, (per, left) in CHECKS.items():
        hit = None
        for p in plays:
            pn = (p.get("period") or {}).get("number") or 0
            cl = secs((p.get("clock") or {}).get("displayValue", "20:00"))
            if pn > per or (pn == per and cl <= left):
                hit = p
                break
        if hit is not None:
            out[key] = {"wp": wp.get(hit.get("id")), "home": hit.get("homeScore"), "away": hit.get("awayScore")}
    return out


def odds_history():
    fields = ["season", "date", "espn_id", "home", "away", "home_score", "away_score", "ot", "provider", "home_ml", "away_ml",
              "spread", "over_under", "fav", "wp_rows"] + [f"{k}_{x}" for k in CHECKS for x in ("wp", "home", "away")]
    for season, (d0, d1) in SEASONS.items():
        path = os.path.join(OUT, f"odds_{season}.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fields)
            w.writeheader()
            d = d0
            n = 0
            while d <= d1:
                try:
                    sb = get(f"{SITE}/scoreboard?dates={d:%Y%m%d}&limit=50") or {}
                except FetchError:
                    sb = {}
                for ev in sb.get("events", []):
                    if (ev.get("season") or {}).get("type") != 2 or not ((ev.get("status") or {}).get("type") or {}).get("completed"):
                        continue
                    comp = ev["competitions"][0]
                    side = {c["homeAway"]: c for c in comp["competitors"]}
                    try:
                        summ = get(f"{SITE}/summary?event={ev['id']}") or {}
                    except FetchError:
                        continue
                    pc = [p for p in summ.get("pickcenter") or [] if p.get("homeTeamOdds")]
                    p = pc[0] if pc else {}
                    h, a = p.get("homeTeamOdds") or {}, p.get("awayTeamOdds") or {}
                    row = {"season": season, "date": d.isoformat(), "espn_id": ev["id"],
                           "home": side["home"]["team"]["abbreviation"], "away": side["away"]["team"]["abbreviation"],
                           "home_score": side["home"].get("score"), "away_score": side["away"].get("score"),
                           "ot": ((ev.get("status") or {}).get("period") or 3) > 3, "provider": (p.get("provider") or {}).get("name"),
                           "home_ml": h.get("moneyLine"), "away_ml": a.get("moneyLine"), "spread": p.get("spread"),
                           "over_under": p.get("overUnder"), "fav": "home" if h.get("favorite") else ("away" if a.get("favorite") else ""),
                           "wp_rows": len(summ.get("winprobability") or [])}
                    for k, v in wp_at(summ).items():
                        row[f"{k}_wp"], row[f"{k}_home"], row[f"{k}_away"] = v.get("wp"), v.get("home"), v.get("away")
                    w.writerow(row)
                    n += 1
                    time.sleep(0.1)
                f.flush()
                d += timedelta(days=1)
            print(f"ok   odds_{season}.csv ({n} games)")


os.makedirs(OUT, exist_ok=True)
today = datetime.now(timezone.utc) - timedelta(hours=8)
ymd = lambda d: f"{d:%Y%m%d}"  # noqa: E731
sb = save("scoreboard_today.json", lambda: get(f"{SITE}/scoreboard?dates={ymd(today)}"))
y = save("scoreboard_yesterday.json", lambda: get(f"{SITE}/scoreboard?dates={ymd(today - timedelta(days=1))}"))
save("scoreboard_tomorrow.json", lambda: get(f"{SITE}/scoreboard?dates={ymd(today + timedelta(days=1))}"))
save("teams.json", lambda: get(f"{SITE}/teams"))
save("standings_espn.json", lambda: get("https://site.web.api.espn.com/apis/v2/sports/hockey/nhl/standings"))
save("injuries_espn.json", lambda: get(f"{SITE}/injuries"))
save("powerindex_espn.json", lambda: get("https://site.web.api.espn.com/apis/fitt/v3/sports/hockey/nhl/powerindex?region=us&lang=en"))
evs = ((y or {}).get("events") or []) + ((sb or {}).get("events") or [])
done = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("completed")]
pre = [e for e in evs if ((e.get("status") or {}).get("type") or {}).get("state") == "pre"]
if done:
    save("summary_final.json", lambda: get(f"{SITE}/summary?event={done[0]['id']}"))
for i, e in enumerate(pre[:3]):
    save(f"summary_pre_{i}.json", lambda e=e: get(f"{SITE}/summary?event={e['id']}"))
# NHL's own API (schedule, standings, a game's landing page with goalie info)
nsch = save("nhl_schedule_today.json", lambda: get(f"{NHL}/schedule/{today:%Y-%m-%d}"))
save("nhl_standings.json", lambda: get(f"{NHL}/standings/now"))
try:
    gid = nsch["gameWeek"][0]["games"][0]["id"]
    save("nhl_landing.json", lambda: get(f"{NHL}/gamecenter/{gid}/landing"))
    save("nhl_right_rail.json", lambda: get(f"{NHL}/gamecenter/{gid}/right-rail"))
except Exception as e:  # noqa: BLE001
    print("no NHL game id:", e)
save("kalshi_events_KXNHLGAME.json", lambda: get(f"{KALSHI}/events?series_ticker=KXNHLGAME&status=open&limit=200&with_nested_markets=true", headers=KH))
if "--odds" in sys.argv:
    odds_history()
