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


def _rank_by(scores: dict) -> dict:
    ordered = sorted((t for t in scores if scores[t] is not None), key=lambda t: scores[t])
    return {t: i for i, t in enumerate(ordered, start=1)}


def consensus(teams: list[dict], own_net_rank: dict, bpi_rank: dict, nba: dict) -> dict:
    """teams: [{"id","display"}]. Returns {team_id: {...ranks, "rank", "split", "disagree"}}.

    "disagree": NBA.com is more than 5 spots off the daily sources or in another
    bucket, so it is left out of that team's blend.
    "split" (the amber tag): the disagreement changes something that matters,
    i.e. with vs without NBA.com the team lands in a different top-10 / middle /
    bottom-10 bucket, which can change the power-rank point.
    """
    nba_by_name = (nba or {}).get("by_name", {})
    raw, daily_only, with_nba = {}, {}, {}
    for t in teams:
        tid = t["id"]
        own, b = own_net_rank.get(tid), bpi_rank.get(tid)
        n = nba_by_name.get(norm(t["display"]))
        daily = [x for x in (own, b) if x is not None]
        avg = sum(daily) / len(daily) if daily else (float(n) if n is not None else None)
        blended = (sum(daily) + 0.5 * n) / (len(daily) + 0.5) if (daily and n is not None) else avg
        disagree = bool(daily and n is not None and (abs(n - avg) > 5 or _bucket(avg) != _bucket(n)))
        daily_only[tid], with_nba[tid] = avg, blended
        raw[tid] = {"own_net": own, "bpi": b, "nba_com": n, "disagree": disagree,
                    "score": None if avg is None else round(avg if disagree else blended, 2)}
    final = _rank_by({tid: r["score"] for tid, r in raw.items()})
    r_daily, r_with = _rank_by(daily_only), _rank_by(with_nba)
    for tid, r in raw.items():
        r["rank"] = final.get(tid)
        r["rank_daily_only"], r["rank_with_nba"] = r_daily.get(tid), r_with.get(tid)
        r["split"] = bool(r["disagree"] and r_daily.get(tid) and r_with.get(tid)
                          and _bucket(r_daily[tid]) != _bucket(r_with[tid]))
    return raw


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except (FetchError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"  ! {fn.__name__} unavailable: {e}")
        return default
