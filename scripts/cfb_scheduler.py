#!/usr/bin/env python3
"""College football tick: decides what is due (no network, no pandas; reads data/cfb/).

Started by the NBA Scheduler's 15-minute tick (it dispatches cfb.yml). Every job is safe to repeat.

  build   weekly board (owner Nov 10):
            no board yet, or this week is done: now (hourly at most)
            game day (an in-range game kicks off today, Eastern date): hourly from 6 AM PT to the last kickoff
            an unlocked game kicks off within 75 minutes: every 15 minutes (its tier locks 1 hour before)
            other days: morning (from 7 AM PT) and evening (from 6 PM PT)
  alert   the first build on a game day sends one phone push (Qualified games today)
  live    a tracked game (Qualified or Watch list) kicks off within 15 minutes or is on: start the logger

    python scripts/cfb_scheduler.py decide [--force-build]
    python scripts/cfb_scheduler.py mark built|alerted
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "cfb")
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
HOURLY = timedelta(minutes=55)
SOON = timedelta(minutes=75)
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
    games = (ctx or {}).get("games", [])
    kicks = [datetime.fromisoformat(g["kick_utc"]) for g in games]
    today = [k for k in kicks if k.astimezone(ET).date().isoformat() == today_et]
    last = datetime.fromisoformat(state["last_build"]) if state.get("last_build") else None
    since = (now - last) if last else None
    out = {"build": False, "alert": False, "live": False, "why": ""}
    slot = now_pt.replace(hour=18 if now_pt.hour >= 18 else 7, minute=0, second=0, microsecond=0)
    if force:
        out["build"], out["why"] = True, "forced"
    elif not games:
        out["build"], out["why"] = (since is None or since >= HOURLY), "no board yet"
    elif all(now > k + LIVE_AFTER for k in kicks) and (since is None or since >= HOURLY):
        out["build"], out["why"] = True, "week done: roll to next week"
    elif any(timedelta(0) < datetime.fromisoformat(g["kick_utc"]) - now <= SOON and not g.get("locked") for g in games) \
            and (since is None or since >= timedelta(minutes=14)):
        out["build"], out["why"] = True, "kickoff within 75 min: lock tiers"
    elif today and now_pt.hour >= 6 and now < max(today) and (since is None or since >= HOURLY):
        out["build"], out["why"] = True, "game day refresh"
    elif not today and now_pt.hour >= 7 and (last is None or last.astimezone(PT) < slot):
        out["build"], out["why"] = True, "morning/evening refresh"
    if out["build"] and today and now_pt.hour >= 6:
        out["alert"] = state.get("alerted") != today_et
    out["live"] = any(g.get("tracked") and datetime.fromisoformat(g["kick_utc"]) - LIVE_BEFORE <= now
                      <= datetime.fromisoformat(g["kick_utc"]) + LIVE_AFTER for g in games)
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
