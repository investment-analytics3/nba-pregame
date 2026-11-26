"""Tennis sources. Network calls are thin; the parsers are pure functions (tested on saved samples).

Kalshi (public, read-only): one event per match with two markets (one per player). `occurrence_datetime` = listed start
(an estimate), `yes_ask_dollars` = price to buy, settled markets carry `result` yes/no ("scalar" = void, e.g. walkover)
and `expiration_value` = winner's name. `rules_primary` names the tournament and round.
Tennis Abstract: Elo table (weekly, with each player's page key) and each player's summary file (`jsfrags/<key>.js`):
season serve/return (SPW, RPW, DR by year, tour and Challenger level) and the last ~20 matches with minutes.
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from datetime import date, datetime

from nba.http import FetchError, get

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KH = {"User-Agent": "tennis-board/1.0"}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
MON = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


# ---- names ---------------------------------------------------------------------------------------
def get_text(url: str, **kw) -> str | None:
    raw = get(url, want="bytes", **kw)
    return raw.decode("utf-8", "replace") if raw is not None else None


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", html.unescape(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ")).strip()


def last_name(name: str) -> str:
    parts = norm(name).split()
    return parts[-1] if parts else ""


# ---- Kalshi --------------------------------------------------------------------------------------
def kalshi_events(series: str, status: str = "open", max_pages: int = 5, stop=None, pace: float = 0.6) -> list[dict]:
    """Events with nested markets, newest first. `stop(event) -> True` ends paging (settled history catch-up)."""
    out, cursor = [], ""
    for _ in range(max_pages):
        url = f"{KALSHI}/events?series_ticker={series}&status={status}&with_nested_markets=true&limit=200"
        body = get(url + (f"&cursor={cursor}" if cursor else ""), headers=KH, retries=2, pace=pace) or {}
        evs = body.get("events") or []
        for e in evs:
            if stop and stop(e):
                return out
            out.append(e)
        cursor = body.get("cursor") or ""
        if not cursor or not evs:
            break
    return out


def kalshi_event(event_ticker: str) -> dict | None:
    try:
        body = get(f"{KALSHI}/events/{event_ticker}?with_nested_markets=true", headers=KH, retries=2, pace=0.6) or {}
    except FetchError:
        return None
    ev = body.get("event") or {}
    if body.get("markets") and not ev.get("markets"):
        ev["markets"] = body["markets"]
    return ev or None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_rules(rules: str) -> tuple[str, str]:
    """'... match in the 2026 M25 Bhopal Quarterfinal after a ball ...' -> ('M25 Bhopal', 'Quarterfinal')."""
    m = re.search(r"match in the (.+?) after a ball", rules or "")
    if not m:
        return "", ""
    text = re.sub(r"^\d{4} ", "", m.group(1)).strip()
    rd = re.search(r"\s((?:Qualifying|Qualification)\b.*|Round [Oo]f \d+.*|Round Robin.*|(?:First|Second|Third|Fourth) Round.*|"
                   r"Quarterfinals?.*|Semifinals?.*|Finals?|Final.*|Round \d+.*)$", text)
    if rd:
        return text[:rd.start()].strip(), rd.group(1).strip()
    return text, ""


def match_from_event(e: dict) -> dict | None:
    """Normalize a Kalshi match event: two players with prices, listed start, tournament, round, result."""
    ms = [m for m in (e.get("markets") or []) if m.get("yes_sub_title")]
    if len(ms) != 2:
        return None
    players = []
    for m in ms:
        players.append({"name": m["yes_sub_title"].strip(), "ticker": m["ticker"],
                        "ask": num(m.get("yes_ask_dollars")), "bid": num(m.get("yes_bid_dollars")),
                        "last": num(m.get("last_price_dollars")), "result": (m.get("result") or "").lower()})
    m0 = ms[0]
    tourney, rnd = parse_rules(m0.get("rules_primary", ""))
    res = {p["result"] for p in players}
    winner = next((p["name"] for p in players if p["result"] == "yes"), None)
    return {"event": e.get("event_ticker"), "series": e.get("series_ticker") or e.get("event_ticker", "").split("-")[0],
            "title": e.get("title"), "start": m0.get("occurrence_datetime") or m0.get("expected_expiration_time"),
            "tourney": tourney, "round": rnd, "players": players, "status": m0.get("status"),
            "winner": winner, "void": "scalar" in res, "settled_ts": m0.get("settlement_ts")}


def price_of(p: dict) -> float | None:
    """Price to buy this player now: the ask; if there is no ask, the last trade."""
    a = p.get("ask")
    if a and 0 < a < 1:
        return a
    last = p.get("last")
    return last if last and 0 < last < 1 else None


def moneyline(p: float) -> int:
    """Kalshi price (0-1) -> American odds: 0.93 -> -1329, 0.20 -> +400."""
    if p >= 0.5:
        return -round(100 * p / (1 - p))
    return round(100 * (1 - p) / p)


# ---- Tennis Abstract Elo ---------------------------------------------------------------------------
def cells(row: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def parse_elo(text: str) -> dict:
    """{"updated": "2026-11-23", "players": {norm name: {...}}} from a Tennis Abstract Elo report."""
    up = re.search(r"Last update:\s*([0-9-]+)", text)
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        c = cells(row)
        if len(c) < 16 or not c[0].isdigit():
            continue
        key = re.search(r"\?p=([A-Za-z0-9]+)", row)
        f = lambda i: num(c[i])  # noqa: E731
        name = c[1]
        out[norm(name)] = {"name": name, "key": key.group(1) if key else re.sub(r"[^A-Za-z]", "", name),
                           "elo_rank": int(c[0]), "elo": f(3), "hard": f(6), "clay": f(8), "grass": f(10),
                           "rank": int(c[15]) if c[15].isdigit() else None}
    return {"updated": up.group(1) if up else None, "players": out}


def fetch_elo(gender: str) -> dict:
    from tennis import config as C
    text = get_text(C.ELO_URL[gender], headers=UA, retries=2, pace=1.0)
    return parse_elo(text or "")


# ---- Tennis Abstract player file ------------------------------------------------------------------
def _table(text: str, tid: str) -> list[list[str]]:
    i = text.find(f'<table id="{tid}"')
    if i < 0:
        return []
    seg = text[i:text.find("</table>", i)]
    head = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip() for c in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]
    return [head] + [cells(r) for r in re.findall(r"<tr>(.*?)</tr>", seg, re.S)[1:]]


def _pct(s):
    v = num((s or "").rstrip("%"))
    return v


def _minutes(s):
    m = re.match(r"(\d+):(\d\d)", s or "")
    return int(m[1]) * 60 + int(m[2]) if m else None


def _day(s):
    m = re.match(r"(\d\d)-([A-Z][a-z]{2})-(\d{4})", s or "")
    return date(int(m[3]), MON[m[2]], int(m[1])).isoformat() if m else None


def parse_frags(text: str, last: str, year: int) -> dict:
    """Season serve/return (SPW + RPW, tour + Challenger, weighted by matches with stats) and recent matches.

    `last` = the player's last name as Tennis Abstract writes it in result lines ("(Q)Nardi d. ...").
    """
    season = {"m": 0}
    for tid in ("tour-years", "chall-years"):
        t = _table(text, tid)
        if not t:
            continue
        h = t[0]
        for r in t[1:]:
            if r and r[0] == str(year) and len(r) == len(h):
                d = dict(zip(h, r))
                ms = int(d.get("MS") or 0)
                spw, rpw, dr = _pct(d.get("SPW")), _pct(d.get("RPW")), num(d.get("DR"))
                if ms and spw is not None and rpw is not None:
                    for k, v in (("spw", spw), ("rpw", rpw), ("dr", dr or 0)):
                        season[k] = season.get(k, 0) + v * ms
                    season["m"] += ms
                season["wl"] = [season.get("wl", [0, 0])[0] + int(d.get("W") or 0), season.get("wl", [0, 0])[1] + int(d.get("L") or 0)]
    if season["m"]:
        for k in ("spw", "rpw", "dr"):
            season[k] = round(season[k] / season["m"], 2)
        season["sr"] = round(season["spw"] + season["rpw"], 1)
    recent = []
    t = _table(text, "recent-results")
    lastn = norm(last)
    for r in t[1:] if t else []:
        if len(r) < 16:
            continue
        line = r[6]
        m = re.match(r"(.*?) d\. (.*)", line)
        if not m:
            continue
        won = lastn and lastn in norm(re.sub(r"\(.*?\)", "", m.group(1)))
        opp = m.group(2) if won else m.group(1)
        opp = re.sub(r"\[[A-Z]{3}\]|\([^)]*\)", "", opp).strip()
        recent.append({"date": _day(r[0]), "tourney": r[1], "surface": r[2], "round": r[3],
                       "rank": int(r[4]) if r[4].isdigit() else None, "opp_rank": int(r[5]) if r[5].isdigit() else None,
                       "won": bool(won), "opp": opp, "score": r[7], "dr": num(r[8]), "minutes": _minutes(r[15])})
    return {"season": season if season["m"] else None, "recent": recent}


def fetch_frags(key: str) -> str | None:
    from tennis import config as C
    try:
        return get_text(C.FRAGS_URL.format(key=key), headers=UA, retries=1, pace=2.0)
    except FetchError:
        return None


# ---- analyst previews --------------------------------------------------------------------------------
def parse_lwos(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'href="(https://lastwordonsports\.com/tennis/20\d\d/[^"]+)"', text or "")))


def preview_link(links: list[str], a: str, b: str) -> str | None:
    la, lb = last_name(a).replace(" ", "-"), last_name(b).replace(" ", "-")
    for u in links:
        slug = u.rstrip("/").rsplit("/", 1)[-1]
        if la and lb and la in slug and lb in slug:
            return u
    return None


def dumps(o) -> str:
    return json.dumps(o, separators=(",", ":"))


_ = (time, datetime)
