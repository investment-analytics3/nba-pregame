#!/usr/bin/env python3
"""
NBA Pregame Dashboard - data source check.

Run this on GitHub Actions (the same servers the daily job will use) to confirm
every free data source responds and returns the fields the dashboard needs.

Uses only the Python standard library, so there is nothing to install.

    python scripts/check_sources.py            # normal run
    python scripts/check_sources.py --strict   # exit 1 if a REQUIRED source fails

Optional: set ODDS_API_KEY to also test The Odds API backup (costs 1 credit).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"

RESULTS: list[dict] = []


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def fetch(url: str, *, headers: dict | None = None, timeout: int = 20,
          retries: int = 2, want: str = "json", method: str = "GET"):
    """Return (status, body, response_headers). body is parsed JSON, bytes or None."""
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html, */*"}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read() if method == "GET" else b""
                body = raw
                if want == "json" and raw:
                    body = json.loads(raw.decode("utf-8"))
                return r.status, body, dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 401):
                return e.code, None, dict(e.headers or {})
            last_err = f"HTTP {e.code}"
        except Exception as e:  # timeouts, DNS, proxy refusals, bad JSON
            last_err = f"{type(e).__name__}: {e}"[:160]
        time.sleep(1.5 * (attempt + 1))
    return None, last_err, {}


def record(name: str, required: bool, ok: bool, detail: str, url: str = ""):
    RESULTS.append({"source": name, "required": required, "ok": ok,
                    "detail": detail, "url": url})
    mark = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"[{mark}] {name}: {detail}")


def ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_espn_teams():
    url = f"{ESPN_SITE}/teams"
    st, body, _ = fetch(url)
    if st != 200 or not isinstance(body, dict):
        return record("ESPN teams list", True, False, f"status {st} {body if st is None else ''}", url)
    try:
        teams = body["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        teams = []
    record("ESPN teams list", True, len(teams) == 30, f"{len(teams)} teams returned (need 30)", url)


def check_espn_scoreboard_and_odds(today: datetime):
    """Today's slate with spreads. If no games today, look ahead up to 3 days."""
    for add in range(4):
        d = today + timedelta(days=add)
        url = f"{ESPN_SITE}/scoreboard?dates={ymd(d)}"
        st, body, _ = fetch(url)
        if st != 200 or not isinstance(body, dict):
            record("ESPN scoreboard (schedule)", True, False, f"status {st} {body if st is None else ''}", url)
            record("ESPN spreads (odds in scoreboard)", True, False, "not tested: scoreboard failed", url)
            return
        events = body.get("events", [])
        if not events:
            continue
        record("ESPN scoreboard (schedule)", True, True,
               f"{len(events)} games on {d:%a %b %d}", url)
        with_spread, sample = 0, ""
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            odds = comp.get("odds") or []
            if odds:
                o = odds[0]
                if o.get("details") or o.get("spread") is not None:
                    with_spread += 1
                    if not sample:
                        sample = (f'e.g. "{o.get("details")}" spread={o.get("spread")} '
                                  f'provider={((o.get("provider") or {}).get("name"))}')
        ok = with_spread > 0
        record("ESPN spreads (odds in scoreboard)", True, ok,
               f"{with_spread}/{len(events)} games have a spread. {sample}".strip(), url)
        return
    record("ESPN scoreboard (schedule)", True, False, "no games found in the next 4 days", "")


