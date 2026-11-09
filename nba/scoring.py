"""Qualification tiers and the 40-point hero score.

Everything is from the favorite's point of view (favorite = spread favorite;
moneyline breaks a pick'em). Ranks are 1-30 with 1 = best for every metric.

Tiers (since Nov 9):
  Qualified   (A) Analysis Score 28+, or
              (B) all-four mismatch: favorite top 10 in all four metrics AND
                  underdog bottom 10 (rank 21-30) in all four, for both the
                  season and the last 3.
  Watch list  Analysis Score 18-27 and not qualified.
  No track    everything else.
Injury gate on every tier: the favorite has no key player Out or Doubtful
(Questionable = yellow warning) and its injury report is available; otherwise
No track. Underdog injuries are information only.

The earlier six-rule checks (favorite top 10 in a metric for season and last 3,
underdog not hot over its last 3, underdog not 3-0) are still reported as
context, and "qualifiers" still lists the metrics where the favorite is top 10
for both windows, but they no longer decide the tier.

Hero score (max 40):
  Favorite metrics   4 metrics x (season, last 3): top 5 = 2, top 10 = 1   16
  Underdog metrics   4 metrics x (season, last 3): bottom 5 = 2, bottom 10 = 1   16
  Net rating         favorite season rank: top 5 = 2, top 10 = 1           2
  Last-3 form        favorite 3-0 = 2, 2-1 = 1                             2
  Power rank         favorite top 10 AND underdog bottom 10                1
  Home court         favorite at home (not at a neutral site)              1
  Health             favorite has no key player out                        1
  Rest               favorite not on a back-to-back                        1
"""
from __future__ import annotations

METRICS = ("efg", "off", "opp_efg", "def")
NAMES = {"efg": "eFG%", "off": "Off Eff", "opp_efg": "Opp eFG%", "def": "Def Eff"}
# Each pill column pairs a favorite metric with the underdog metric it runs into.
MATCHUPS = [("efg", "opp_efg"), ("off", "def"), ("opp_efg", "efg"), ("def", "off")]
MAX_SCORE = 40
BANDS = [(28, "green"), (18, "yellow"), (0, "red")]
QUALIFY_SCORE = 28
WATCH_SCORE = 18
TIER_LABEL = {"qualified": "Qualified", "watch": "Watch list", "out": "No track"}
# Tiers the live tracker follows (end-of-3rd checklist + BET signal pushes, Live page, L1 live chips).
LIVE_TIERS = ("qualified",)


def _rank(team: dict, window: str, metric: str):
    return ((team.get(window) or {}).get("ranks") or {}).get(metric)


def fav_points(rank) -> int:
    return 2 if rank is not None and rank <= 5 else (1 if rank is not None and rank <= 10 else 0)


def dog_points(rank) -> int:
    return 2 if rank is not None and rank >= 26 else (1 if rank is not None and rank >= 21 else 0)


def band(score: int) -> str:
    return next(name for floor, name in BANDS if score >= floor)


def fav_color(rank) -> str:
    if rank is None:
        return "none"
    return "green" if rank <= 10 else ("yellow" if rank <= 20 else "red")


def dog_color(rank) -> str:
    """Flipped: a strong underdog is a threat."""
    if rank is None:
        return "none"
    if rank <= 5:
        return "deep-red"
    return "red" if rank <= 10 else ("yellow" if rank <= 20 else "green")


