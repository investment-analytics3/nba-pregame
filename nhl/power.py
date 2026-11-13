"""NHL consensus power ranking (owner Nov 12): average of
    own      our daily rank (5v5 expected-goals share rank + points-% rank, nhl.metrics.own_rank)
    espn     ESPN's weekly NHL power rankings
    nhl_com  NHL.com's weekly power rankings
re-ranked 1-32. The two published lists have no feed: the owner's Claude scheduled task "NHL power rankings"
reads the articles each week and saves data/nhl/power/<date>.json (format: data/nhl/power/README.md).
A published list older than STALE_DAYS is left out. Ranks are logged daily (data/nhl/power_log.json).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "nhl", "power")
STALE_DAYS = 10
SOURCES = (("espn", "ESPN"), ("nhl_com", "NHL.com"))


def published(day: date) -> dict:
    """{"espn": {"date", "url", "ranks": {code: rank}}, "nhl_com": {...}} — newest list of each, not stale."""
    out = {}
    if not os.path.isdir(DIR):
        return out
    files = sorted(f for f in os.listdir(DIR) if f.endswith(".json") and f[:10] <= day.isoformat())
    for f in reversed(files):
        try:
            with open(os.path.join(DIR, f)) as fh:
                body = json.load(fh)
        except (OSError, ValueError):
            continue
        for key, _ in SOURCES:
            src = (body.get("sources") or {}).get(key)
            if key in out or not src or not src.get("ranks"):
                continue
            try:
                d = date.fromisoformat(str(src.get("date") or f[:10])[:10])
            except ValueError:
                continue
            if day - d <= timedelta(days=STALE_DAYS):
                out[key] = {"date": d.isoformat(), "url": src.get("url"), "ranks": {k: int(v) for k, v in src["ranks"].items()}}
    return out


def consensus(own: dict, pub: dict) -> dict:
    """{team: {"rank", "own", "espn", "nhl_com"}} — average of the available ranks, re-ranked (ties: own rank)."""
    teams = set(own)
    for v in pub.values():
        teams |= set(v["ranks"])
    avg = {}
    for t in teams:
        vals = [x for x in [own.get(t)] + [pub[k]["ranks"].get(t) for k in pub] if x is not None]
        if vals:
            avg[t] = sum(vals) / len(vals)
    order = sorted(avg, key=lambda t: (avg[t], own.get(t, 99), t))
    out = {}
    for i, t in enumerate(order):
        out[t] = {"rank": i + 1, "own": own.get(t), "avg": round(avg[t], 2)}
        for k, _ in SOURCES:
            if k in pub:
                out[t][k] = pub[k]["ranks"].get(t)
    return out
