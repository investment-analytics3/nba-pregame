#!/usr/bin/env python3
"""Developer probe: can the expert-pick sources be read from GitHub's servers?

Fetches last week's pick articles (the report's sources), each site's robots.txt and a few
guessed addresses for next week, and saves them to samples/picks/ (pushed to the
dev-nfl-picks branch by nfl-picks-probe.yml). Touches nothing on main.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

OUT = os.path.join("samples", "picks")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
URLS = {
    "cbs_w9": "https://www.cbssports.com/nfl/news/nfl-week-9-odds-how-to-watch-streaming-expert-picks/",
    "cbs_w10_guess": "https://www.cbssports.com/nfl/news/nfl-week-10-odds-how-to-watch-streaming-expert-picks/",
    "cbs_picks_page": "https://www.cbssports.com/nfl/expert-picks/",
    "br_w9": "https://bleacherreport.com/articles/25529216-bleacher-reports-expert-week-9-2026-nfl-picks",
    "covers_w9": "https://www.covers.com/nfl/picks-against-the-spread-week-9-2026",
    "covers_w10_guess": "https://www.covers.com/nfl/picks-against-the-spread-week-10-2026",
    "si_w9": "https://www.si.com/betting/road-to-272-bets-nfl-week-9-picks-for-every-game-01m6fe8ttb27",
    "yahoo_w9": "https://sports.yahoo.com/nfl/betting/article/week-9-nfl-picks-against-the-spread-predictions-for-every-game-bears-have-momentum-going-into-sunday-night-vs-buccaneers-151416985.html",
}
ROBOTS = ["https://www.cbssports.com/robots.txt", "https://bleacherreport.com/robots.txt", "https://www.covers.com/robots.txt",
          "https://www.si.com/robots.txt", "https://sports.yahoo.com/robots.txt"]
TEAMS = ["Bengals", "Falcons", "Lions", "Dolphins", "Saints", "Browns", "Giants", "Eagles", "Cowboys", "Colts", "Broncos",
         "Panthers", "Chiefs", "Jets", "Rams", "Commanders", "Texans", "Chargers", "Patriots", "Packers", "Seahawks",
         "Cardinals", "49ers", "Raiders", "Bears", "Buccaneers", "Bills", "Vikings", "Jaguars", "Ravens"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return f"{type(e).__name__}", b""


os.makedirs(OUT, exist_ok=True)
report = {}
for name, url in URLS.items():
    code, body = fetch(url)
    text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
    report[name] = {"url": url, "status": code, "bytes": len(body),
                    "team_mentions": sum(text.count(t) for t in TEAMS),
                    "pick_words": len(re.findall(r"\b(Pick|pick|ATS|against the spread|covers?)\b", text))}
    with open(os.path.join(OUT, f"{name}.html"), "wb") as f:
        f.write(body)
for url in ROBOTS:
    code, body = fetch(url)
    with open(os.path.join(OUT, "robots_" + url.split("/")[2].replace(".", "_") + ".txt"), "wb") as f:
        f.write(body)
    report["robots " + url.split("/")[2]] = {"status": code, "bytes": len(body)}
with open(os.path.join(OUT, "report.json"), "w") as f:
    json.dump(report, f, indent=1)
for k, v in report.items():
    print(k, v)