def score_game(game: dict) -> dict:
    side = game.get("favorite")
    if side not in ("home", "away"):
        return {"scored": False, "qualified": False, "tracked": False, "tier": "out", "tier_label": TIER_LABEL["out"], "routes": [], "route": "",
                "reason": "No spread posted yet",
                "fails": ["No spread posted yet"], "warnings": [], "score": None}
    fav = game[side]
    dog = game["home" if side == "away" else "away"]
    fails, warnings, rules = [], [], []

    # ---- context checks (the earlier six rules; no longer decide the tier) ---
    season_top10 = [m for m in METRICS if (_rank(fav, "season", m) or 99) <= 10]
    qualifiers = [m for m in season_top10 if (_rank(fav, "last3", m) or 99) <= 10]
    hot = [m for m in METRICS if (_rank(dog, "last3", m) or 99) <= 10]
    dog_hot_streak = dog.get("l3_record") == "3-0"
    context = [
        {"check": "Favorite top 10 in a metric, season and last 3", "pass": bool(qualifiers),
         "detail": ", ".join(NAMES[m] for m in qualifiers) or "none"},
        {"check": "Underdog not top 10 in any metric over its last 3", "pass": not hot,
         "detail": ", ".join(NAMES[m] for m in hot) or "none"},
        {"check": "Underdog not 3-0 in its last 3", "pass": not dog_hot_streak, "detail": dog.get("l3_record")},
    ]

    # ---- injury gate (every tier) -------------------------------------------
    key_out = fav.get("key_out") or []
    injury_known = fav.get("injuries_known", True)
    injury_fails = []
    if key_out:
        injury_fails.append(f"Favorite key player out: {', '.join(key_out)}")
    if not injury_known:
        injury_fails.append("Injury report unavailable for the favorite")
    for p in fav.get("key_questionable") or []:
        warnings.append(f"{fav['abbr']} key player questionable: {p}")

    # information-only notes
    if dog.get("key_out"):
        warnings.append(f"Info: {dog['abbr']} without {', '.join(dog['key_out'])}")
    for t in (fav, dog):
        if (t.get("power") or {}).get("split"):
            warnings.append(f"{t['abbr']} power rank: NBA.com disagrees enough to change its top/bottom-10 group")
    if game.get("neutral"):
        warnings.append("Neutral-site game: no home-court point")

    # ---- hero score ---------------------------------------------------------
    fm = sum(fav_points(_rank(fav, w, m)) for m in METRICS for w in ("season", "last3"))
    dm = sum(dog_points(_rank(dog, w, m)) for m in METRICS for w in ("season", "last3"))
    net = fav_points(_rank(fav, "season", "net"))
    form = {"3-0": 2, "2-1": 1}.get(fav.get("l3_record"), 0)
    fp, dp = (fav.get("power") or {}).get("rank"), (dog.get("power") or {}).get("rank")
    power = 1 if (fp is not None and dp is not None and fp <= 10 and dp >= 21) else 0
    home = 1 if (side == "home" and not game.get("neutral")) else 0
    health = 0 if key_out else 1
    rest = 0 if fav.get("b2b") else 1
    parts = [
        {"key": "fav_metrics", "label": "Favorite metrics", "points": fm, "max": 16},
        {"key": "dog_metrics", "label": "Underdog metrics", "points": dm, "max": 16},
        {"key": "net", "label": "Net rating", "points": net, "max": 2},
        {"key": "form", "label": "Last-3 form", "points": form, "max": 2},
        {"key": "power", "label": "Power rank", "points": power, "max": 1},
        {"key": "home", "label": "Home court", "points": home, "max": 1},
        {"key": "health", "label": "Health", "points": health, "max": 1},
        {"key": "rest", "label": "Rest", "points": rest, "max": 1},
    ]
    total = sum(p["points"] for p in parts)

    # ---- tier -------------------------------------------------------------------
    misses = [f"{fav['abbr']} {NAMES[m]} {'season' if w == 'season' else 'last 3'} #{_rank(fav, w, m) or '–'}"
              for w in ("season", "last3") for m in METRICS if (_rank(fav, w, m) or 99) > 10]
    misses += [f"{dog['abbr']} {NAMES[m]} {'season' if w == 'season' else 'last 3'} #{_rank(dog, w, m) or '–'}"
               for w in ("season", "last3") for m in METRICS if (_rank(dog, w, m) or 0) < 21]
    mismatch = not misses
    by_score = total >= QUALIFY_SCORE
    routes = (["Score " + str(total)] if by_score else []) + (["All-four mismatch"] if mismatch else [])
    if injury_fails:
        tier = "out"
    elif routes:
        tier = "qualified"
    elif total >= WATCH_SCORE:
        tier = "watch"
    else:
        tier = "out"
    rules = [
        {"rule": "A", "name": "Analysis Score 28+", "pass": by_score, "detail": f"Score {total} of {MAX_SCORE}"},
        {"rule": "B", "name": "All-four mismatch (season and last 3)", "pass": mismatch,
         "detail": "Favorite top 10 and underdog bottom 10 in all four" if mismatch
                   else f"{16 - len(misses)} of 16 ranks; misses: " + ", ".join(misses[:4]) + (" …" if len(misses) > 4 else "")},
        {"rule": "Injury", "name": "No key player out for the favorite", "pass": not injury_fails,
         "detail": "; ".join(injury_fails) or "Clear"},
    ]
    if tier == "qualified":
        fails = []
    elif injury_fails:
        fails = injury_fails + ([f"Would be {'qualified' if routes else 'on the watch list'} otherwise (score {total})"]
                                if routes or total >= WATCH_SCORE else [])
    elif tier == "watch":
        fails = [f"Watch list: score {total} (qualifies at {QUALIFY_SCORE})"]
    else:
        fails = [f"Score {total} (watch list from {WATCH_SCORE}, qualifies at {QUALIFY_SCORE})"]

    # ---- pill grid (L1) ------------------------------------------------------
    pills = []
    for team, role, window in ((fav, "fav", "season"), (fav, "fav", "last3"),
                               (dog, "dog", "season"), (dog, "dog", "last3")):
        cells = []
        for fav_m, dog_m in MATCHUPS:
            m = fav_m if role == "fav" else dog_m
            r = _rank(team, window, m)
            cells.append({"metric": m, "rank": r,
                          "color": fav_color(r) if role == "fav" else dog_color(r)})
        pills.append({"team": team["abbr"], "role": role, "window": window, "cells": cells})

    return {
        "scored": True, "favorite_side": side, "fav": fav["abbr"], "dog": dog["abbr"],
        "qualified": tier == "qualified", "tier": tier, "tier_label": TIER_LABEL[tier], "tracked": tier in LIVE_TIERS,
        "routes": routes, "route": " + ".join(routes),
        "fails": fails, "warnings": warnings, "rules": rules, "context": context,
        "qualifiers": [NAMES[m] for m in qualifiers],
        "score": total, "max": MAX_SCORE, "band": band(total), "parts": parts, "pills": pills,
    }


def order_games(games: list[dict]) -> list[dict]:
    """Qualified, then watch list, then the rest; each by score (high to low), then tip time."""
    order = {"qualified": 0, "watch": 1, "out": 2}
    def key(g):
        s = g.get("scoring") or {}
        return (order.get(s.get("tier"), 2), -(s.get("score") or -1), g.get("tip_utc") or "")
    return sorted(games, key=key)