def check_espn_boxscore(today: datetime):
    """Find a finished game in the last few days and confirm team box-score fields."""
    for back in range(1, 6):
        d = today - timedelta(days=back)
        url = f"{ESPN_SITE}/scoreboard?dates={ymd(d)}"
        st, body, _ = fetch(url)
        if st != 200 or not isinstance(body, dict):
            return record("ESPN box scores", True, False, f"scoreboard status {st}", url)
        done = [e for e in body.get("events", [])
                if (e.get("status", {}).get("type", {}).get("completed"))]
        if not done:
            continue
        ev = done[0]
        surl = f"{ESPN_SITE}/summary?event={ev['id']}"
        st2, summ, _ = fetch(surl)
        if st2 != 200 or not isinstance(summ, dict):
            return record("ESPN box scores", True, False, f"summary status {st2}", surl)
        teams = (summ.get("boxscore") or {}).get("teams") or []
        if len(teams) != 2:
            return record("ESPN box scores", True, False, "boxscore.teams missing", surl)
        names = [s.get("name", "") for s in teams[0].get("statistics", [])]
        needed = {
            "FG made-att": ["fieldGoalsMade-fieldGoalsAttempted"],
            "3P made-att": ["threePointFieldGoalsMade-threePointFieldGoalsAttempted"],
            "FT made-att": ["freeThrowsMade-freeThrowsAttempted"],
            "Off rebounds": ["offensiveRebounds"],
            "Turnovers": ["turnovers", "totalTurnovers"],
        }
        missing = [k for k, alts in needed.items() if not any(a in names for a in alts)]
        # starters flag lets us decide who is a "key starter" for the injury rule
        starters = 0
        for grp in (summ.get("boxscore") or {}).get("players", []) or []:
            for stat in grp.get("statistics", []) or []:
                starters += sum(1 for a in stat.get("athletes", []) or [] if a.get("starter"))
        detail = (f"{ev.get('shortName')} on {d:%b %d}: "
                  + ("all fields present" if not missing else f"MISSING {missing}")
                  + f"; starters flagged: {starters}"
                  + f"; stat names: {', '.join(names)}")
        return record("ESPN box scores", True, not missing, detail, surl)
    record("ESPN box scores", True, False, "no completed games in the last 5 days", "")


def check_espn_injuries():
    url = f"{ESPN_SITE}/injuries"
    st, body, _ = fetch(url)
    if st != 200 or not isinstance(body, dict):
        return record("ESPN injuries (backup)", False, False, f"status {st}", url)
    groups = body.get("injuries") or []
    n = sum(len(g.get("injuries", []) or []) for g in groups)
    record("ESPN injuries (backup)", False, len(groups) > 0,
           f"{len(groups)} teams, {n} players listed", url)


def check_espn_bpi(season_year: int):
    """ESPN BPI lives in a few places; report which one answers."""
    candidates = [
        f"https://site.web.api.espn.com/apis/fitt/v3/sports/basketball/nba/powerindex?region=us&lang=en",
        f"{ESPN_CORE}/seasons/{season_year}/powerindex?limit=40",
        f"{ESPN_CORE}/seasons/{season_year}/types/2/powerindex?limit=40",
    ]
    tried = []
    for url in candidates:
        st, body, _ = fetch(url, retries=1)
        tried.append(f"{st}")
        if st == 200 and isinstance(body, dict) and body:
            size = len(body.get("teams") or body.get("items") or body.get("categories") or [])
            if size:
                return record("ESPN BPI (power rank)", False, True,
                              f"answered with {size} entries", url)
    record("ESPN BPI (power rank)", False, False,
           f"no candidate URL returned data (statuses {', '.join(tried)}); "
           "consensus will use own net rating + NBA.com", candidates[0])


