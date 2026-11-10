#!/usr/bin/env python3
"""Decide whether this run should send a board, and which one.

Schedule (Pacific time):
  board  12:00 PM weekdays, 10:00 AM Sat/Sun
  final  90 minutes before the day's first tip
Rules:
  - If the final time comes before the board time, or within 45 minutes after
    it, only the final is sent (one email).
  - Each is sent at most once a day (tracked in data/board_log.json).
  - No games: nothing is sent.
  - Runs are late sometimes; a missed send goes out on the next run, as long as
    a game hasn't started yet.

Prints the decision and writes kind=board|final|none to $GITHUB_OUTPUT.
    python scripts/board_decide.py [--kind auto|board|final|preview]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import espn, store  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
MERGE_WINDOW = timedelta(minutes=45)


def decide(now: datetime, tips: list[datetime], log_today: dict, day=None) -> tuple[str, str]:
    """`day` = the slate date (ET game date) the log entry belongs to. The board time is on that
    date: from 9 PM PT the ET date is already tomorrow, and without this the next day's board
    went out at 9 PM the night before (Nov 9 and 10, 2026)."""
    if not tips:
        return "none", "no games today"
    first_tip = min(tips)
    day = day or now.astimezone(PT).date()
    board_t = datetime.combine(day, time(10 if day.weekday() >= 5 else 12, 0), PT)
    final_t = first_tip - timedelta(minutes=90)
    sent_final, sent_board = "final" in log_today, "board" in log_today
    if sent_final:
        return "none", "final already sent"
    if now >= first_tip:
        if now < max(tips):
            return "final", "late: first game started but others haven't, sending the final now"
        return "none", "all games have started"
    merged = final_t <= board_t + MERGE_WINDOW
    if now >= final_t:
        return "final", "90 minutes before first tip"
    if not merged and not sent_board and now >= board_t:
        return "board", "scheduled board time"
    nxt = final_t if (merged or sent_board) else board_t
    return "none", f"next send at {nxt.astimezone(PT):%-I:%M %p} PT ({'final' if nxt == final_t else 'board'})"


def main() -> int:
    forced = "auto"
    if "--kind" in sys.argv:
        forced = sys.argv[sys.argv.index("--kind") + 1] or "auto"
    now = datetime.now(PT)
    today = datetime.now(ET).date()
    if forced in ("board", "final", "preview"):
        kind, why = forced, "requested by hand"
    else:
        events = [e for e in espn.parse_events(espn.scoreboard(today))
                  if e["season_type"] == espn.REGULAR_SEASON]
        tips = [datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00")) for e in events
                if e["state"] == "pre" or e["completed"] or e["state"] == "in"]
        log_today = (store.read_json("board_log.json", {}) or {}).get(today.isoformat(), {})
        kind, why = decide(now, tips, log_today, today)
    print(f"{now:%a %-I:%M %p} PT: {kind} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"kind={kind}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
