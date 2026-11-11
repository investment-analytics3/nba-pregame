"""College basketball scoring: the 40-point Analysis Score, tiers and gates (owner decisions Nov 11, 2026).

Which games: men's Division I vs Division I, spread favorite giving 10 to 20 points (both ends included).
Ranks are among D-I teams (about 365): strong = 1-50 (elite = 1-20), weak = the bottom 50 (316-365).

Analysis Score (max 40), same parts as the NBA board with college cutoffs:
  Favorite metrics  16  4 metrics x (season, last 3): rank 1-20 = 2, 21-50 = 1
  Underdog metrics  16  4 x 2: outside the top 50 = 1, bottom 50 (316+) = 2   (as college football)
  Net rating         2  favorite season rank 1-20 = 2, 21-50 = 1
  Last-3 form        2  favorite 3-0 = 2, 2-1 = 1
  Power rank         1  favorite consensus top 50 AND underdog consensus bottom 50
  Home court         1  favorite at home (none at neutral sites)
  Health             1  no favorite key player out
  Rest               1  favorite didn't play yesterday
Tiers
  Qualified   score 28+ (route A) OR last-3 mismatch (route B): last 3, favorite top 50 in >= 3 of 4 AND underdog
              bottom 50 in >= 3 of 4; season, favorite top half (1-182) in all four AND underdog top 50 in none
  Watch list  18-27;  No track  the rest
  Early season (owner Nov 11): until CUTOVER every game that would qualify is held on the Watch list
              (season stats are too thin); from CUTOVER the normal tiers apply.
Gates (fail = No track, on every tier)
  power   underdog in the AP Top 25 or ESPN BPI top 50 (the note names it)
  injury  favorite key player Out or Doubtful on ESPN's injury list. College has no league-wide injury report,
          so no report = a yellow warning only (health assumed full); the morning analyst readout covers news.
Live: Qualified + Watch list followed live; phone pushes for Qualified only.
"""
from __future__ import annotations

from .metrics import LABELS, METRICS

SPREAD_MIN, SPREAD_MAX = 10.0, 20.0
ELITE, STRONG, WEAK_N = 20, 50, 50
N_D1 = 365
QUALIFY_SCORE, WATCH_SCORE = 28, 18
CUTOVER = "12-15"            # month-day the normal tiers start each season (set from the backtest, Nov 11)
TIER_LABEL = {"qualified": "Qualified", "watch": "Watch list", "out": "No track"}
LIVE_TIERS = ("qualified", "watch")
ALERT_TIERS = ("qualified",)
OUT = {"Out", "Doubtful"}
LONG_TERM = {"Out For Season", "Out for Season", "Suspension", "Season-Ending", "Redshirt"}
POWER_SOURCES = (("AP Top 25", "ap", 25), ("ESPN BPI", "bpi", 50))


def weak_from(n: int = N_D1) -> int:
    return n - WEAK_N + 1


def season_floor(n: int = N_D1) -> int:
    return n // 2


def in_band(spread) -> bool:
    return spread is not None and SPREAD_MIN <= abs(float(spread)) <= SPREAD_MAX


def fav_points(rank) -> int:
    return 0 if rank is None else (2 if rank <= ELITE else (1 if rank <= STRONG else 0))


def dog_points(rank, n: int = N_D1) -> int:
    return 0 if rank is None else (2 if rank >= weak_from(n) else (1 if rank > STRONG else 0))


def band(score) -> str:
    return "green" if score >= QUALIFY_SCORE else ("yellow" if score >= WATCH_SCORE else "red")


def fav_color(rank) -> str:
    return "none" if rank is None else ("green" if rank <= STRONG else ("yellow" if rank <= 150 else "red"))


def dog_color(rank, n: int = N_D1) -> str:
    """Flipped: a strong underdog is a threat."""
    if rank is None:
        return "none"
    if rank <= ELITE:
        return "deep-red"
    return "red" if rank <= STRONG else ("yellow" if rank <= 150 else "green")


def early_season(day: str) -> bool:
    """True before the cutover (Nov 1 - Dec 14). `day` = YYYY-MM-DD (ET)."""
    md = day[5:10]
    return "07-01" <= md < CUTOVER


