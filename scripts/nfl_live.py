#!/usr/bin/env python3
"""NFL checkpoint logger (shadow mode: logs, never alerts, never bets).

While NFL games are on, every ~60 s it reads ESPN's scoreboard and, for each game in
this week's board (data/nfl/context.json), records four checkpoints once each:
    half   at halftime
    q3     at the end of the 3rd quarter
    q4_10  4th quarter 10:00 (end of the primary window)
    q4_6   4th quarter 6:00 (end of the confirm window)
Each record also carries the live engine's grade (docs/nfl/engine.js, run with Node) so the
4th-quarter grading can be reviewed against results before it is ever used for a bet.
Every poll it also publishes the live Kalshi prices to the "nfl-live-data" branch
(prices.json) for the Live page, which can't call Kalshi from a browser.
Each record holds the score, the spread favorite's lead, the favorite's real Kalshi
price at that moment, ESPN's win probability, and a preliminary early-path read
(lead 10+ and both defensive metrics top 10). Control and the QB edge are graded the
next day from nflverse play-by-play (scripts/nfl_grade.py), with the same rules as the
backtest. If a poll misses the exact moment, the first poll after it is used and the
record is marked "late" with the game clock.

Writes data/nfl/live/<season>.json and docs/nfl/live.json, committed to main.

    python scripts/nfl_live.py [--max-minutes 330] [--once] [--no-push]
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba.http import FetchError  # noqa: E402
from nfl import espn, kalshi  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
CTX = os.path.join(ROOT, "data", "nfl", "context.json")
POLL = 60


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def log_path(season: int) -> str:
    return os.path.join(ROOT, "data", "nfl", "live", f"{season}.json")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(season: int, book: dict):
    os.makedirs(os.path.dirname(log_path(season)), exist_ok=True)
    body = json.dumps(book, indent=1)
    with open(log_path(season), "w") as f:
        f.write(body)
    os.makedirs(os.path.join(ROOT, "docs", "nfl"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "nfl", "live.json"), "w") as f:
        f.write(body)


def push(msg: str):
    if "--no-push" in sys.argv:
        return
    try:
        run = lambda *a: subprocess.run(a, cwd=ROOT, check=True, capture_output=True)  # noqa: E731
        run("git", "add", "data/nfl/live", "docs/nfl/live.json")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
            return
        run("git", "-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-q", "-m", msg)
        for _ in range(3):
            if subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=ROOT).returncode == 0 and \
               subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
                return
            time.sleep(5)
        print("! push failed; will retry with the next checkpoint")
    except subprocess.CalledProcessError as e:
        print(f"! git: {e.stderr.decode()[:200]}")


def engine_grade(summ: dict, g: dict) -> dict | None:
    """The live engine's grade for this moment (Node); None if Node or the engine fails."""
    if not summ:
        return None
    ctx = {"fav": g.get("fav_abbr"), "dog": g.get("dog_abbr"), "fav_defense_top10": g.get("fav_defense_top10"),
           "qbs": g.get("qbs") or {}, "date": g.get("day_et")}
    try:
        with tempfile.TemporaryDirectory() as d:
            sp, cp = os.path.join(d, "s.json"), os.path.join(d, "c.json")
            with open(sp, "w") as f:
                json.dump(summ, f)
            with open(cp, "w") as f:
                json.dump(ctx, f)
            out = subprocess.run(["node", os.path.join(ROOT, "scripts", "nfl_engine_cli.js"), sp, cp],
                                 capture_output=True, timeout=60, check=True)
            return json.loads(out.stdout)
    except Exception as e:      # the checkpoint is still logged without a grade
        print(f"! engine: {type(e).__name__}: {str(e)[:120]}")
        return None


def snapshot(g: dict, ev: dict, kind: str, late: bool) -> dict:
    fav_home = g["fav"] == g["home"]
    hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
    lead = (hs - as_) if fav_home else (as_ - hs)
    rec = {"at": datetime.now(PT).isoformat(timespec="seconds"), "late": late,
           "clock": f"Q{ev.get('period')} {ev.get('clock') or ''}".strip(), "status": ev.get("status"),
           "score": f"{g['away']} {as_} - {g['home']} {hs}", "lead": lead, "up10": lead >= 10,
           "fav_defense_top10": bool(g.get("fav_defense_top10")),
           "prelim": "lead 10+ and top-10 defense" if (lead >= 10 and g.get("fav_defense_top10")) else
                     ("lead 10+, defense not top 10" if lead >= 10 else "lead under 10"),
           "espn_wp": None}
    try:
        summ = espn.summary(ev["id"])
    except FetchError:
        summ = None
    wp = (summ or {}).get("winprobability") or []
    if wp:
        try:
            h = float(wp[-1]["homeWinPercentage"])
            rec["espn_wp"] = round(h if fav_home else 1 - h, 3)
        except (KeyError, TypeError, ValueError):
            pass
    rec["engine"] = engine_grade(summ, g)
    day = datetime.fromisoformat(g["kick_utc"]).astimezone(ET).date()
    k = kalshi.game_prices(day, g["kalshi_away"], g["kalshi_home"])
    fk = g["kalshi_home"] if fav_home else g["kalshi_away"]
    if k and k.get(fk):
        rec["kalshi"] = {"event": k["event"], **k[fk]}
    return rec


