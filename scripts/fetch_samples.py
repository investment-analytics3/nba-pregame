#!/usr/bin/env python3
"""Save raw responses from every data source into samples/ (developer tool).

Used to build and test the parsers against real data. Run from the
"Fetch samples" workflow; the folder is uploaded as a short-lived artifact.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from check_sources import ESPN_SITE, ESPN_CORE, fetch, ymd  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
OUT = "samples"


def save(name: str, url: str, want: str = "json"):
    st, body, _ = fetch(url, want=want, retries=1)
    path = os.path.join(OUT, name)
    if st == 200 and body is not None:
        mode = "wb" if want != "json" else "w"
        with open(path, mode) as f:
            if want == "json":
                json.dump(body, f, indent=1)
            else:
                f.write(body)
        print(f"saved {name} ({url})")
        return body
    print(f"FAILED {name}: {st} {body if st is None else ''} ({url})")
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(PT)
    today = now.date()
    season_year = now.year + 1 if now.month >= 8 else now.year

    save("teams.json", f"{ESPN_SITE}/teams")
    for label, d in [("yesterday", today - timedelta(days=1)), ("today", today),
                     ("tomorrow", today + timedelta(days=1))]:
        sb = save(f"scoreboard_{label}.json", f"{ESPN_SITE}/scoreboard?dates={d:%Y%m%d}")
        if sb and sb.get("events"):
            ev = sb["events"][0]
            save(f"summary_{label}.json", f"{ESPN_SITE}/summary?event={ev['id']}")

    # does a date range work in one call? (season so far)
    save("scoreboard_range.json",
         f"{ESPN_SITE}/scoreboard?dates={season_year - 1}1001-{today:%Y%m%d}&limit=1000")
    save("injuries.json", f"{ESPN_SITE}/injuries")
    save("bpi_fitt.json",
         "https://site.web.api.espn.com/apis/fitt/v3/sports/basketball/nba/powerindex?region=us&lang=en")
    save("bpi_core.json", f"{ESPN_CORE}/seasons/{season_year}/powerindex?limit=40")
    save("team_schedule_bos.json", f"{ESPN_SITE}/teams/2/schedule")

    # newest official injury report PDF in the last 24h
    base = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
    t = now.astimezone(ET)
    t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
    for _ in range(96):
        name = f"{t:%Y-%m-%d}_{t:%I}_{t:%M}{t:%p}.pdf"
        st, body, _ = fetch(base + name, want="bytes", retries=0, timeout=15)
        if st == 200 and body and body[:4] == b"%PDF":
            with open(os.path.join(OUT, "injury_report.pdf"), "wb") as f:
                f.write(body)
            print("saved injury_report.pdf", name)
            break
        t -= timedelta(minutes=15)

    page = save("nba_power_rankings.html", "https://www.nba.com/news/category/power-rankings", want="bytes")
    if page:
        import re
        weeks = re.findall(rb'/news/power-rankings-(\d{4})-(\d{2})-week-(\d+)', page)
        if weeks:
            y1, y2, wk = max(weeks, key=lambda w: (int(w[0]), int(w[2])))
            slug = f"power-rankings-{y1.decode()}-{y2.decode()}-week-{wk.decode()}"
            save("nba_power_rankings_article.html", f"https://www.nba.com/news/{slug}", want="bytes")


if __name__ == "__main__":
    main()
