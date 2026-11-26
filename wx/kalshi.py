"""Kalshi public market data for the weather series (read-only; the system never trades).

GET /markets?series_ticker=KXHIGHNY&status=open  -> every open bracket of the series (all open dates)
GET /markets/<ticker>                             -> one market; `result` is "yes"/"no" once settled
Prices come as dollar strings (`yes_bid_dollars`), sizes as `yes_bid_size_fp`. Kalshi books only hold bids:
buying NO at the best price pays 1 - best YES bid, and the size there is the YES bid size.
"""
from __future__ import annotations

from nba.http import FetchError, get

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "wx-board/1.0"}


def open_markets(series: str) -> list[dict]:
    out, cursor = [], ""
    for _ in range(5):
        url = f"{KALSHI}/markets?series_ticker={series}&status=open&limit=200" + (f"&cursor={cursor}" if cursor else "")
        body = get(url, headers=HEADERS, retries=2, pace=0.15) or {}
        out += body.get("markets") or []
        cursor = body.get("cursor") or ""
        if not cursor or not body.get("markets"):
            break
    return out


def market(ticker: str) -> dict | None:
    try:
        body = get(f"{KALSHI}/markets/{ticker}", headers=HEADERS, retries=2, pace=0.15) or {}
    except FetchError:
        return None
    return body.get("market")


def forecast_highs(lat: float, lon: float, tz: str) -> dict:
    """{"2026-11-27": 61.0, ...} daily max temperature (F) from Open-Meteo. Display only; {} on failure."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max"
           f"&temperature_unit=fahrenheit&timezone={tz}&forecast_days=3")
    try:
        body = get(url, retries=1) or {}
    except FetchError:
        return {}
    d = body.get("daily") or {}
    return {t: v for t, v in zip(d.get("time") or [], d.get("temperature_2m_max") or []) if v is not None}
