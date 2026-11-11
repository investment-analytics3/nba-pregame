#!/usr/bin/env python3
"""Build and publish the college football board (docs/cfb/), optionally with one phone push.

    python scripts/cfb_build.py [--alert] [--no-refresh]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import notify  # noqa: E402
from cfb import board  # noqa: E402

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")


def main() -> int:
    b = board.build(refresh="--no-refresh" not in sys.argv)
    s = board.write(b)
    if "--alert" in sys.argv and b.get("games"):
        today = datetime.now(ET).date().isoformat()
        todays = [g for g in b["games"] if g["day_et"] == today and g["state"] == "pre"]
        if not todays:
            print("No in-range games left today; no push.")
            return 0
        q = [g for g in todays if g["scoring"]["tier"] == "qualified"]
        w = [g for g in todays if g["scoring"]["tier"] == "watch"]
        first = min(datetime.fromisoformat(g["kick_utc"]) for g in todays).astimezone(PT)
        name = lambda g: f"{(g['home'] if g['fav'] == g['home']['code'] else g['away'])['abbr']} over {(g['away'] if g['fav'] == g['home']['code'] else g['home'])['abbr']}"  # noqa: E731
        title = f"College Week {b['week']}: {len(q)} qualified today" if q else f"College Week {b['week']}: none qualified today"
        body = (f"{len(todays)} games at -10 to -20, first kickoff {first:%-I:%M %p} PT"
                + (f" · {', '.join(name(g) for g in q[:3])}" if q else "") + (f" · Watch list: {len(w)}" if w else ""))
        try:
            notify.send_alert(title, body, click=board.PAGES_URL + "cfb/", priority="high" if q else "default", tags="football")
            print(f"Phone push sent: {title} | {body}")
        except Exception as e:      # the page is published either way
            print(f"! phone push not sent: {e}")
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
