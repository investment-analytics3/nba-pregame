#!/usr/bin/env python3
"""NFL tick: decides what is due (no network, no pandas; reads data/nfl/).

Started by the NBA Scheduler's 15-minute tick (it dispatches nfl.yml), so the NFL never
needs its own clock. Every job is safe to repeat.

  build   rebuild the board:
            game day (any kickoff today, Eastern date): hourly from 6 AM PT until the last kickoff
            other days: once a day from 7 AM PT (picks up nflverse's overnight update)
            no board yet, or this week's games are all done: now
  alert   the first build on a game day sends one phone alert
  grade   once a day from 3 AM PT: grade logged checkpoints from nflverse
  live    a game kicks off within 15 minutes or is on (kickoff to +4.5 h): start the logger

    python scripts/nfl_scheduler.py decide [--force-build]
    python scripts/nfl_scheduler.py mark built|alerted|graded
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "nfl")
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
BUILD_EVERY = timedelta(minutes=55)
LIVE_BEFORE = timedelta(minutes=15)
LIVE_AFTER = timedelta(hours=4, minutes=30)


def read(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def decide(now: datetime, ctx: dict | None, state: dict, force: bool = False) -> dict:
    now_pt, today_et = now.astimezone(PT), now.astimezone(ET).date().isoformat()
    kicks = [datetime.fromisoformat(g["kick_utc"]) for g in (ctx or {}).get("games", [])]
    today_kicks = [k for k in kicks if k.astimezone(ET).date().isoformat() == today_et]
    last = datetime.fromisoformat(state["last_build"]) if state.get("last_build") else None
    out = {"build": False, "alert": False, "grade": False, "live": False, "why": ""}

    if force:
        out["build"], out["why"] = True, "forced"
    elif not ctx or not kicks:
        out["build"], out["why"] = (last is None or now - last >= BUILD_EVERY), "no board yet"
    elif all(now > k + LIVE_AFTER for k in kicks) and (last is None or now - last >= BUILD_EVERY):
        out["build"], out["why"] = True, "week done: roll to next week"
    elif today_kicks:
        if now_pt.hour >= 6 and now < max(today_kicks) and (last is None or now - last >= BUILD_EVERY):
            out["build"], out["why"] = True, "game day refresh"
            out["alert"] = state.get("alerted") != today_et
    elif now_pt.hour >= 7 and (last is None or last.astimezone(PT).date() != now_pt.date()):
        out["build"], out["why"] = True, "daily refresh"

    out["grade"] = now_pt.hour >= 3 and state.get("graded") != now_pt.date().isoformat()
    out["live"] = any(k - LIVE_BEFORE <= now <= k + LIVE_AFTER for k in kicks)
    return out


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "decide"
    state = read("state.json", {})
    now = datetime.now(timezone.utc)
    if cmd == "mark":
        what = sys.argv[2]
        if what == "built":
            state["last_build"] = now.isoformat(timespec="seconds")
        elif what == "alerted":
            state["alerted"] = now.astimezone(ET).date().isoformat()
        elif what == "graded":
            state["graded"] = now.astimezone(PT).date().isoformat()
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "state.json"), "w") as f:
            json.dump(state, f, indent=1)
        return 0
    d = decide(now, read("context.json", None), state, force="--force-build" in sys.argv)
    print(f"{now.astimezone(PT):%a %b %-d %-I:%M %p} PT  build={d['build']} ({d['why']}) alert={d['alert']} "
          f"grade={d['grade']} live={d['live']}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            for k in ("build", "alert", "grade", "live"):
                f.write(f"{k}={'true' if d[k] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
