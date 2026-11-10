#!/usr/bin/env python3
"""The Scheduler: decides what is due on every 15-minute tick.

The tick is started by an outside clock (cron-job.org calls GitHub's
"workflow_dispatch" API) with GitHub's own cron as a backup. Nothing here
depends on running at an exact time: every job catches up if a tick is missed,
and every job is safe to repeat.

  nightly  the overnight data update; due once per Pacific day from 3 AM on
  board    "board" / "final" / "none", from scripts/board_decide.py (each is
           sent at most once a day, so repeated ticks never double-send)
  live     true when an above-the-line game is in progress (or tips within
           15 minutes) and the live tracker should be running

    python scripts/scheduler.py decide [--dry-run]
    python scripts/scheduler.py mark-nightly
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import espn, store  # noqa: E402
from nba.http import FetchError  # noqa: E402
from scripts.board_decide import decide as board_decide  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
NIGHTLY_HOUR = 3            # Pacific
LIVE_LEAD = timedelta(minutes=15)


def nightly_due(now_pt: datetime, state: dict) -> bool:
    return now_pt.hour >= NIGHTLY_HOUR and state.get("nightly") != now_pt.date().isoformat()


def tracked_ids(day_et: date) -> set[str]:
    """Above-the-line games on a day's slate (empty if no slate yet)."""
    slate = store.read_json(f"slates/{day_et.isoformat()}.json", {}) or {}
    sc = lambda g: g.get("scoring") or {}
    return {g["id"] for g in slate.get("games", []) if sc(g).get("tracked", sc(g).get("qualified"))}


def live_due(now: datetime, events: list[dict], tracked: set[str]) -> bool:
    for e in events:
        if e["id"] not in tracked or e["completed"]:
            continue
        if e["state"] == "in":
            return True
        tip = datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00"))
        if e["state"] == "pre" and tip - LIVE_LEAD <= now:
            return True
    return False


def decide(dry_run: bool = False) -> dict:
    now = datetime.now(PT)
    today_et = datetime.now(ET).date()
    state = store.read_json("state.json", {}) or {}
    out = {"nightly": nightly_due(now, state), "board": "none", "live": False, "note": ""}

    try:
        events = [e for e in espn.parse_events(espn.scoreboard(today_et))
                  if e["season_type"] == espn.REGULAR_SEASON]
    except FetchError as ex:
        # A blip at ESPN: skip the board/live checks this tick, try again next tick.
        out["note"] = f"ESPN scoreboard unavailable ({ex}); board/live checked next tick"
        return out

    tips = [datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00")) for e in events]
    log_today = (store.read_json("board_log.json", {}) or {}).get(today_et.isoformat(), {})
    out["board"], why = board_decide(now, tips, log_today, today_et)
    out["note"] = why

    live = live_due(now, events, tracked_ids(today_et))
    # Late games run past midnight Eastern: also look at yesterday's slate.
    if not live and datetime.now(ET).hour < 3:
        y = today_et - timedelta(days=1)
        try:
            y_events = espn.parse_events(espn.scoreboard(y))
            live = live_due(now, y_events, tracked_ids(y))
        except FetchError:
            pass
    out["live"] = live

    if dry_run:
        out["note"] = f"dry run (would do: nightly={out['nightly']} board={out['board']} live={live}); {out['note']}"
        out.update(nightly=False, board="none", live=False)
    return out


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "decide"
    if cmd == "mark-nightly":
        state = store.read_json("state.json", {}) or {}
        state["nightly"] = datetime.now(PT).date().isoformat()
        store.write_json("state.json", state)
        print(f"nightly update recorded for {state['nightly']}")
        return 0
    if cmd != "decide":
        print(__doc__)
        return 2

    d = decide(dry_run="--dry-run" in sys.argv)
    now = datetime.now(PT)
    print(f"{now:%a %b %-d %-I:%M %p} PT  nightly={d['nightly']}  board={d['board']}  "
          f"live={d['live']}  ({d['note']})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"nightly={'true' if d['nightly'] else 'false'}\n")
            f.write(f"board={d['board']}\n")
            f.write(f"live={'true' if d['live'] else 'false'}\n")
            f.write(f"summary=nightly={d['nightly']} board={d['board']} live={d['live']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
