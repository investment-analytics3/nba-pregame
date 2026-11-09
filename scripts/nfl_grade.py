#!/usr/bin/env python3
"""Grade logged NFL checkpoints from nflverse play-by-play (same rules as the backtest).

For every halftime / end-of-3rd record in data/nfl/live/<season>.json whose game is in
nflverse's play-by-play, adds:
    graded.<kind>  exact lead, defense top 10, control (3 of 5), QB edge, full qualifier,
                   shadow line (control + QB edge, any defense), nflverse model price
    result         final margin and win for the favorite
Safe to repeat: already-graded entries are skipped.

    python scripts/nfl_grade.py [--season 2026] [--no-refresh]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nfl import early_path, metrics, sources  # noqa: E402
from nfl.board import season_for  # noqa: E402

ROOT = sources.ROOT


def main() -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    season = int(sys.argv[sys.argv.index("--season") + 1]) if "--season" in sys.argv else \
        season_for(datetime.now(ZoneInfo("America/New_York")).date())
    path = os.path.join(sources.DATA, "live", f"{season}.json")
    if not os.path.exists(path):
        print("No checkpoint log yet.")
        return 0
    with open(path) as f:
        book = json.load(f)
    todo = {gid: e for gid, e in book["games"].items()
            if any(k in e for k in early_path.CHECKPOINTS) and not e.get("graded_done")}
    if not todo:
        print("Nothing to grade.")
        return 0
    pbp = sources.pbp(season, refresh="--no-refresh" not in sys.argv)
    tg = metrics.team_games(pbp)
    done = 0
    for gid, e in todo.items():
        gp = pbp[pbp.game_id == e["nflverse_id"]]
        if gp.empty or gp.result.isna().all() or gp.qtr.max() < 4:
            continue
        wk = int(e["week"])
        wm = metrics.week_metrics(pbp, wk, tg)
        qbs = metrics.quarterbacks(pbp, wk)
        e.setdefault("graded", {})
        for kind in early_path.CHECKPOINTS:
            if kind in e:
                gr = early_path.grade(gp, kind, e["fav"], e["dog"], wm, qbs)
                if gr:
                    e["graded"][kind] = gr
        res = float(gp.result.dropna().iloc[0])            # home margin
        margin = res if e["fav"] == e["home"] else -res
        e["result"] = {"fav_margin": int(margin), "fav_won": margin > 0,
                       "covered": (margin > abs(e["spread"])) if e.get("spread") is not None else None}
        e["graded_done"] = True
        done += 1
    body = json.dumps(book, indent=1)
    with open(path, "w") as f:
        f.write(body)
    with open(os.path.join(ROOT, "docs", "nfl", "live.json"), "w") as f:
        f.write(body)
    print(f"Graded {done} game(s); {len(todo) - done} waiting for nflverse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
