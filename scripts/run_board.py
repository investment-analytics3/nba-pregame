#!/usr/bin/env python3
"""Publish and send the board for today's slate.

    python scripts/run_board.py --kind board|final|preview [--date YYYY-MM-DD] [--no-send]

Reads data/slates/<date>.json (built just before by build_slate.py), then:
  - writes the interactive page to docs/index.html (GitHub Pages) and keeps a
    copy in docs/boards/
  - emails the board (summary in the email, full page attached)
  - sends a phone alert that opens the page
  - records what was sent in data/board_log.json (for the "what changed" list)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import board, notify, store  # noqa: E402
from scripts import live_context  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
DOCS = os.path.join(board.ROOT, "docs")


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> int:
    kind = arg("--kind", "board")
    day = date.fromisoformat(arg("--date")) if arg("--date") else datetime.now(ET).date()
    slate = store.read_json(f"slates/{day.isoformat()}.json")
    if not slate:
        print(f"No slate for {day}; run build_slate.py first.")
        return 1
    log = store.read_json("board_log.json", {}) or {}
    today_log = log.get(day.isoformat(), {})

    change_list, earlier = None, None
    if kind == "final" and "board" in today_log:
        earlier = today_log["board"]["label"]
        change_list = board.changes(today_log["board"]["snapshot"], board.snapshot(slate))

    data = board.page_data(slate, kind, change_list, earlier)
    page = board.render_page(data)
    os.makedirs(os.path.join(DOCS, "boards"), exist_ok=True)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    with open(os.path.join(DOCS, "boards", f"{day.isoformat()}-{kind}.html"), "w") as f:
        f.write(page)
    print(f"Page written ({len(page) // 1024} KB).")
    ctx = live_context.build(day)
    os.makedirs(os.path.dirname(live_context.OUT), exist_ok=True)
    with open(live_context.OUT, "w") as f:
        json.dump(ctx, f, indent=1)
    print(f"Live context written ({len(ctx['games'])} tracked).")

    title, body = board.alert_text(data)
    if "--no-send" not in sys.argv:
        subject = title.replace("NBA Pregame Board", "NBA Board") + " - " + body
        notify.send_email(subject[:150], board.render_email(data), f"{title}\n{body}\n{board.PAGES_URL}",
                          attachments=[(f"NBA-board-{day.isoformat()}.html", page.encode("utf-8"), "text/html")])
        print("Email sent.")
        try:
            if kind == "preview":
                raise RuntimeError("previews don't send a phone alert")
            notify.send_alert(title, body, click=board.PAGES_URL,
                              priority="high" if any(g["scoring"].get("qualified") for g in data["games"]) else "default")
            print("Phone alert sent.")
        except Exception as e:  # the email already went out; don't fail the run
            print(f"! phone alert not sent: {e}")

    if kind in ("board", "final"):
        today_log[kind] = {"sent_at": datetime.now(PT).isoformat(timespec="seconds"),
                           "label": datetime.now(PT).strftime("%-I:%M %p"),
                           "page": f"boards/{day.isoformat()}-{kind}.html",
                           "snapshot": board.snapshot(slate)}
        log[day.isoformat()] = today_log
        # keep the log small: last 30 days
        for k in sorted(log)[:-30]:
            log.pop(k)
        store.write_json("board_log.json", log)
    print(f"{title}: {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