def check_nba_injury_pdf(now_pt: datetime):
    """Official report: newest snapshot within the last 24h. Two URL styles seen."""
    base = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
    now_et = now_pt.astimezone(ET)
    t = now_et.replace(minute=(now_et.minute // 15) * 15, second=0, microsecond=0)
    tries = 0
    for _ in range(96):  # 24 hours of 15-minute slots
        date = t.strftime("%Y-%m-%d")
        hh = t.strftime("%I")
        ampm = t.strftime("%p")
        urls = [f"{base}{date}_{hh}_{t:%M}{ampm}.pdf"]
        if t.minute == 0:
            urls.append(f"{base}{date}_{hh}{ampm}.pdf")
        for url in urls:
            tries += 1
            st, body, hdr = fetch(url, want="bytes", retries=0, timeout=15)
            if st == 200 and isinstance(body, (bytes, bytearray)) and body[:4] == b"%PDF":
                return record("NBA official injury report (PDF)", True, True,
                              f"latest snapshot {t:%a %b %d %I:%M %p} ET, {len(body)//1024} KB "
                              f"(found after {tries} tries)", url)
            if st is None and tries <= 2:
                # network-level failure (blocked or down) - no point walking back 24h
                return record("NBA official injury report (PDF)", True, False,
                              f"could not connect: {body}", url)
        t -= timedelta(minutes=15)
    record("NBA official injury report (PDF)", True, False,
           f"no snapshot found in the last 24h ({tries} URLs tried). "
           "Off-days have no report; rerun on a game day.", base)


def check_nba_power_rankings():
    candidates = [
        "https://www.nba.com/news/category/power-rankings",
        "https://www.nba.com/news/power-rankings",
    ]
    for url in candidates:
        st, body, _ = fetch(url, want="bytes", retries=1)
        if st == 200 and body and b"ower" in body:
            return record("NBA.com power rankings (weekly, optional)", False, True,
                          f"page reachable ({len(body)//1024} KB)", url)
    record("NBA.com power rankings (weekly, optional)", False, False,
           "page not reachable from this server; consensus will use the two daily sources",
           candidates[0])


def check_odds_api():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds?regions=us&markets=spreads&oddsFormat=american"
    if not key:
        return record("The Odds API (spread backup)", False, False,
                      "skipped: no ODDS_API_KEY secret set (optional)", url)
    st, body, hdr = fetch(url + f"&apiKey={key}", retries=1)
    if st != 200 or not isinstance(body, list):
        return record("The Odds API (spread backup)", False, False, f"status {st}", url)
    with_spread = sum(1 for ev in body if any(
        m.get("key") == "spreads" for b in ev.get("bookmakers", []) for m in b.get("markets", [])))
    left = hdr.get("x-requests-remaining") or hdr.get("X-Requests-Remaining")
    record("The Odds API (spread backup)", False, with_spread > 0,
           f"{with_spread}/{len(body)} games with spreads; credits left this month: {left}", url)


def check_nba_stats_api():
    """Expected to fail from cloud servers - recorded for information only."""
    url = ("https://stats.nba.com/stats/leaguestandingsv3?LeagueID=00&Season=2026-27"
           "&SeasonType=Regular%20Season")
    hdr = {"Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
           "x-nba-stats-origin": "stats", "x-nba-stats-token": "true"}
    st, body, _ = fetch(url, headers=hdr, retries=0, timeout=12)
    record("stats.nba.com (not used; info only)", False, st == 200,
           "reachable" if st == 200 else f"no response ({body if st is None else st}) - expected from cloud servers",
           url)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def write_report(now_pt: datetime):
    req_fail = [r for r in RESULTS if r["required"] and not r["ok"]]
    lines = [
        f"# NBA data source check - {now_pt:%a %b %d %Y, %I:%M %p} PT",
        "",
        ("**Result: all required sources passed.**" if not req_fail
         else f"**Result: {len(req_fail)} required source(s) failed.**"),
        "",
        "| Status | Source | Required | Detail |",
        "|---|---|---|---|",
    ]
    for r in RESULTS:
        status = "PASS" if r["ok"] else ("FAIL" if r["required"] else "WARN")
        detail = r["detail"].replace("|", "/")
        lines.append(f"| {status} | {r['source']} | {'yes' if r['required'] else 'no'} | {detail} |")
    lines += ["", "Runner: " + os.environ.get("RUNNER_NAME", "local") + " / "
              + os.environ.get("RUNNER_OS", sys.platform)]
    md = "\n".join(lines) + "\n"
    os.makedirs("reports", exist_ok=True)
    with open("reports/source_check.md", "w") as f:
        f.write(md)
    with open("reports/source_check.json", "w") as f:
        json.dump({"checked_at": now_pt.isoformat(), "results": RESULTS}, f, indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    return req_fail


def main() -> int:
    now_pt = datetime.now(PT)
    today = now_pt.replace(hour=12, minute=0, second=0, microsecond=0)
    # NBA season label year: Oct-Dec belong to next calendar year in ESPN's season ids
    season_year = now_pt.year + 1 if now_pt.month >= 8 else now_pt.year

    print(f"Checking NBA data sources at {now_pt:%Y-%m-%d %H:%M} PT\n")
    check_espn_teams()
    check_espn_scoreboard_and_odds(today)
    check_espn_boxscore(today)
    check_nba_injury_pdf(now_pt)
    check_espn_injuries()
    check_espn_bpi(season_year)
    check_nba_power_rankings()
    check_odds_api()
    check_nba_stats_api()

    req_fail = write_report(now_pt)
    print(f"\nReport written to reports/source_check.md "
          f"({'all required passed' if not req_fail else f'{len(req_fail)} required failed'})")
    return 1 if (req_fail and "--strict" in sys.argv) else 0


if __name__ == "__main__":
    sys.exit(main())
