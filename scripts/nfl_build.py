#!/usr/bin/env python3
"""Build and publish the NFL pregame board (docs/nfl/), optionally with one phone alert.

    python scripts/nfl_build.py [--alert] [--no-refresh]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import notify  # noqa: E402
from nfl import board  # noqa: E402

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")


def main() -> int:
    b = board.build(refresh="--no-refresh" not in sys.argv)
    s = board.write(b)
    if "--alert" in sys.argv and b.get("games"):
        today = datetime.now(ET).date().isoformat()
        todays = [g for g in b["games"] if g["day_et"] == today and g["state"] == "pre"]
        if not todays:
            print("No games left today; no alert.")
            return 0
        tracked = [g for g in todays if g["scoring"].get("tier") == "qualified"]
        watch = [g for g in todays if g["scoring"].get("tier") == "watch"]
        first = min(datetime.fromisoformat(g["kick_utc"]) for g in todays).astimezone(PT)
        title = (f"NFL Week {b['week']}: {len(tracked)} qualified today" if tracked
                 else f"NFL Week {b['week']}: none qualified today")
        names = ", ".join(f"{g['fav']} over {g['dog']}" for g in tracked[:3])
        body = (f"{len(todays)} games today, first kickoff {first:%-I:%M %p} PT" + (f" · {names}" if names else "")
                + (f" · Watch list: {len(watch)} on the board" if watch else ""))
        try:
            notify.send_alert(title, body, click=board.PAGES_URL + "nfl/",
                              priority="high" if tracked else "default", tags="football")
            print(f"Phone alert sent: {title} | {body}")
        except Exception as e:      # the page is published either way
            print(f"! phone alert not sent: {e}")
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