def _r(t: dict, w: str, m: str):
    return ((t.get(w) or {}).get("ranks") or {}).get(m)


def analysis_score(game: dict, fav: dict, dog: dict, n: int = N_D1) -> dict:
    fm = sum(fav_points(_r(fav, w, m)) for m in METRICS for w in ("season", "last3"))
    dm = sum(dog_points(_r(dog, w, m), n) for m in METRICS for w in ("season", "last3"))
    net = fav_points(_r(fav, "season", "net"))
    form = {"3-0": 2, "2-1": 1}.get(fav.get("l3_record"), 0)
    fp, dp = (fav.get("power") or {}).get("rank"), (dog.get("power") or {}).get("rank")
    power = 1 if (fp is not None and dp is not None and fp <= STRONG and dp >= weak_from(n)) else 0
    home = 1 if (game.get("favorite") == "home" and not game.get("neutral")) else 0
    health = 0 if fav.get("key_out") else 1
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
    return {"score": total, "band": band(total), "parts": parts, "max": 40}


def power_gate(dog_power: dict | None) -> list[str]:
    p = dog_power or {}
    return [f"{label} #{p[k]}" for label, k, cut in POWER_SOURCES if p.get(k) is not None and p[k] <= cut]


def score_game(game: dict, fav: dict, dog: dict, day: str, n: int = N_D1) -> dict:
    """Tier, routes, gates, Analysis Score and L1 pills for one game (favorite's point of view)."""
    fa, da = fav.get("abbr", "Favorite"), dog.get("abbr", "Underdog")
    a = analysis_score(game, fav, dog, n)
    total = a["score"]
    wf, floor = weak_from(n), season_floor(n)
    f3 = [m for m in METRICS if (_r(fav, "last3", m) or 999) <= STRONG]
    d3 = [m for m in METRICS if (_r(dog, "last3", m) or 0) >= wf]
    f_miss = [m for m in METRICS if (_r(fav, "season", m) or 999) > floor]
    d_top = [m for m in METRICS if (_r(dog, "season", m) or 999) <= STRONG]
    checks_b = [
        (len(f3) >= 3, f"{fa} last-3 top {STRONG} in {len(f3)} of 4"),
        (len(d3) >= 3, f"{da} last-3 bottom {WEAK_N} ({wf}+) in {len(d3)} of 4"),
        (not f_miss, f"{fa} season top half (1-{floor}) in all four" if not f_miss
         else f"{fa} season outside 1-{floor}: " + ", ".join(f"{LABELS[m]} #{_r(fav, 'season', m) or '–'}" for m in f_miss)),
        (not d_top, f"{da} season top {STRONG} in none" if not d_top
         else f"{da} season top {STRONG}: " + ", ".join(f"{LABELS[m]} #{_r(dog, 'season', m)}" for m in d_top)),
    ]
    mismatch = all(ok for ok, _ in checks_b)
    by_score = total >= QUALIFY_SCORE
    routes = ([f"Score {total}"] if by_score else []) + (["Last-3 mismatch"] if mismatch else [])

    injury_fails = [f"{fa} key player out: " + ", ".join(fav["key_out"])] if fav.get("key_out") else []
    pw = power_gate(dog.get("power"))
    power_fail = f"{da} ranked: " + ", ".join(pw) if pw else None
    early = early_season(day)

    if injury_fails or power_fail:
        tier = "out"
    elif routes:
        tier = "watch" if early else "qualified"
    elif total >= WATCH_SCORE:
        tier = "watch"
    else:
        tier = "out"

    dp = dog.get("power") or {}
    power_info = ", ".join(f"{label} #{dp[k]}" for label, k in (("consensus", "rank"), ("AP", "ap"), ("BPI", "bpi"), ("our net", "own_net"))
                           if dp.get(k) is not None)
    lt = fav.get("key_long_term") or []
    inj_note = ("Clear" if fav.get("injuries_known") else "No injury list from ESPN (warning only; health assumed full)") + \
        (f" (out long term, not counted: {', '.join(lt)})" if lt else "")
    rules = [
        {"rule": "A", "name": f"Analysis Score {QUALIFY_SCORE}+", "pass": by_score, "detail": f"Score {total} of 40"},
        {"rule": "B", "name": "Last-3 mismatch (3 of 4) with a season floor", "pass": mismatch,
         "detail": " · ".join(("✓ " if ok else "✗ ") + txt for ok, txt in checks_b)},
        {"rule": "Injury", "name": "No key player out or doubtful for the favorite", "pass": not injury_fails,
         "detail": "; ".join(injury_fails) or inj_note},
        {"rule": "Power", "name": "Underdog not in the AP Top 25 or ESPN BPI top 50", "pass": not power_fail,
         "detail": power_fail or (f"{da}: {power_info}" if power_info else "No rankings available (gate passes)")},
    ]
    if early:
        rules.append({"rule": "Early", "name": f"qualifiers held on the Watch list until {_cut_label()}",
                      "pass": not routes, "detail": ("Would qualify (" + " + ".join(routes) + "); held on the Watch list") if routes
                      else "Not a qualifier yet"})

    warnings = []
    if not fav.get("injuries_known"):
        warnings.append(f"No injury list for {fa}: check news before betting")
    for p in fav.get("key_questionable") or []:
        warnings.append(f"{fa} key player questionable: {p}")
    if dog.get("key_out"):
        warnings.append(f"Info: {da} without {', '.join(dog['key_out'])}")
    if game.get("neutral"):
        warnings.append("Neutral-site game: no home-court point")
    if (fav.get("games") or 0) < 5 or (dog.get("games") or 0) < 5:
        warnings.append("Fewer than 5 D-I games for a team: ranks are noisy")

    if tier == "qualified":
        fails = []
    elif injury_fails or power_fail:
        fails = ([power_fail] if power_fail else []) + injury_fails + (
            [f"Would be {'qualified' if routes else 'on the watch list'} otherwise (score {total})"] if routes or total >= WATCH_SCORE else [])
    elif tier == "watch" and routes:
        fails = [f"Early season: would qualify ({' + '.join(routes)}); held on the Watch list until {_cut_label()}"]
    elif tier == "watch":
        fails = [f"Watch list: score {total} (qualifies at {QUALIFY_SCORE})"]
    else:
        fails = [f"Score {total} (watch list from {WATCH_SCORE}, qualifies at {QUALIFY_SCORE})"]

    pills = []
    for team, role, w in ((fav, "fav", "season"), (fav, "fav", "last3"), (dog, "dog", "season"), (dog, "dog", "last3")):
        cells = []
        for fm_, dm_ in (("efg", "opp_efg"), ("off", "def"), ("opp_efg", "efg"), ("def", "off")):
            m = fm_ if role == "fav" else dm_
            rk = _r(team, w, m)
            cells.append({"metric": m, "rank": rk, "color": fav_color(rk) if role == "fav" else dog_color(rk, n)})
        pills.append({"team": team.get("abbr"), "role": role, "window": w, "cells": cells})

    return {"scored": True, "fav": fa, "dog": da, "qualified": tier == "qualified", "tier": tier,
            "tier_label": TIER_LABEL[tier], "tracked": tier in LIVE_TIERS, "alerts": tier in ALERT_TIERS,
            "routes": routes, "route": " + ".join(routes), "qualifiers": [], "would_qualify": bool(routes) and not (injury_fails or power_fail),
            "early": early, "rules": rules, "fails": fails, "warnings": warnings,
            "score": total, "max": 40, "band": a["band"], "parts": a["parts"], "pills": pills}


def _cut_label() -> str:
    m, d = CUTOVER.split("-")
    return ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m)] + f" {int(d)}"


def order_games(games: list[dict]) -> list[dict]:
    order = {"qualified": 0, "watch": 1, "out": 2}
    return sorted(games, key=lambda g: (order.get((g.get("scoring") or {}).get("tier"), 2),
                                        -((g.get("scoring") or {}).get("score") or -1), g.get("tip_utc") or ""))
