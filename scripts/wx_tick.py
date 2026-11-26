#!/usr/bin/env python3
"""Weather tick (wx.yml, started by the NBA Scheduler every 15 minutes). Every step is safe to repeat.

  scan     for each group, events whose price check is due (about 32 h before close for the main rule):
           log a paper NO bet on every bracket with a YES bid of 1-4c (data/wx/ledger.json)
  settle   open bets whose market closed 1 h+ ago: read Kalshi's result (only those, so most ticks make no call)
  publish  docs/wx/index.html, results.html, results.csv, summary.json when anything changed (or hourly)
  push     one quiet summary a day from 5:20 PM PT, main rule only (--send)

    python scripts/wx_tick.py [--send] [--force-publish] [--no-fetch]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba.http import FetchError  # noqa: E402
from wx import config as C  # noqa: E402
from wx import kalshi, pages, paper  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
STATE = os.path.join(paper.DATA, "state.json")


def read_state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(s: dict):
    os.makedirs(paper.DATA, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f, indent=1)


def scan(led: dict, now: datetime, log=print) -> int:
    logged = paper.logged_events(led)
    due = {g: paper.due_events(g, now, logged) for g in C.GROUPS}
    series_needed = sorted({s for items in due.values() for s, _, _ in items})
    if not series_needed:
        return 0
    cache, fc_cache, added = {}, {}, 0
    for s in series_needed:
        try:
            cache[s] = kalshi.open_markets(s)
        except FetchError as e:
            log(f"  {s}: Kalshi fetch failed ({e}); will retry next tick")
    for group, items in due.items():
        for series, d, cl in items:
            if series not in cache:
                continue
            ev = f"{series}-{paper.date_code(d)}"
            ms = [m for m in cache[series] if m.get("event_ticker") == ev]
            if not ms:
                log(f"  {group} {ev}: not listed yet")
                continue
            real = datetime.fromisoformat(ms[0]["close_time"].replace("Z", "+00:00"))
            if abs((real - cl).total_seconds()) > 60:
                h = (real - now).total_seconds() / 3600
                target = C.GROUPS[group]["target_h"]
                log(f"  NOTE {ev}: Kalshi close {real.isoformat()} differs from the expected {cl.isoformat()}")
                if not (target - C.WINDOW_H <= h <= target):
                    continue        # decide from the market's own close time; it comes due on a later tick
            fc = None
            if group == "main" and series in C.STATIONS:
                if series not in fc_cache:
                    fc_cache[series] = kalshi.forecast_highs(*C.STATIONS[series])
                fc = fc_cache[series].get(d.isoformat())
            n = 0
            for m in ms:
                if paper.qualifies(m):
                    b = paper.make_bet(group, series, m, now, fc)
                    if b and b["id"] not in {x["id"] for x in led["bets"]}:
                        led["bets"].append(b)
                        n += 1
            led.setdefault("checked", []).append([group, ev, now.isoformat(timespec="seconds")])
            added += n
            log(f"  {group} {ev}: {len(ms)} brackets, {n} paper bet(s)")
    return added


def settle_open(led: dict, now: datetime, log=print) -> int:
    n = 0
    tickers = {}
    for b in led["bets"]:
        if b["status"] == "open" and now >= datetime.fromisoformat(b["close_utc"]) + timedelta(hours=1):
            tickers.setdefault(b["ticker"], []).append(b)
    for t, bets in list(tickers.items())[:150]:
        m = kalshi.market(t)
        res = ((m or {}).get("result") or "").lower()
        if res in ("yes", "no"):
            for b in bets:
                paper.settle(b, res, now)
                n += 1
                log(f"  settled {b['group']} {t} -> {res}: ${b['pnl']:+.2f}")
    return n


def prune_checked(led: dict, now: datetime):
    cut = now - timedelta(days=10)
    led["checked"] = [c for c in led.get("checked", []) if datetime.fromisoformat(c[2]) >= cut]


def push_text(led: dict, now: datetime) -> tuple[str, str] | None:
    """Daily summary, main rule only: tomorrow's bets + yesterday's settled + running total."""
    now_pt = now.astimezone(PT)
    tomorrow = (now_pt.date() + timedelta(days=1)).isoformat()
    main = [b for b in led["bets"] if b["group"] == "main"]
    new = [b for b in main if b["date"] == tomorrow]
    since = now - timedelta(hours=24)
    recent = [b for b in main if b["status"] in ("won", "lost") and datetime.fromisoformat(b["settled_utc"]) >= since]
    st = paper.stats(main)
    lines = []
    if new:
        lines.append(f"{len(new)} paper NO bet(s) for {datetime.fromisoformat(tomorrow):%a %b %-d}, "
                     f"${sum(b['cost'] for b in new):.0f} at risk, up to ${sum(b['max_win'] for b in new):.2f} to win.")
    else:
        lines.append(f"No bracket priced 1-4c for {datetime.fromisoformat(tomorrow):%a %b %-d}.")
    if recent:
        w = sum(b["status"] == "won" for b in recent)
        lines.append(f"Settled since yesterday: {w}-{len(recent) - w}, ${sum(b['pnl'] for b in recent):+.2f}.")
    if st["settled"]:
        lines.append(f"Running: {st['won']}-{st['lost']}, ${st['pnl']:+.2f} ({st['cpc']:+.2f}c/contract).")
    title = "Weather paper: " + (f"{len(new)} bet{'s' if len(new) != 1 else ''} for tomorrow" if new else "no bets tomorrow")
    return title, " ".join(lines)


def probe():
    """Live check of the fields the rule reads (no ledger or page changes)."""
    now = datetime.now(timezone.utc)
    for s in C.MAIN:
        ms = kalshi.open_markets(s)
        evs = sorted({m.get("event_ticker") for m in ms})
        q = [m for m in ms if paper.qualifies(m)]
        print(f"{s}: {len(ms)} open brackets, events {evs}, {len(q)} at 1-4c YES bid now")
        for e in evs:
            d = paper.event_date(e)
            real = next(m["close_time"] for m in ms if m.get("event_ticker") == e)
            print(f"   {e}: close {real} expected {paper.close_utc(s, d).isoformat()}")
    m = next(iter(kalshi.open_markets("KXHIGHNY")), None)
    if m:
        print("sample fields:", {k: m.get(k) for k in ("ticker", "status", "yes_sub_title", "strike_type", "floor_strike",
              "cap_strike", "yes_bid_dollars", "no_ask_dollars", "yes_bid_size_fp", "close_time", "volume_fp")})
    y = now.astimezone(PT).date() - timedelta(days=2)
    old = kalshi.market(f"KXHIGHNY-{paper.date_code(y)}-B50.5") or {}
    print("settled lookup:", {k: old.get(k) for k in ("ticker", "status", "result")})
    prev = [m for m in kalshi.open_markets("KXHIGHNY")][:1]
    print("open lookup:", {k: (kalshi.market(prev[0]["ticker"]) or {}).get(k) for k in ("ticker", "status", "result")} if prev else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="allow the daily phone push")
    ap.add_argument("--force-publish", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="no network: rebuild pages only")
    ap.add_argument("--probe", action="store_true", help="print what Kalshi returns for the main series; change nothing")
    a = ap.parse_args()
    if a.probe:
        probe()
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    led, state = paper.load(), read_state()
    added = settled = 0
    if not a.no_fetch:
        print("Scan:")
        added = scan(led, now)
        print("Settle:")
        settled = settle_open(led, now)
        prune_checked(led, now)
        paper.save(led)
    last_pub = state.get("last_publish")
    stale = not last_pub or now - datetime.fromisoformat(last_pub) >= timedelta(minutes=55)
    changed = bool(added or settled or a.force_publish or stale or not os.path.exists(pages.BOARD))
    if changed:
        pages.publish(led, now)
        state["last_publish"] = now.isoformat()
        print(f"Published ({added} new, {settled} settled).")
    now_pt = now.astimezone(PT)
    if (a.send and state.get("pushed") != now_pt.date().isoformat()
            and (now_pt.hour, now_pt.minute) >= (C.PUSH_HOUR_PT, C.PUSH_MINUTE_PT)):
        title, text = push_text(led, now)
        try:
            from nba.notify import send_alert
            send_alert(title, text, click=f"{C.SITE}/wx/", tags="thermometer")
            state["pushed"] = now_pt.date().isoformat()
            print("Push sent:", title)
        except Exception as e:  # never fail the tick over a push
            print("Push failed:", type(e).__name__, e)
    write_state(state)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"changed={'true' if changed or added or settled else 'false'}\n")


if __name__ == "__main__":
    main()
