"""Power rank consensus: own net-rating rank + ESPN BPI (daily) + NBA.com (weekly).

Rules (from the design spec):
- Daily sources count fully; NBA.com counts half.
- If NBA.com is more than 5 spots off the daily average, or puts a team in a
  different top-10 / middle / bottom-10 bucket, it is dropped for that team
  and the team is flagged "split".
- If BPI or NBA.com is unavailable, the rest still produce a ranking; the own
  net-rating rank is always available.
"""
from __future__ import annotations

import json
import re

from .http import get, FetchError
from .injury_report import norm


def nba_com_rankings() -> dict:
    """Latest weekly NBA.com power rankings: {"published", "url", "by_name": {norm_name: rank}}."""
    page = get("https://www.nba.com/news/category/power-rankings", want="bytes")
    weeks = re.findall(rb"/news/power-rankings-(\d{4})-(\d{2})-week-(\d+)", page or b"")
    if not weeks:
        return {}
    y1, y2, wk = max(weeks, key=lambda w: (int(w[0]), int(w[2])))
    url = f"https://www.nba.com/news/power-rankings-{y1.decode()}-{y2.decode()}-week-{wk.decode()}"
    html = get(url, want="bytes") or b""
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    art = json.loads(m.group(1))["props"]["pageProps"]["article"]
    by_name = {}
    for x in art.get("powerRankings", []):
        if x.get("currentWeekRank"):
            by_name[norm(x["teamName"])] = int(x["currentWeekRank"])
    return {"published": art.get("date"), "url": url, "title": art.get("title"), "by_name": by_name}


def _bucket(r: float) -> str:
    r = round(r)
    return "top" if r <= 10 else ("bottom" if r >= 21 else "mid")


def consensus(teams: list[dict], own_net_rank: dict, bpi_rank: dict, nba: dict) -> dict:
    """teams: [{"id","display"}]. Returns {team_id: {...ranks, "rank", "split"}}."""
    nba_by_name = (nba or {}).get("by_name", {})
    raw = {}
    for t in teams:
        tid = t["id"]
        own, b = own_net_rank.get(tid), bpi_rank.get(tid)
        n = nba_by_name.get(norm(t["display"]))
        daily = [x for x in (own, b) if x is not None]
        split = False
        if daily:
            avg = sum(daily) / len(daily)
            score = avg
            if n is not None:
                if abs(n - avg) > 5 or _bucket(avg) != _bucket(n):
                    split = True
                else:
                    score = (sum(daily) + 0.5 * n) / (len(daily) + 0.5)
        elif n is not None:
            score = float(n)
        else:
            score = None
        raw[tid] = {"own_net": own, "bpi": b, "nba_com": n, "score": score, "split": split}
    ordered = sorted((tid for tid in raw if raw[tid]["score"] is not None),
                     key=lambda tid: (raw[tid]["score"], raw[tid]["own_net"] or 99))
    for i, tid in enumerate(ordered, start=1):
        raw[tid]["rank"] = i
        raw[tid]["score"] = round(raw[tid]["score"], 2)
    return raw


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except (FetchError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"  ! {fn.__name__} unavailable: {e}")
        return default
