"""Kalshi public market data for NFL games (read-only; the system never trades).

Series KXNFLGAME, one event per game: KXNFLGAME-26NOV12WASNYG (away code + home code),
one market per team: ...-NYG. Kalshi refuses browser-style requests (Origin header),
so only server-side scripts call it.
"""
from __future__ import annotations

from datetime import date

from nba.http import FetchError, get

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "nfl-board/1.0"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def event_ticker(day_et: date, away_k: str, home_k: str) -> str:
    return f"KXNFLGAME-{day_et:%y}{MON[day_et.month - 1]}{day_et.day:02d}{away_k}{home_k}"


def _price(m: dict) -> dict:
    def f(k):
        try:
            v = float(m.get(k))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    bid, ask, last = f("yes_bid_dollars"), f("yes_ask_dollars"), f("last_price_dollars")
    if bid and ask and ask - bid <= 0.10:
        mid = (bid + ask) / 2
    elif last:
        mid = last
    elif bid and ask:
        mid = (bid + ask) / 2
    else:
        mid = None
    return {"bid": bid, "ask": ask, "last": last, "prob": round(mid, 3) if mid else None}


def game_prices(day_et: date, away_k: str, home_k: str) -> dict | None:
    """{"event": ticker, away_k: {...}, home_k: {...}} or None if no market."""
    ev = event_ticker(day_et, away_k, home_k)
    try:
        body = get(f"{KALSHI}/markets?event_ticker={ev}&limit=10", headers=HEADERS, retries=2) or {}
    except FetchError:
        return None
    out = {"event": ev}
    for m in body.get("markets") or []:
        code = m.get("ticker", "").split("-")[-1]
        if code in (away_k, home_k):
            out[code] = _price(m)
    return out if (away_k in out or home_k in out) else None
