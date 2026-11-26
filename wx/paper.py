"""The paper rule, the ledger and the results math. No network here (the tick passes market dicts in).

Ledger = data/wx/ledger.json: {"bets": [...]} one row per paper bet, frozen once settled.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from wx import config as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "wx")
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ---- small helpers -----------------------------------------------------------------------------
def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def date_code(d: date) -> str:
    return f"{d:%y}{MON[d.month - 1]}{d.day:02d}"


def event_date(event_ticker: str) -> date | None:
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)$", event_ticker or "")
    if not m or m[2] not in MON:
        return None
    return date(2000 + int(m[1]), MON.index(m[2]) + 1, int(m[3]))


def close_utc(series: str, d: date) -> datetime:
    """Scheduled close of the event for date d: 1:00 AM local clock time the next day (Phoenix: midnight)."""
    tz = C.ALL_SERIES[series][1]
    nd = d + timedelta(days=1)
    hour = 0 if tz == "America/Phoenix" else 1
    return datetime(nd.year, nd.month, nd.day, hour, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def fee(price: float, contracts: int) -> float:
    """Kalshi taker fee for one order in dollars: ceil(0.07 x C x P x (1-P)) to the cent."""
    return math.ceil(round(C.FEE_RATE * contracts * price * (1 - price) * 100, 6)) / 100


def bracket_label(m: dict) -> str:
    sub = (m.get("yes_sub_title") or "").strip()
    if sub:
        return sub
    st, lo, hi = m.get("strike_type"), num(m.get("floor_strike")), num(m.get("cap_strike"))
    if st == "between" and lo is not None and hi is not None:
        return f"{lo:g}° to {hi:g}°"
    if st == "greater" and lo is not None:
        return f"{lo + 1:g}° or above"
    if st == "less" and hi is not None:
        return f"{hi - 1:g}° or below"
    return m.get("ticker", "")


# ---- the rule --------------------------------------------------------------------------------
def due_events(group: str, now: datetime, logged: set) -> list[tuple[str, date, datetime]]:
    """(series, event date, close) whose price check is due now for this group and not logged yet.

    Due = hours to close between target - WINDOW_H and target (the first tick inside the window logs).
    """
    g = C.GROUPS[group]
    out = []
    for series in g["series"]:
        for k in range(-1, 3):
            d = now.astimezone(timezone.utc).date() + timedelta(days=k)
            cl = close_utc(series, d)
            h = (cl - now).total_seconds() / 3600
            if g["target_h"] - C.WINDOW_H <= h <= g["target_h"] and (group, f"{series}-{date_code(d)}") not in logged:
                out.append((series, d, cl))
    return out


def qualifies(m: dict) -> bool:
    if (m.get("status") or "active") not in ("active", "open"):
        return False
    bid = num(m.get("yes_bid_dollars"))
    return bid is not None and C.YES_BID_MIN - 1e-9 <= bid <= C.YES_BID_MAX + 1e-9


def make_bet(group: str, series: str, m: dict, now: datetime, forecast: float | None = None) -> dict | None:
    """Paper bet: buy NO at the best price (1 - YES bid), up to CONTRACTS at that price."""
    bid = num(m.get("yes_bid_dollars"))
    no_ask = num(m.get("no_ask_dollars"))
    price = round(no_ask if no_ask and 0 < no_ask < 1 else 1 - bid, 4)
    depth = num(m.get("yes_bid_size_fp")) or 0
    contracts = int(min(C.CONTRACTS, math.floor(depth))) if depth else C.CONTRACTS
    if contracts <= 0 or not (0 < price < 1):
        return None
    cl = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
    f = fee(price, contracts)
    return {
        "id": f"{group}:{m['ticker']}", "group": group, "series": series, "city": C.ALL_SERIES[series][0],
        "kind": "low" if series.startswith("KXLOW") else "high",
        "event": m.get("event_ticker"), "ticker": m["ticker"], "date": event_date(m.get("event_ticker", "")).isoformat(),
        "bracket": bracket_label(m), "logged_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "hours_before": round((cl - now).total_seconds() / 3600, 1), "close_utc": cl.isoformat(),
        "yes_bid": bid, "no_price": price, "depth": depth, "contracts": contracts, "fee": f,
        "cost": round(price * contracts + f, 2), "max_win": round((1 - price) * contracts - f, 2),
        "forecast": forecast, "volume": num(m.get("volume_fp")),
        "status": "open", "result": None, "pnl": None, "settled_utc": None,
    }


def settle(bet: dict, result: str, now: datetime) -> dict:
    """result = the market's "yes"/"no". NO wins when the bracket settles NO."""
    won = result == "no"
    c, p = bet["contracts"], bet["no_price"]
    bet.update(status="won" if won else "lost", result=result,
               pnl=round((c * (1 - p) if won else -c * p) - bet["fee"], 2),
               settled_utc=now.astimezone(timezone.utc).isoformat(timespec="seconds"))
    return bet


# ---- ledger ----------------------------------------------------------------------------------
def load() -> dict:
    try:
        with open(os.path.join(DATA, "ledger.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"bets": []}


def save(led: dict):
    os.makedirs(DATA, exist_ok=True)
    led["bets"].sort(key=lambda b: (b["date"], b["group"], b["series"], b["ticker"]))
    with open(os.path.join(DATA, "ledger.json"), "w") as f:
        json.dump(led, f, indent=1)


def logged_events(led: dict) -> set:
    """(group, event) pairs already checked. A check that found no qualifying bracket is kept in `checked`."""
    s = {(b["group"], b["event"]) for b in led["bets"]}
    s |= {tuple(x) for x in led.get("checked", [])}
    return s


# ---- results ---------------------------------------------------------------------------------
def stats(bets: list[dict]) -> dict:
    done = [b for b in bets if b["status"] in ("won", "lost")]
    n = len(done)
    out = {"bets": len(bets), "open": sum(b["status"] == "open" for b in bets), "settled": n,
           "won": sum(b["status"] == "won" for b in done), "lost": sum(b["status"] == "lost" for b in done),
           "pnl": round(sum(b["pnl"] for b in done), 2), "risked": round(sum(b["cost"] for b in done), 2),
           "win": None, "price": None, "edge": None, "cpc": None, "ci": None, "roi": None}
    if n:
        contracts = sum(b["contracts"] for b in done)
        out["win"] = round(100 * out["won"] / n, 1)
        out["price"] = round(100 * sum(b["no_price"] for b in done) / n, 1)
        out["edge"] = round(out["win"] - out["price"], 1)
        per = [b["pnl"] / b["contracts"] * 100 for b in done]          # cents per contract, one value per bet
        m = sum(per) / n
        out["cpc"] = round(sum(b["pnl"] for b in done) / contracts * 100, 2)
        if n > 1:
            sd = math.sqrt(sum((x - m) ** 2 for x in per) / (n - 1))
            se = sd / math.sqrt(n)
            out["ci"] = [round(m - 1.645 * se, 2), round(m + 1.645 * se, 2)]
        out["roi"] = round(100 * out["pnl"] / out["risked"], 2) if out["risked"] else None
    return out
