#!/usr/bin/env python3
"""Rebuild boards for past days that were never sent, then grade them.

Uses only what was known before tip-off (see build_slate.build(retro=True)).
Rows graded from these boards are tagged "Rebuilt" on the Results page.

    python scripts/rebuild_days.py              # the last 3 days
    python scripts/rebuild_days.py --days 7     # the last 7 days
    python scripts/rebuild_days.py 2026-11-05   # specific dates
Days that already have a sent board are skipped.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from nba import board, store  # noqa: E402
import build_slate  # noqa: E402
import update_results  # noqa: E402

ET = ZoneInfo("America/New_York")
DOCS = os.path.join(board.ROOT, "docs")


def main() -> int:
    argv = sys.argv[1:]
    if "--days" in argv:
        i = argv.index("--days")
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    if args:
        days = [date.fromisoformat(a) for a in args]
    else:
        n = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 3
        today = datetime.now(ET).date()
        days = [today - timedelta(days=i) for i in range(n, 0, -1)]
    log = store.read_json("board_log.json", {}) or {}
    os.makedirs(os.path.join(DOCS, "boards"), exist_ok=True)
    for d in days:
        entry = log.get(d.isoformat(), {})
        if entry.get("board") or entry.get("final"):
            print(f"{d}: a board was sent that day; skipping")
            continue
        slate = build_slate.build(d, retro=True)
        early = [g for g in slate["games"] if min(g["home"].get("games_played", 0), g["away"].get("games_played", 0)) < 3]
        slate["games"] = [g for g in slate["games"] if g not in early]
        if early:
            print(f"{d}: skipped {len(early)} game(s) where a team had played fewer than 3 games")
        if not slate["games"]:
            print(f"{d}: no games")
            continue
        store.write_json(f"slates/{d.isoformat()}.json", slate)
        page = board.render_page(board.page_data(slate, "retro", None, None))
        rel = f"boards/{d.isoformat()}-retro.html"
        with open(os.path.join(DOCS, rel), "w") as f:
            f.write(page)
        entry["retro"] = {"sent_at": slate["built_at"], "label": "rebuilt", "page": rel,
                          "snapshot": board.snapshot(slate)}
        log[d.isoformat()] = entry
        q = sum(1 for g in slate["games"] if g["scoring"].get("qualified"))
        lines = sum(1 for g in slate["games"] if g.get("spread"))
        print(f"{d}: rebuilt {len(slate['games'])} games ({lines} with closing lines, {q} above the line); "
              f"injuries: {slate['injury_source']} ({slate['injury_report_time']})")
    store.write_json("board_log.json", log)
    return update_results.main()


if __name__ == "__main__":
    sys.exit(main())
