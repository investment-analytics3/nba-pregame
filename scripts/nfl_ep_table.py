#!/usr/bin/env python3
"""Build docs/nfl/ep.json: expected points by down x distance x field position.

The live engine estimates EPA from ESPN play-by-play (which has no EPA) with this
lookup: average nflverse expected points (`ep`) before the snap, from the given seasons.
One-off; rerun before a season if wanted:  python scripts/nfl_ep_table.py 2024 2025
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nfl import sources  # noqa: E402

DIST = [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 99]          # bucket upper bounds for yards to go
YARD = list(range(5, 101, 5))                          # yards to the end zone, 5-yard buckets


def bucket(v, bounds):
    for i, b in enumerate(bounds):
        if v <= b:
            return i
    return len(bounds) - 1


def main():
    seasons = [int(s) for s in sys.argv[1:]] or [2024, 2025]
    frames = []
    for s in seasons:
        p = os.path.join(sources.CACHE, f"pbp_{s}.parquet")
        if not os.path.exists(p):
            sources._cached(f"pbp_{s}.parquet", sources.PBP_URL.format(season=s), True)
        frames.append(pd.read_parquet(p, columns=["down", "ydstogo", "yardline_100", "ep"]))
    d = pd.concat(frames).dropna()
    d = d[d.down.between(1, 4)]
    d["db"] = d.ydstogo.apply(lambda v: bucket(v, DIST))
    d["yb"] = d.yardline_100.apply(lambda v: bucket(v, YARD))
    t = d.groupby(["down", "db", "yb"]).ep.agg(["mean", "size"]).reset_index()
    grid = [[[None] * len(YARD) for _ in DIST] for _ in range(4)]
    for r in t.itertuples():
        if r.size >= 15:
            grid[int(r.down) - 1][int(r.db)][int(r.yb)] = round(float(r.mean), 3)
    # fill gaps from the nearest field-position bucket, then nearest distance bucket
    for dn in range(4):
        for di in range(len(DIST)):
            row = grid[dn][di]
            for yi in range(len(YARD)):
                if row[yi] is None:
                    near = [(abs(yi - j), row[j]) for j in range(len(YARD)) if row[j] is not None]
                    row[yi] = min(near)[1] if near else None
        for di in range(len(DIST)):
            for yi in range(len(YARD)):
                if grid[dn][di][yi] is None:
                    near = [(abs(di - k), grid[dn][k][yi]) for k in range(len(DIST)) if grid[dn][k][yi] is not None]
                    grid[dn][di][yi] = min(near)[1] if near else 0.0
    out = {"source": f"nflverse ep, seasons {seasons}", "dist_bounds": DIST, "yard_bounds": YARD, "ep": grid}
    path = os.path.join(sources.ROOT, "docs", "nfl", "ep.json")
    with open(path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {path} ({os.path.getsize(path)} bytes) from {len(d)} plays")


if __name__ == "__main__":
    main()
