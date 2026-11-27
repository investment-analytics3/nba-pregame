#!/usr/bin/env python3
"""Tennis tick (tennis.yml, started by the NBA Scheduler every 15 minutes). Tracking only: no pushes, never trades.

  refresh  Tennis Abstract Elo (daily), Last Word On Tennis preview links (hourly)
  scan     open Kalshi singles matches (ATP, WTA, Challengers, ITF); favorites at -800 or shorter starting within 30 h
  players  Tennis Abstract summary files for main-tab players (cached 2 days, a few per tick)
  log      each match once, 5-8 h (ITF 10-13 h) before Kalshi's listed time, which runs late; frozen in data/tennis/ledger.json
  history  newly settled Kalshi matches (hourly): ITF form, played yesterday, travel, grading
  grade    logged matches from Kalshi's result (void = walkover / retirement before a ball)
  publish  docs/tennis/index.html, results.html, results.csv, summary.json (on change, else hourly)

    python scripts/tennis_tick.py [--force-publish] [--no-fetch]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba.http import FetchError  # noqa: E402
from tennis import config as C  # noqa: E402
from tennis import ledger as L  # noqa: E402
from tennis import pages  # noqa: E402
from tennis import scoring  # noqa: E402
from tennis import sources as S  # noqa: E402
from tennis.history import History  # noqa: E402

DATA = L.DATA


def jload(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(name, obj, indent=None):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, indent=indent, separators=None if indent else (",", ":"))


def older(ts, hours, now):
    return not ts or now - datetime.fromisoformat(ts) >= timedelta(hours=hours)


def refresh(state, now, log=print):
    for g in ("m", "w"):
        if older(state.get(f"elo_{g}"), 20, now):
            try:
                e = S.fetch_elo(g)
                if len(e["players"]) > 100:
                    jsave(f"elo_{g}.json", e)
                    state[f"elo_{g}"] = now.isoformat()
                    log(f"  Elo {g}: {len(e['players'])} players, updated {e['updated']}")
            except FetchError as ex:
                log(f"  Elo {g} failed: {ex}")
    if older(state.get("lwos"), 1, now):
        try:
            links = S.parse_lwos(S.get_text(C.LWOS_URL, headers=S.UA, retries=1) or "")
            if links:
                jsave("previews.json", links[:60])
            state["lwos"] = now.isoformat()
        except FetchError as ex:
            log(f"  previews failed: {ex}")


def scan(now, log=print) -> list[dict]:
    """Open matches starting within 30 h (or started within 3 h) whose favorite is at -800 or shorter."""
    out = []
    for series in C.SERIES:
        try:
            evs = S.kalshi_events(series, status="open", max_pages=3)
        except FetchError as ex:
            log(f"  {series}: {ex}")
            continue
        n = 0
        for e in evs:
            m = S.match_from_event(e)
            if not m or not m["start"]:
                continue
            st = datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
            h = (st - now).total_seconds() / 3600
            if not (-3 <= h <= 36):
                continue
            prices = [S.price_of(p) or 0 for p in m["players"]]
            fav = 0 if prices[0] >= prices[1] else 1
            if prices[fav] >= C.GATE_PRICE - 1e-9 and prices[fav] < 1:
                m["fav_i"], m["hours"] = fav, round(h, 2)
                out.append(m)
                n += 1
        log(f"  {series}: {len(evs)} open, {n} at -800 or shorter")
    return out


def ensure_players(cands, elo, players, now, budget, log=print):
    """Tennis Abstract files for main-tab players, closest matches first."""
    want = []
    for m in sorted(cands, key=lambda m: m["hours"]):
        tab, _, g = C.SERIES[m["series"]]
        if tab != "main":
            continue
        for p in m["players"]:
            e = (elo.get(g) or {}).get("players", {}).get(S.norm(p["name"]))
            if e and older((players.get(e["key"]) or {}).get("fetched"), C.PLAYER_TTL_H, now) and e["key"] not in [w[0] for w in want]:
                want.append((e["key"], e["name"]))
    got = 0
    for key, name in want[:budget]:
        text = S.fetch_frags(key)
        if text and "recent-results" in text:
            parsed = S.parse_frags(text, name.split()[-1], now.year)
            players[key] = {"fetched": now.isoformat(), **parsed}
            got += 1
        elif text is not None:
            players[key] = {"fetched": now.isoformat(), "season": None, "recent": []}
    if want:
        log(f"  players: {got} fetched, {max(0, len(want) - budget)} waiting")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-publish", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = jload("state.json", {})
    led = L.load()
    hist = History.load()
    players = jload("players.json", {})
    elo = {g: jload(f"elo_{g}.json", {}) for g in ("m", "w")}
    cands, logged, graded = [], 0, 0
    if not a.no_fetch:
        print("Refresh:")
        refresh(state, now)
        elo = {g: jload(f"elo_{g}.json", {}) for g in ("m", "w")}
        print("Scan:")
        cands = scan(now)
        ensure_players(cands, elo, players, now, C.FRAGS_PER_TICK)
        if older(state.get("history"), 1, now):
            print("History:")
            hist.update(now.date(), max_pages=4 if state.get("history") else 15)
            hist.save(now.date())
            state["history"] = now.isoformat()
    ctx = {"elo": elo, "ta": players, "hist": hist, "previews": jload("previews.json", [])}
    scored = []
    have = {r["event"] for r in led["matches"]}
    for m in cands:
        try:
            sc = scoring.score_match(m, m["fav_i"], ctx)
        except Exception as ex:  # one odd match never stops the tick
            print(f"  score {m['event']}: {type(ex).__name__} {ex}")
            continue
        sc["hours"] = m["hours"]
        scored.append(sc)
        lo, hi = C.LOG_WINDOW_H[sc["tab"]]
        if m["event"] not in have and C.LATE_LOG_H[sc["tab"]] <= m["hours"] <= hi and sc["price"]:
            led["matches"].append(L.entry(sc, now, late=m["hours"] < lo))
            have.add(m["event"])
            logged += 1
            print(f"  logged {m['event']} {sc['fav']['name']} {sc['ml']} ({sc['tier_label']}, {sc['score']}/{sc['max']})")
    # grade from history; ask Kalshi directly for anything 8 h+ past its listed start that history lacks
    direct = 0
    for r in led["matches"]:
        if r["status"] != "open":
            continue
        h = hist.rows.get(r["event"])
        if h:
            L.grade(r, h["w"], h["v"], now, settled_ts=h.get("ts"))
        elif not a.no_fetch and direct < 20 and now - datetime.fromisoformat(r["start"].replace("Z", "+00:00")) > timedelta(hours=8):
            direct += 1
            e = S.kalshi_event(r["event"])
            m = S.match_from_event(e) if e else None
            if m and (m["winner"] or m["void"]):
                L.grade(r, m["winner"], m["void"], now, settled_ts=m.get("settled_ts"))
        graded += r["status"] != "open"
    if not a.no_fetch:
        L.save(led)
        jsave("players.json", {k: v for k, v in players.items() if not older(v.get("fetched"), 24 * 21, now)})
    # publish when the board's content changes, else hourly
    sig = hashlib.md5(json.dumps([[s["event"], s["section"], s["ml"]] for s in scored] +
                                 [[r["event"], r["status"]] for r in led["matches"]], sort_keys=True).encode()).hexdigest()
    if a.force_publish or sig != state.get("sig") or older(state.get("published"), 1, now) or not os.path.exists(pages.BOARD):
        pages.publish(led, scored, elo, now)
        state.update(sig=sig, published=now.isoformat())
        print(f"Published ({len(scored)} on the board, {logged} logged).")
    jsave("state.json", state, indent=1)


if __name__ == "__main__":
    main()
