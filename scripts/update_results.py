#!/usr/bin/env python3
"""Grade finished games from sent boards and rebuild the Results page.

Writes data/results.csv, docs/results.csv (for spreadsheets) and docs/results.html.
Fast: reads two small files, no network.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import board, results, store  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
DOCS = os.path.join(board.ROOT, "docs")


def main() -> int:
    log = store.read_json("board_log.json", {}) or {}
    rows, added = results.grade(log, store.load_team_games(), results.load())
    rows = results.save(rows, os.path.join(DOCS, "results.csv"))
    summ = results.summary(rows)
    data = {"rows": rows, "summary": summ, "updated": datetime.now(PT).strftime("%b %-d, %-I:%M %p PT")}
    html = board._env().get_template("results.html.j2").render(
        d=data, data_json=json.dumps(data).replace("</", "<\\/"), pages_url=board.PAGES_URL)
    with open(os.path.join(DOCS, "results.html"), "w") as f:
        f.write(html)
    a = summ["above"]
    print(f"Results: {len(rows)} graded games ({added} new). Above the line: "
          f"{a['won']}-{a['lost']} straight up, {a['covered']}-{a['not_covered']}-{a['push']} against the spread.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
