#!/usr/bin/env python3
"""Overnight data update (scheduled 3 AM Pacific).

1. Catch up: save the box score of every finished regular-season game not yet saved.
2. Recompute season and last-3 metrics with ranks -> data/metrics.json
3. Rebuild the power-rank consensus -> data/power.json

Safe to run any time and any number of times: it only fetches what is missing.
    python scripts/update_data.py              # normal
    python scripts/update_data.py --scheduled  # only acts at 3 AM Pacific (for cron)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import espn, metrics, power, store  # noqa: E402
from nba.http import FetchError  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")


def catch_up(today) -> dict:
    """Fetch every missing finished game before `today` (an ET date)."""
    state = store.read_json("state.json", {}) or {}
    done_days = set(state.get("complete_days", []))
    saved = store.saved_game_ids()
    sb_today = espn.scoreboard(today)
    days = [d for d in espn.season_game_days(sb_today) if d < today]
    added, failed, checked = 0, [], 0
    for d in days:
        if d.isoformat() in done_days:
            continue
        checked += 1
        events = espn.parse_events(espn.scoreboard(d))
        reg = [e for e in events if e["season_type"] == espn.REGULAR_SEASON]
        all_done = True
        new_t, new_p = [], []
        for e in reg:
            if not e["completed"]:
                all_done = False
                continue
            if e["id"] in saved:
                continue
            try:
                t_rows, p_rows = espn.parse_boxscore(espn.summary(e["id"]), d)
                new_t += t_rows
                new_p += p_rows
                saved.add(e["id"])
                added += 1
            except (FetchError, KeyError, ValueError) as ex:
                failed.append(f"{e['short']} {d}: {ex}")
                all_done = False
        if new_t:
            store.add_games(new_t, new_p)
        if all_done:
            done_days.add(d.isoformat())
    state["complete_days"] = sorted(done_days)
    state["last_update"] = datetime.now(PT).isoformat(timespec="seconds")
    store.write_json("state.json", state)
    return {"days_checked": checked, "games_added": added, "failed": failed}


def _output(name: str, value: str):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


def main() -> int:
    now_pt = datetime.now(PT)
    if "--scheduled" in sys.argv and now_pt.hour != 3:
        print(f"Scheduled run at {now_pt:%H:%M} PT; only acts at 3 AM PT. Skipping.")
        _output("acted", "false")
        return 0
    _output("acted", "true")
    today = datetime.now(ET).date()
    print(f"Overnight update for games before {today} (ET), run at {now_pt:%Y-%m-%d %H:%M} PT")

    result = catch_up(today)
    print(f"  days checked: {result['days_checked']}, games added: {result['games_added']}")
    for f in result["failed"]:
        print("  ! failed:", f)

    rows = store.load_team_games()
    m = metrics.compute(rows, before=today.isoformat())
    m["computed_for"] = today.isoformat()
    store.write_json("metrics.json", m)
    print(f"  metrics: {m['team_count']} teams, games through {m['games_through']}, "
          f"{len(rows) // 2} games saved")

    teams = power.safe(espn.teams, default=None) or store.read_json("teams.json", [])
    if teams:
        store.write_json("teams.json", teams)
    own = {tid: d["season"]["ranks"]["net"] for tid, d in m["teams"].items() if d["season"].get("ranks")}
    b = power.safe(espn.bpi, default={}) or {}
    nba = power.safe(power.nba_com_rankings, default={}) or {}
    cons = power.consensus(teams, own, {k: v["rank"] for k, v in (b.get("teams") or {}).items()}, nba)
    store.write_json("power.json", {
        "computed_for": today.isoformat(),
        "sources": {"own_net": bool(own), "bpi": bool(b.get("teams")), "bpi_updated": b.get("updated"),
                    "nba_com": bool(nba.get("by_name")), "nba_com_published": nba.get("published"),
                    "nba_com_title": nba.get("title")},
        "teams": cons,
    })
    print(f"  power: own={bool(own)} bpi={bool(b.get('teams'))} nba.com={bool(nba.get('by_name'))} "
          f"({nba.get('published', 'n/a')})")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"## Overnight data update\n\n- Games through **{m['games_through']}**, "
                    f"{len(rows) // 2} games saved, {result['games_added']} added this run\n"
                    f"- Power sources: own net rating {'yes' if own else 'NO'}, ESPN BPI "
                    f"{'yes' if b.get('teams') else 'NO'}, NBA.com {'yes' if nba.get('by_name') else 'NO'}\n")
            if result["failed"]:
                f.write("- Failed games (will retry next run): " + "; ".join(result["failed"]) + "\n")
    # a partial catch-up is fine (retried next run); only fail if nothing usable exists
    return 0 if m["team_count"] >= 30 else 1


if __name__ == "__main__":
    sys.exit(main())
