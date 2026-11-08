"""Qualification rules and the 40-point hero score.

Everything is from the favorite's point of view (favorite = spread favorite;
moneyline breaks a pick'em). Ranks are 1-30 with 1 = best for every metric.

Qualification (all six must pass):
 1. Favorite top 10 (season) in at least one of the four metrics.
 2. Favorite also top 10 over its last 3 in that same metric.
 3. Underdog not top 10 in any of the four metrics over its last 3.
 4. Underdog not 3-0 in its last 3.
 5. Favorite has no key player Out or Doubtful. (Questionable = yellow warning.)
 6. Injury information is available for the favorite. (Underdog injuries are
    information only and never fail the game.)

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
        return {"scored": False, "qualified": False, "reason": "No spread posted yet",
                "fails": ["No spread posted yet"], "warnings": [], "score": None}
    fav = game[side]
    dog = game["home" if side == "away" else "away"]
    fails, warnings, rules = [], [], []

    # ---- qualification -----------------------------------------------------
    season_top10 = [m for m in METRICS if (_rank(fav, "season", m) or 99) <= 10]
    qualifiers = [m for m in season_top10 if (_rank(fav, "last3", m) or 99) <= 10]
    rules.append({"rule": 1, "pass": bool(season_top10),
                  "detail": "Season top 10 in " + (", ".join(NAMES[m] for m in season_top10) or "none")})
    rules.append({"rule": 2, "pass": bool(qualifiers),
                  "detail": "Also top 10 over last 3 in " + (", ".join(NAMES[m] for m in qualifiers) or "none")})
    if not season_top10:
        fails.append("Favorite not top 10 in any metric this season")
    elif not qualifiers:
        fails.append("Favorite not top 10 over last 3 in a season top-10 metric")

    hot = [m for m in METRICS if (_rank(dog, "last3", m) or 99) <= 10]
    rules.append({"rule": 3, "pass": not hot,
                  "detail": "Underdog last-3 top 10: " + (", ".join(NAMES[m] for m in hot) or "none")})
    if hot:
        fails.append(f"Underdog top 10 over last 3 in {', '.join(NAMES[m] for m in hot)}")

    dog_hot_streak = dog.get("l3_record") == "3-0"
    rules.append({"rule": 4, "pass": not dog_hot_streak, "detail": f"Underdog last 3: {dog.get('l3_record')}"})
    if dog_hot_streak:
        fails.append("Underdog is 3-0 in its last 3")

    key_out = fav.get("key_out") or []
    rules.append({"rule": 5, "pass": not key_out,
                  "detail": "Key out: " + (", ".join(key_out) or "none")})
    if key_out:
        fails.append(f"Favorite key player out: {', '.join(key_out)}")
    for p in fav.get("key_questionable") or []:
        warnings.append(f"{fav['abbr']} key player questionable: {p}")

    injury_known = fav.get("injuries_known", True)
    rules.append({"rule": 6, "pass": injury_known,
                  "detail": "Injury report available" if injury_known else "No injury report for the favorite"})
    if not injury_known:
        fails.append("Injury report unavailable for the favorite")

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
        "qualified": not fails, "fails": fails, "warnings": warnings, "rules": rules,
        "qualifiers": [NAMES[m] for m in qualifiers],
        "score": total, "max": MAX_SCORE, "band": band(total), "parts": parts, "pills": pills,
    }


def order_games(games: list[dict]) -> list[dict]:
    """Qualified first, then by score (high to low), then tip time."""
    def key(g):
        s = g.get("scoring") or {}
        return (not s.get("qualified"), -(s.get("score") or -1), g.get("tip_utc") or "")
    return sorted(games, key=key)
