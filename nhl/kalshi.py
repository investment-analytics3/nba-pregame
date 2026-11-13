"""Kalshi public market data for NHL (read-only; the system never trades).

Series KXNHLGAME, one event per game: KXNHLGAME-26NOV13NYRVAN (Eastern date + away code +
home code), one market per team (...-VAN) whose yes_sub_title is the team name ("Vancouver"); codes are ESPN's (LA, NJ, SJ, TB, UTA).
Codes usually match ESPN's abbreviations; when they don't, the event is matched by date and team
names. Kalshi refuses browser-style requests (Origin header), so only server-side scripts call it.
"""
from __future__ import annotations

import re
from datetime import date

from nba.http import FetchError, get
from nfl.kalshi import _price

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "nhl-board/1.0"}
SERIES = "KXNHLGAME"
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _norm(s: str) -> str:
    s = (s or "").lower().replace("st.", "state").replace("&", "and")
    return re.sub(r"[^a-z]", "", s)


def date_code(d: date) -> str:
    return f"{d:%y}{MON[d.month - 1]}{d.day:02d}"


def open_events() -> list[dict]:
    """Every open NHL game event with its markets (paged)."""
    out, cursor = [], ""
    for _ in range(15):
        url = f"{KALSHI}/events?series_ticker={SERIES}&status=open&limit=200&with_nested_markets=true" + (f"&cursor={cursor}" if cursor else "")
        body = get(url, headers=HEADERS, retries=2) or {}
        out += body.get("events") or []
        cursor = body.get("cursor") or ""
        if not cursor or not body.get("events"):
            break
    return out


def match(events: list[dict], day_et: date, away: dict, home: dict) -> dict | None:
    """Find the event for a game. away/home = {"abbr", "names": [ESPN location, display name, ...]}."""
    want = f"{SERIES}-{date_code(day_et)}{away['abbr']}{home['abbr']}"
    for e in events:
        if e.get("event_ticker") == want:
            return e
    dc = f"-{date_code(day_et)}"
    an, hn = {_norm(n) for n in away.get("names", []) if n}, {_norm(n) for n in home.get("names", []) if n}
    for e in events:
        if dc not in e.get("event_ticker", ""):
            continue
        subs = {_norm(m.get("yes_sub_title")) for m in e.get("markets") or []}
        if subs & an and subs & hn:
            return e
    return None


def prices(event: dict, away: dict, home: dict) -> dict:
    """{"event", "away": {...}, "home": {...}} with bid/ask/last/prob for each side."""
    out = {"event": event.get("event_ticker")}
    an, hn = {_norm(n) for n in away.get("names", []) if n}, {_norm(n) for n in home.get("names", []) if n}
    for m in event.get("markets") or []:
        code, sub = m.get("ticker", "").split("-")[-1], _norm(m.get("yes_sub_title"))
        side = "away" if (code == away["abbr"] or sub in an) else ("home" if (code == home["abbr"] or sub in hn) else None)
        if side:
            out[side] = _price(m)
    return out


def event_prices(ticker: str) -> list[dict]:
    """Fresh markets for one event (live polling)."""
    try:
        body = get(f"{KALSHI}/markets?event_ticker={ticker}&limit=10", headers=HEADERS, retries=2) or {}
    except FetchError:
        return []
    return body.get("markets") or []
