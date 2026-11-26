"""Tracked matches (data/tennis/ledger.json): logged once 3-6 h before the listed start, graded from Kalshi's result.

Results are per $100 risked on the favorite at the logged price, Kalshi taker fee included. A void match (walkover,
retirement before a ball, "scalar" settlement) is graded void and left out of the record.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

from tennis import config as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "tennis")


def load() -> dict:
    try:
        with open(os.path.join(DATA, "ledger.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"matches": []}


def save(led: dict):
    os.makedirs(DATA, exist_ok=True)
    led["matches"].sort(key=lambda r: (r["start"] or "", r["event"]))
    with open(os.path.join(DATA, "ledger.json"), "w") as f:
        json.dump(led, f, indent=1)


def stake(price: float) -> tuple[int, float, float]:
    contracts = max(1, round(C.STAKE / price))
    fee = math.ceil(round(C.FEE_RATE * contracts * price * (1 - price) * 100, 6)) / 100
    return contracts, fee, round(contracts * price + fee, 2)


def entry(sc: dict, now: datetime, late: bool) -> dict:
    """Freeze a scored match into a ledger row."""
    c, fee, cost = stake(sc["price"])
    start = datetime.fromisoformat(sc["start"].replace("Z", "+00:00"))
    keep = {k: sc[k] for k in ("event", "series", "tab", "tour", "tourney", "round", "start", "surface", "price", "ml",
                                "opp_price", "opp_ml", "tier", "tier_label", "section", "rank_match", "score", "max", "band",
                                "flags", "readout", "links")}
    side = lambda p: {k: p.get(k) for k in ("name", "elo_rank", "rank", "elo", "sr", "rated")} | {  # noqa: E731
        "form": f"{p['form']['w']}-{p['form']['l']}" if p.get("form") else None,
        "load": (p.get("load") or {}).get("minutes")}
    return {**keep, "fav": side(sc["fav"]), "opp": side(sc["opp"]),
            "metrics": [{k: x[k] for k in ("key", "label", "pts", "max", "note")} for x in sc["metrics"]],
            "logged_utc": now.isoformat(timespec="seconds"), "hours_before": round((start - now).total_seconds() / 3600, 1),
            "late": late, "contracts": c, "fee": fee, "cost": cost,
            "status": "open", "winner": None, "pnl": None, "graded_utc": None}


def grade(row: dict, winner: str | None, void: bool, now: datetime):
    if void:
        row.update(status="void", pnl=0.0)
    elif winner:
        from tennis.sources import norm
        won = norm(winner) == norm(row["fav"]["name"])
        p, c = row["price"], row["contracts"]
        row.update(status="won" if won else "lost", winner=winner,
                   pnl=round((c * (1 - p) if won else -c * p) - row["fee"], 2))
    else:
        return
    row["graded_utc"] = now.astimezone(timezone.utc).isoformat(timespec="seconds")


def stats(rows: list[dict]) -> dict:
    done = [r for r in rows if r["status"] in ("won", "lost")]
    n = len(done)
    out = {"tracked": len(rows), "open": sum(r["status"] == "open" for r in rows), "void": sum(r["status"] == "void" for r in rows),
           "n": n, "won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
           "pnl": round(sum(r["pnl"] for r in done), 2), "risked": round(sum(r["cost"] for r in done), 2),
           "win": None, "implied": None, "edge": None, "roi": None, "avg_ml": None}
    if n:
        out["win"] = round(100 * out["won"] / n, 1)
        out["implied"] = round(100 * sum(r["price"] for r in done) / n, 1)
        out["edge"] = round(out["win"] - out["implied"], 1)
        out["roi"] = round(100 * out["pnl"] / out["risked"], 1) if out["risked"] else None
        p = sum(r["price"] for r in done) / n
        out["avg_ml"] = -round(100 * p / (1 - p)) if p < 1 else None
    return out
