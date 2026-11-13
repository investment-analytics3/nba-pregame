"""NHL scoring: range filter, Analysis Score, tiers and flags (owner decisions Nov 12-13, 2026; spec "NHL Board").

Range (owner): a game is analyzed only when a TOP-7 team in the consensus power ranking plays a BOTTOM-7 team
(26-32). The top-7 team is the favorite. Every other game is listed under Did not qualify.

Analysis Score (max 40), built like the NFL hero score (32 teams):
  Favorite metrics  16  4 metrics x (season, last 5): rank 1-5 = 2, 6-10 = 1
  Underdog metrics  16  4 x 2: outside the top 10 = 1, 23-32 = 2
  Goalies + health   4  favorite's regular goalie starts 2, underdog starts its backup 1, no favorite key skater out 1
  Home ice           1  favorite at home (none at neutral sites)
  Rest               1  favorite didn't play yesterday
  Last-3 form        2  favorite won 3 = 2, won 2 = 1
Tiers (range games only; cutoffs from the 2024-26 backtest, data/nhl/backtest.json, Nov 13):
  Qualified   score 22+, the favorite's regular goalie starts, and the favorite didn't play yesterday
              (range favorites 22+ with both: 74% won, 85 games; priced 2025-26: 81% vs a 69% moneyline, 31 games)
  Watch list  score 22+ but the favorite starts its backup goalie or is on a back-to-back (70% won, 73 games)
  No track    score under 22 (18-21 won 52% vs a 64% price; under 18 won 52%)
Goalie flag (owner: like the NFL quarterback): the favorite's regular goalie not listed as the probable starter = red
flag and the game is capped at the Watch list. No probable starter posted yet = yellow warning.
Back-to-back (owner: NBA pill rules): favorite on a back-to-back = red pill and capped at the Watch list (range
favorites on a back-to-back won 52% vs a 65% price).
Key skater out (ESPN injury list; injured reserve / long-term IR never count, as the NFL): costs the health point and
shows a yellow warning (one skater moves a hockey game less than a quarterback or an NBA starter).
Live: Qualified + Watch list followed live; phone pushes for Qualified only.
"""
from __future__ import annotations

from .metrics import LABELS, METRICS

N = 32
TOP, BOTTOM_FROM = 7, 26
ELITE, STRONG, WEAK_FROM = 5, 10, 23
QUALIFY_SCORE, WATCH_SCORE = 22, 22   # Watch list = a 22+ game capped by the goalie or back-to-back
CUTOVER = None                 # no early-season hold (backtest Nov 13: see data/nhl/backtest.json rank_stability)
TIER_LABEL = {"qualified": "Qualified", "watch": "Watch list", "out": "No track"}
LIVE_TIERS = ("qualified", "watch")
ALERT_TIERS = ("qualified",)
OUT = {"Out", "Doubtful"}
LONG_TERM = {"Injured Reserve", "Long Term Injured Reserve", "Long-Term Injured Reserve", "Suspension", "Season-Ending"}


def fav_points(rank) -> int:
    return 0 if rank is None else (2 if rank <= ELITE else (1 if rank <= STRONG else 0))


def dog_points(rank) -> int:
    return 0 if rank is None else (2 if rank >= WEAK_FROM else (1 if rank > STRONG else 0))


def band(score) -> str:
    return "green" if score >= QUALIFY_SCORE else ("yellow" if score >= 18 else "red")


def fav_color(rank) -> str:
    return "none" if rank is None else ("green" if rank <= STRONG else ("yellow" if rank <= 22 else "red"))


def dog_color(rank) -> str:
    if rank is None:
        return "none"
    if rank <= ELITE:
        return "deep-red"
    return "red" if rank <= STRONG else ("yellow" if rank <= 22 else "green")


def in_range(fav_rank, dog_rank) -> bool:
    return fav_rank is not None and dog_rank is not None and fav_rank <= TOP and dog_rank >= BOTTOM_FROM


def _r(t: dict, w: str, m: str):
    return ((t.get(w) or {}).get("ranks") or {}).get(m)


def analysis_score(game: dict, fav: dict, dog: dict) -> dict:
    fm = sum(fav_points(_r(fav, w, m)) for m in METRICS for w in ("season", "last5"))
    dm = sum(dog_points(_r(dog, w, m)) for m in METRICS for w in ("season", "last5"))
    goalie = (2 if fav.get("goalie_status") == "regular" else 0) + (1 if dog.get("goalie_status") == "backup" else 0)
    health = 0 if fav.get("key_out") else 1
    wins = str(fav.get("l3_record") or "0-0-0").split("-")[0]
    form = {"3": 2, "2": 1}.get(wins, 0)
    home = 1 if (game.get("favorite") == "home" and not game.get("neutral")) else 0
    rest = 0 if fav.get("b2b") else 1
    parts = [
        {"key": "fav_metrics", "label": "Favorite metrics", "points": fm, "max": 16},
        {"key": "dog_metrics", "label": "Underdog metrics", "points": dm, "max": 16},
        {"key": "goalies", "label": "Goalies + health", "points": goalie + health, "max": 4},
        {"key": "home", "label": "Home ice", "points": home, "max": 1},
        {"key": "rest", "label": "Rest", "points": rest, "max": 1},
        {"key": "form", "label": "Last-3 form", "points": form, "max": 2},
    ]
    total = sum(p["points"] for p in parts)
    return {"score": total, "band": band(total), "parts": parts, "max": 40}


