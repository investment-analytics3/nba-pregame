#!/usr/bin/env python3
"""Build and publish the NHL board (docs/nhl/), optionally with one phone push.

    python scripts/nhl_build.py [--alert] [--no-refresh] [--date YYYY-MM-DD]

--alert sends the day's push (scheduler: weekdays from 1 PM PT, weekends from 9 AM PT) only when at
least one game today is Qualified.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import notify  # noqa: E402
from nhl import board  # noqa: E402

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")


def main() -> int:
    day = date.fromisoformat(sys.argv[sys.argv.index("--date") + 1]) if "--date" in sys.argv else None
    b = board.build(refresh="--no-refresh" not in sys.argv, day=day)
    s = board.write(b)
    if "--alert" in sys.argv:
        today = datetime.now(ET).date().isoformat()
        todays = [g for g in b["games"] if g["day_et"] == today and g["state"] == "pre"]
        q = [g for g in todays if g["scoring"]["tier"] == "qualified"]
        if not q:
            print(f"No Qualified games today ({len(todays)} in range); no push.")
            print(s)
            return 0
        w = [g for g in todays if g["scoring"]["tier"] == "watch"]
        first = min(datetime.fromisoformat(g["tip_utc"]) for g in q).astimezone(PT)
        def name(g):
            f = g[g["favorite"]]
            gl = (f.get("goalie") or {}).get("name")
            return f"{f['abbr']} over {g['away' if g['favorite'] == 'home' else 'home']['abbr']}" + (f" ({gl})" if gl else "")
        title = f"NHL: {len(q)} qualified today"
        body = (f"{', '.join(name(g) for g in q[:4])}{' …' if len(q) > 4 else ''} · first puck drop {first:%-I:%M %p} PT"
                + (f" · Watch list: {len(w)}" if w else ""))
        try:
            notify.send_alert(title, body, click=board.PAGES_URL + "nhl/", priority="high", tags="hockey")
            print(f"Phone push sent: {title} | {body}")
        except Exception as e:      # the page is published either way
            print(f"! phone push not sent: {e}")
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
