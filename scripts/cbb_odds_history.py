"""One-off: closing DraftKings lines for past college basketball games (for the backtest).

The free play-by-play release has no real spreads, but each game's raw ESPN summary is archived in
sportsdataverse/hoopR-mbb-raw, and its `pickcenter` holds the DraftKings line. Only games whose ESPN
pregame win probability puts the favorite at 68-99.5% are fetched (a -10 to -20 line sits inside that).

    python scripts/cbb_odds_history.py data/cbb/cand_2026.csv data/cbb/odds_2026.csv
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

RAW = "https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-raw/main/mbb/json/final/{gid}.json"
FIELDS = ["game_id", "provider", "details", "spread", "home_fav", "home_ml", "away_ml", "home_id", "away_id"]


def one(gid: str) -> dict | None:
    r = subprocess.run(["curl", "-sfL", "--max-time", "60", RAW.format(gid=gid)], capture_output=True)
    if r.returncode or not r.stdout:
        return None
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return None
    pc = [p for p in (d.get("pickcenter") or []) if p.get("spread") is not None]
    if not pc:
        return {"game_id": gid}
    p = sorted(pc, key=lambda x: (x.get("provider") or {}).get("priority", 9))[0]
    h, a = p.get("homeTeamOdds") or {}, p.get("awayTeamOdds") or {}
    return {"game_id": gid, "provider": (p.get("provider") or {}).get("name"), "details": p.get("details"),
            "spread": p.get("spread"), "home_fav": h.get("favorite"), "home_ml": h.get("moneyLine"),
            "away_ml": a.get("moneyLine"), "home_id": h.get("teamId"), "away_id": a.get("teamId")}


def main(cand: str, out: str) -> None:
    ids = [r["game_id"] for r in csv.DictReader(open(cand))]
    done = set()
    if os.path.exists(out):
        done = {r["game_id"] for r in csv.DictReader(open(out))}
    todo = [g for g in ids if g not in done]
    new = not os.path.exists(out)
    with open(out, "a", newline="") as f, ThreadPoolExecutor(24) as ex:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for i, row in enumerate(ex.map(one, todo)):
            if row:
                w.writerow(row)
            if i % 200 == 0:
                f.flush()
                print(f"{i}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
