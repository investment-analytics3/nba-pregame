#!/usr/bin/env python3
"""NFL checkpoint logger (shadow mode: logs, never alerts, never bets).

While NFL games are on, every ~60 s it reads ESPN's scoreboard and, for each game in
this week's board (data/nfl/context.json), records two checkpoints once each:
    half  at halftime
    q3    at the end of the 3rd quarter
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


def win_prob(event_id: str, fav_home: bool):
    try:
        wp = (espn.summary(event_id).get("winprobability") or [])
        if wp:
            h = float(wp[-1]["homeWinPercentage"])
            return round(h if fav_home else 1 - h, 3)
    except (FetchError, KeyError, TypeError, ValueError):
        pass
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
           "espn_wp": win_prob(ev["id"], fav_home)}
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
    return out


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