def due_checkpoints(ev: dict, entry: dict) -> list[tuple[str, bool]]:
    """Which checkpoints to record now: (kind, late)."""
    out = []
    st, per = ev.get("status") or "", ev.get("period") or 0
    if ev.get("state") != "in":
        return out                       # a missed checkpoint stays missing; never back-filled from a final
    if "half" not in entry:
        if st == "STATUS_HALFTIME":
            out.append(("half", False))
        elif per == 3:
            out.append(("half", True))
    if "q3" not in entry:
        if per == 3 and st == "STATUS_END_PERIOD":
            out.append(("q3", False))
        elif per == 4:
            out.append(("q3", True))
    clock = _secs(ev.get("clock"))
    if per == 4 and clock is not None:
        if "q4_10" not in entry and clock <= 600:
            out.append(("q4_10", clock < 540))
        if "q4_6" not in entry and clock <= 360:
            out.append(("q4_6", clock < 300))
    return out


def _secs(c):
    try:
        m, s = str(c).split(":")
        return int(m) * 60 + int(float(s))
    except (TypeError, ValueError):
        return None


_published = None


def publish_prices(season: int, games: dict, events: list):
    """Force-push live Kalshi prices to the nfl-live-data branch (one file, one commit)."""
    global _published
    token = os.environ.get("GH_TOKEN")
    out = {"updated": datetime.now(PT).isoformat(timespec="seconds"), "games": {}}
    for ev in events:
        g = games.get(ev["id"])
        if not g or ev["state"] != "in":
            continue
        day = datetime.fromisoformat(g["kick_utc"]).astimezone(ET).date()
        k = kalshi.game_prices(day, g["kalshi_away"], g["kalshi_home"])
        if not k:
            continue
        pr = {}
        for code, abbr in ((g["kalshi_away"], g.get("away_abbr")), (g["kalshi_home"], g.get("home_abbr"))):
            if abbr and (k.get(code) or {}).get("prob") is not None:
                pr[abbr] = k[code]["prob"]
        out["games"][ev["id"]] = {"event": k["event"], "prices": pr}
    body = json.dumps(out["games"], sort_keys=True)
    if not token or body == _published or "--no-push" in sys.argv:
        return
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "prices.json"), "w") as f:
            json.dump(out, f, indent=1)
        try:
            g = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)  # noqa: E731
            g("init", "-q", "-b", "nfl-live-data")
            g("add", "prices.json")
            g("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
              "commit", "-q", "-m", f"prices {out['updated']}")
            repo = os.environ.get("GITHUB_REPOSITORY", "investment-analytics3/nba-pregame")
            g("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "HEAD:nfl-live-data")
            _published = body
        except subprocess.CalledProcessError as e:
            print(f"! price publish failed: {e.stderr.decode()[:150]}")


def main() -> int:
    ctx = load(CTX, None)
    if not ctx or not ctx.get("games"):
        print("No NFL context (build the board first).")
        return 0
    season = ctx["season"]
    games = {g["id"]: g for g in ctx["games"] if g.get("fav")}
    book = load(log_path(season), {"season": season, "games": {}})
    stop = time.time() + 60 * float(arg("--max-minutes", 330))
    while True:
        now = datetime.now(timezone.utc)
        days = {now.astimezone(ET).date()}
        if now.astimezone(ET).hour < 4:
            days.add(now.astimezone(ET).date() - timedelta(days=1))
        events = []
        for d in days:
            try:
                events += espn.parse_events(espn.scoreboard_date(f"{d:%Y%m%d}"))
            except FetchError as e:
                print(f"ESPN unavailable: {e}")
        changed = []
        for ev in events:
            g = games.get(ev["id"])
            if not g or ev["state"] == "pre":
                continue
            entry = book["games"].setdefault(ev["id"], {
                "week": ctx["week"], "nflverse_id": g["nflverse_id"], "day_et": g["day_et"], "away": g["away"],
                "home": g["home"], "fav": g["fav"], "dog": g["dog"], "spread": g["spread"], "tracked": g["tracked"]})
            for kind, late in due_checkpoints(ev, entry):
                entry[kind] = snapshot(g, ev, kind, late)
                changed.append(f"{g['away']}@{g['home']} {kind}")
                print(f"{datetime.now(PT):%-I:%M %p} {g['away']}@{g['home']} {kind}{' (late)' if late else ''}: "
                      f"{entry[kind]['score']} lead {entry[kind]['lead']} kalshi {(entry[kind].get('kalshi') or {}).get('prob')}")
            if ev["state"] == "post" and "final" not in entry:
                hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
                margin = (hs - as_) if g["fav"] == g["home"] else (as_ - hs)
                entry["final"] = {"score": f"{g['away']} {as_} - {g['home']} {hs}", "fav_margin": margin, "fav_won": margin > 0}
                changed.append(f"{g['away']}@{g['home']} final")
        publish_prices(season, games, events)
        if changed:
            book["updated"] = datetime.now(PT).isoformat(timespec="seconds")
            save(season, book)
            push("NFL checkpoints: " + ", ".join(changed)[:180])
        live = [e for e in events if e["id"] in games and e["state"] == "in"]
        upcoming = [g for g in games.values()
                    if timedelta(0) < datetime.fromisoformat(g["kick_utc"]) - now < timedelta(minutes=40)]
        if "--once" in sys.argv or time.time() > stop or (not live and not upcoming):
            print(f"Stopping: {len(live)} live, {len(upcoming)} kicking off within 40 min.")
            return 0
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
