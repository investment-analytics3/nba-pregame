#!/usr/bin/env python3
"""NHL tick: decides what is due (no network, no pandas; reads data/nhl/).

Started by the NBA Scheduler's 15-minute tick (it dispatches nhl.yml). Every job is safe to repeat.

  build   daily board (owner Nov 12, as the NBA):
            no board yet, or the board is for an earlier day: now
            an unlocked game starts within 75 minutes: every 15 minutes (its tier locks 1 hour before)
            game day: hourly from 6 AM PT to the last tip, then hourly until the board rolls to tomorrow
            (the build switches to tomorrow's preview once today's games are final)
            otherwise: morning (from 7 AM PT) and evening (from 6 PM PT)
  alert   the first build at or after board time (weekdays 1 PM PT, after most starting goalies are posted;
          weekends 9 AM PT, before the matinees) sends one phone push, only when a game today is Qualified
  live    a tracked game (Qualified or Watch list) starts within 15 minutes or is on: start the logger

    python scripts/nhl_scheduler.py decide [--force-build]
    python scripts/nhl_scheduler.py mark built|alerted
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "nhl")
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
HOURLY = timedelta(minutes=55)
SOON = timedelta(minutes=75)
LIVE_BEFORE = timedelta(minutes=15)
LIVE_AFTER = timedelta(hours=3)
GAME_LEN = timedelta(hours=2, minutes=30)


def read(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def board_hour(now_pt: datetime) -> int:
    return 9 if now_pt.weekday() >= 5 else 13


def decide(now: datetime, ctx: dict | None, state: dict, force: bool = False) -> dict:
    now_pt, today_et = now.astimezone(PT), now.astimezone(ET).date().isoformat()
    games = (ctx or {}).get("games", [])
    day = (ctx or {}).get("date")
    tips = [datetime.fromisoformat(g["tip_utc"]) for g in games]
    last = datetime.fromisoformat(state["last_build"]) if state.get("last_build") else None
    since = (now - last) if last else None
    hourly = since is None or since >= HOURLY
    out = {"build": False, "alert": False, "live": False, "why": ""}
    slot = now_pt.replace(hour=18 if now_pt.hour >= 18 else 7, minute=0, second=0, microsecond=0)
    if force:
        out["build"], out["why"] = True, "forced"
    elif not ctx or not day:
        out["build"], out["why"] = hourly, "no board yet"
    elif day < today_et:
        out["build"], out["why"] = (since is None or since >= timedelta(minutes=14)), "board is for an earlier day"
    elif any(timedelta(0) < datetime.fromisoformat(g["tip_utc"]) - now <= SOON and not g.get("locked") for g in games) \
            and (since is None or since >= timedelta(minutes=14)):
        out["build"], out["why"] = True, "puck drop within 75 min: lock tiers"
    elif day == today_et and now_pt.hour >= 6 and hourly:
        out["build"], out["why"] = True, ("game day refresh" if tips and now < max(tips) else "waiting to roll to tomorrow")
    elif day > today_et and now_pt.hour >= 7 and (last is None or last.astimezone(PT) < slot):
        out["build"], out["why"] = True, "morning/evening refresh (preview)"
    if out["build"] and state.get("alerted") != today_et and now_pt.hour >= board_hour(now_pt) and day == today_et \
            and tips and now < max(tips):
        out["alert"] = True
    out["live"] = any(g.get("tracked") and datetime.fromisoformat(g["tip_utc"]) - LIVE_BEFORE <= now
                      <= datetime.fromisoformat(g["tip_utc"]) + LIVE_AFTER for g in games)
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
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "state.json"), "w") as f:
            json.dump(state, f, indent=1)
        return 0
    d = decide(now, read("context.json", None), state, force="--force-build" in sys.argv)
    print(f"{now.astimezone(PT):%a %b %-d %-I:%M %p} PT  build={d['build']} ({d['why']}) alert={d['alert']} live={d['live']}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            for k in ("build", "alert", "live"):
                f.write(f"{k}={'true' if d[k] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