def early_season(day: str) -> bool:
    return bool(CUTOVER) and "07-01" <= day[5:10] < CUTOVER


def score_game(game: dict, fav: dict, dog: dict, day: str, in_range: bool | None = None) -> dict:
    fa, da = fav.get("abbr", "Favorite"), dog.get("abbr", "Underdog")
    fr, dr = (fav.get("power") or {}).get("rank"), (dog.get("power") or {}).get("rank")
    rng = globals()["in_range"](fr, dr) if in_range is None else in_range
    a = analysis_score(game, fav, dog)
    total = a["score"]
    goalie_cap = fav.get("goalie_status") == "backup"
    b2b_cap = bool(fav.get("b2b"))
    early = early_season(day)
    if not rng:
        tier, routes = "out", []
    elif total >= QUALIFY_SCORE and not goalie_cap and not b2b_cap and not early:
        tier, routes = "qualified", [f"Score {total}"]
    elif total >= WATCH_SCORE:
        tier, routes = "watch", []
    else:
        tier, routes = "out", []
    would = rng and total >= QUALIFY_SCORE and tier != "qualified"

    gs = fav.get("goalie") or {}
    rules = [
        {"rule": "Range", "name": f"Top-{TOP} team vs bottom-{N - BOTTOM_FROM + 1} team (consensus power rank)", "pass": rng,
         "detail": f"{fa} #{fr if fr is not None else '–'} vs {da} #{dr if dr is not None else '–'}"},
        {"rule": "A", "name": f"Analysis Score {QUALIFY_SCORE}+", "pass": total >= QUALIFY_SCORE, "detail": f"Score {total} of 40"},
        {"rule": "Goalie", "name": f"{fa}'s regular goalie starts", "pass": not goalie_cap,
         "detail": (f"{gs.get('name') or 'Backup'} listed instead of {fav.get('regular_goalie_name') or 'the regular starter'}: capped at the Watch list"
                    if goalie_cap else (f"{gs.get('name')} ({'confirmed' if gs.get('confirmed') else 'probable'})" if gs.get("name")
                                        else "No probable starter posted yet"))},
        {"rule": "Rest", "name": f"{fa} didn't play yesterday", "pass": not b2b_cap,
         "detail": f"{fa} on a back-to-back: capped at the Watch list" if b2b_cap else "Rested"},
    ]
    warnings, flags = [], []
    if goalie_cap:
        flags.append(f"{fa} backup goalie starting ({gs.get('name') or 'not the regular starter'})")
    elif fav.get("goalie_status") == "unknown":
        warnings.append(f"{fa} starting goalie not posted yet: check before betting")
    if dog.get("goalie_status") == "backup":
        warnings.append(f"Info: {da} starting its backup goalie ({(dog.get('goalie') or {}).get('name') or '?'})")
    for p in fav.get("key_out") or []:
        warnings.append(f"{fa} key skater out: {p}")
    if fav.get("b2b"):
        warnings.append(f"{fa} played yesterday (back-to-back)")
    if not fav.get("injuries_known", True):
        warnings.append(f"No injury list for {fa}: check news before betting")

    if tier == "qualified":
        fails = []
    elif not rng:
        fails = [f"Not a top-{TOP} vs bottom-{N - BOTTOM_FROM + 1} matchup ({fa} #{fr if fr is not None else '–'}, {da} #{dr if dr is not None else '–'})"]
    elif (goalie_cap or b2b_cap) and total >= QUALIFY_SCORE:
        why = " and ".join(x for x, on in ((f"{fa}'s backup goalie starts", goalie_cap), (f"{fa} played yesterday", b2b_cap)) if on)
        fails = [f"Would qualify (score {total}) but {why}: Watch list"]
    elif early and total >= QUALIFY_SCORE:
        fails = [f"Early season: would qualify (score {total}); held on the Watch list"]
    elif tier == "watch":
        fails = [f"Watch list: score {total} (qualifies at {QUALIFY_SCORE})"]
    else:
        fails = [f"Score {total} (qualifies at {QUALIFY_SCORE})"]

    pills = []
    for team, role, w in ((fav, "fav", "season"), (fav, "fav", "last5"), (dog, "dog", "season"), (dog, "dog", "last5")):
        cells = [{"metric": m, "rank": _r(team, w, m), "color": fav_color(_r(team, w, m)) if role == "fav" else dog_color(_r(team, w, m))}
                 for m in METRICS]
        pills.append({"team": team.get("abbr"), "role": role, "window": w, "cells": cells})

    return {"scored": True, "fav": fa, "dog": da, "in_range": rng, "qualified": tier == "qualified", "tier": tier,
            "tier_label": TIER_LABEL[tier], "tracked": tier in LIVE_TIERS, "alerts": tier in ALERT_TIERS,
            "routes": routes, "route": " + ".join(routes), "qualifiers": [], "would_qualify": bool(would),
            "rules": rules, "fails": fails, "warnings": warnings, "flags": flags,
            "score": total, "max": 40, "band": a["band"], "parts": a["parts"], "pills": pills, "labels": LABELS}


def order_games(games: list[dict]) -> list[dict]:
    order = {"qualified": 0, "watch": 1, "out": 2}
    return sorted(games, key=lambda g: (order.get((g.get("scoring") or {}).get("tier"), 2),
                                        -((g.get("scoring") or {}).get("score") or -1), g.get("kick_utc") or ""))
