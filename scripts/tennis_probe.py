#!/usr/bin/env python3
"""Tennis probe (developer): saves raw responses from candidate tennis sources to samples/tennis/ so the tennis
board's parsers can be built against real data. tennis-probe.yml pushes them to the dev-tennis-samples branch.

    python scripts/tennis_probe.py [--deep]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "tennis")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
LOG = []


def fetch(name: str, url: str, headers=None, limit=3_000_000):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=headers or UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(limit)
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = e.read()[:20000], e.code
    except Exception as e:  # noqa: BLE001
        body, code = repr(e).encode(), 0
    path = os.path.join(OUT, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    LOG.append({"name": name, "url": url, "status": code, "bytes": len(body), "secs": round(time.time() - t0, 1)})
    print(f"{code:>3} {len(body):>9} {name}  {url}")
    return code, body


def jload(body):
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return None


def kalshi():
    """Every Kalshi series whose title/ticker/tags mention tennis, then open events + markets for each."""
    series = []
    cursor = ""
    for i in range(10):
        code, body = fetch(f"kalshi/series_sports_{i}.json", f"{KALSHI}/series?category=Sports&limit=1000" +
                           (f"&cursor={cursor}" if cursor else ""), headers={"User-Agent": "tennis-probe/1.0"}, limit=60_000_000)
        js = jload(body) or {}
        series += js.get("series") or []
        cursor = js.get("cursor") or ""
        if not cursor:
            break
    keys = ("tennis", "atp", "wta", "itf", "challenger", "wimbledon", "open", "davis", "billie")
    ten = [s for s in series if any(k in (s.get("title", "") + " " + s.get("ticker", "") + " " +
                                          " ".join(s.get("tags") or [])).lower() for k in ("tennis", "atp", "wta", "itf", "challenger"))]
    with open(os.path.join(OUT, "kalshi", "tennis_series.json"), "w") as f:
        json.dump(ten, f, indent=1)
    print(f"Kalshi: {len(series)} sports series, {len(ten)} tennis-like")
    summary = []
    for s in ten:
        t = s["ticker"]
        code, body = fetch(f"kalshi/events_{t}.json", f"{KALSHI}/events?series_ticker={t}&status=open&with_nested_markets=true&limit=200",
                           headers={"User-Agent": "tennis-probe/1.0"})
        evs = (jload(body) or {}).get("events") or []
        summary.append({"ticker": t, "title": s.get("title"), "open_events": len(evs),
                        "sample": [e.get("title") for e in evs[:5]]})
    # also settled events of the singles match series (for grading format)
    singles = [x["ticker"] for x in summary if x["open_events"] and any(k in x["ticker"] for k in ("GAME", "MATCH"))
               and "DOUBLES" not in x["ticker"] and "TABLE" not in x["ticker"] and "ITTF" not in x["ticker"]]
    for t in singles[:8]:
        fetch(f"kalshi/settled_{t}.json", f"{KALSHI}/markets?series_ticker={t}&status=settled&limit=50",
              headers={"User-Agent": "tennis-probe/1.0"})
    with open(os.path.join(OUT, "kalshi", "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    _ = keys


def others(deep: bool):
    today = datetime.now(timezone.utc).date()
    y = today - timedelta(days=1)
    # Tennis Abstract (Elo + player match files)
    fetch("ta/atp_elo.html", "https://tennisabstract.com/reports/atp_elo_ratings.html")
    fetch("ta/wta_elo.html", "https://tennisabstract.com/reports/wta_elo_ratings.html")
    fetch("ta/sinner.js", "https://www.tennisabstract.com/jsmatches/JannikSinner.js")
    fetch("ta/sinner_page.html", "https://www.tennisabstract.com/cgi-bin/player.cgi?p=JannikSinner")
    fetch("ta/sinner_classic.html", "https://www.tennisabstract.com/cgi-bin/player-classic.cgi?p=JannikSinner")
    fetch("ta/sinner_career.js", "https://www.tennisabstract.com/jsmatches/JannikSinnerCareer.js")
    fetch("ta/alcaraz.js", "https://www.tennisabstract.com/jsmatches/CarlosAlcaraz.js")
    fetch("ta/rune.js", "https://www.tennisabstract.com/jsmatches/HolgerRune.js")
    fetch("ta/squire.js", "https://www.tennisabstract.com/jsmatches/HenriSquire.js")
    fetch("ta/sabalenka_page.html", "https://www.tennisabstract.com/cgi-bin/wplayer.cgi?p=ArynaSabalenka")
    fetch("ta/sabalenka.js", "https://www.tennisabstract.com/jsmatches/ArynaSabalenka.js")
    # Sackmann raw files
    for f in ("tennis_atp/master/atp_matches_2025.csv", "tennis_atp/master/atp_matches_2026.csv",
              "tennis_atp/master/atp_matches_qual_chall_2025.csv", "tennis_wta/master/wta_matches_2025.csv"):
        fetch("sackmann/" + f.split("/")[-1], "https://raw.githubusercontent.com/JeffSackmann/" + f, limit=300_000)
    # Sofascore
    for d in (y, today, today + timedelta(days=1)):
        fetch(f"sofa/scheduled_{d}.json", f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{d}")
    fetch("sofa/rank_atp.json", "https://api.sofascore.com/api/v1/rankings/type/5")
    fetch("sofa/rank_wta.json", "https://api.sofascore.com/api/v1/rankings/type/6")
    code, body = fetch(f"sofa/www_scheduled_{y}.json", f"https://www.sofascore.com/api/v1/sport/tennis/scheduled-events/{y}")
    js = jload(body) or {}
    evs = [e for e in js.get("events") or [] if (e.get("status") or {}).get("type") == "finished"]
    for e in evs[:2]:
        fetch(f"sofa/event_{e['id']}_stats.json", f"https://www.sofascore.com/api/v1/event/{e['id']}/statistics")
    # ESPN
    for tour in ("atp", "wta"):
        fetch(f"espn/{tour}_scoreboard.json", f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard")
        fetch(f"espn/{tour}_rankings.json", f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/rankings")
    # tennis-data.co.uk, Bzzoiro, Matchstat-free, Flashscore-ish
    fetch("tdcu/2026.xlsx", "http://www.tennis-data.co.uk/2026/2026.xlsx")
    fetch("tdcu/2026w.xlsx", "http://www.tennis-data.co.uk/2026w/2026.xlsx")
    fetch("bzzoiro/matches.json", "https://tennis.bzzoiro.com/api/matches/")
    fetch("atp/rankings.html", "https://www.atptour.com/en/rankings/singles")
    fetch("wta/rankings_api.json", "https://api.wtatennis.com/tennis/players/ranked?page=0&pageSize=20&type=rankSingles&sort=asc&metric=SINGLES")
    fetch("predict/home.html", "https://predict.tennis/")
    fetch("lwos/predictions.html", "https://lastwordonsports.com/tennis/category/predictions/")
    _ = deep


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    try:
        kalshi()
    except Exception as e:  # noqa: BLE001
        print("kalshi probe error", e)
    others("--deep" in sys.argv)
    with open(os.path.join(OUT, "log.json"), "w") as f:
        json.dump(LOG, f, indent=1)
