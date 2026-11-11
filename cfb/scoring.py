"""College football scoring: the 40-point hero score, tiers and gates (owner decisions Nov 10, 2026).

Which games: FBS vs FBS, spread favorite giving 10 to 20 points (both ends included).
Ranks are among FBS teams (138 in 2026): strong = 1-25 (elite = 1-10), weak = 114-138.

Hero score (max 40)
  fav strength  16  4 metrics x (season, last 3): rank 1-10 = 2, 11-25 = 1
  dog weakness  16  4 x 2: rank 26-113 = 1, 114+ = 2
  health         4  regular QB active 2, no other key player out 1, interior OL 1 (no OL data: always 1)
  home           1  favorite at home (none at neutral sites)
  rest           1  favorite off a bye (13+ days), or underdog on a short week (5 days or less) when the favorite isn't
  form           2  favorite won its last 2 (1 if it split them)
Tiers
  Qualified   score 28+ (route A) OR last-3 mismatch (route B): last 3, favorite rank 1-25 in >= 3 of 4 AND underdog
              114+ in >= 3 of 4; season, favorite top half (1-69) in all four AND underdog 1-25 in none
  Watch list  18-27;  No track  the rest
Gates (fail = No track, on every tier)
  injury  favorite key player newly Out or Doubtful. Injured reserve / out for season / suspension never count.
          No injury report = a yellow warning only (owner Nov 10), health assumed full (as in the backtest)
  power   underdog in the AP Top 25, the CFP rankings or ESPN's FPI top 25 (the note names it)
Live: Qualified + Watch list followed live (Live page + checkpoint log, with Kalshi prices);
phone pushes for Qualified only.
"""
from __future__ import annotations

from .metrics import LABELS, METRICS

SPREAD_MIN, SPREAD_MAX = 10.0, 20.0
ELITE, STRONG, WEAK_FROM = 10, 25, 114
SEASON_FLOOR = 69
QUALIFY_SCORE, WATCH_SCORE = 28, 18
BYE_REST, SHORT_WEEK = 13, 5
TIER_LABEL = {"qualified": "Qualified", "watch": "Watch list", "out": "No track"}
LIVE_TIERS = ("qualified", "watch")
ALERT_TIERS = ("qualified",)
OUT = {"Out", "Doubtful"}
LONG_TERM = {"Injured Reserve", "Out For Season", "Out for Season", "Suspension", "Season-Ending", "Physically Unable to Perform"}
POWER_SOURCES = (("AP Top 25", "ap"), ("CFP", "cfp"), ("ESPN FPI", "fpi"))


def in_band(spread) -> bool:
    return spread is not None and SPREAD_MIN <= abs(float(spread)) <= SPREAD_MAX


def fav_points(rank) -> int:
    return 0 if rank is None else (2 if rank <= ELITE else (1 if rank <= STRONG else 0))


def dog_points(rank) -> int:
    return 0 if rank is None else (2 if rank >= WEAK_FROM else (1 if rank > STRONG else 0))


def band(score) -> str:
    return "green" if score >= QUALIFY_SCORE else ("yellow" if score >= WATCH_SCORE else "red")


def hero_score(game: dict, fav: dict, dog: dict) -> dict:
    parts = {"fav_strength": sum(fav_points(fav[w]["ranks"].get(m)) for w in ("season", "last3") for m in METRICS),
             "dog_weakness": sum(dog_points(dog[w]["ranks"].get(m)) for w in ("season", "last3") for m in METRICS)}
    h = (2 if fav.get("qb_active", True) else 0)
    h += 1 if not [p for p in (fav.get("key_out") or []) if p not in (fav.get("qb_out") or [])] else 0
    h += 1                                            # interior OL: no data in college, counted as healthy
    parts["health"] = h
    parts["home"] = 1 if (game.get("fav_home") and not game.get("neutral")) else 0
    fr, dr = fav.get("rest"), dog.get("rest")
    dog_short = dr is not None and dr <= SHORT_WEEK and fr is not None and fr - dr >= 2
    parts["rest"] = 1 if ((fr is not None and fr >= BYE_REST) or dog_short) else 0
    l2 = fav.get("last2_results") or []
    w2 = l2.count("W")
    parts["form"] = 2 if (len(l2) == 2 and w2 == 2) else (1 if w2 == 1 else 0)
    score = sum(parts.values())
    return {"score": score, "band": band(score), "parts": parts, "max": 40}


def power_gate(dog_power: dict | None) -> list[str]:
    p = dog_power or {}
    return [f"{label} #{p[k]}" for label, k in POWER_SOURCES if p.get(k) is not None and p[k] <= STRONG]


def qualification(fav: dict, dog: dict, score: int, n_fbs: int = 138) -> dict:
    fa, da = fav.get("abbr", "Favorite"), dog.get("abbr", "Underdog")
    fs, fl3 = fav["season"]["ranks"], fav["last3"]["ranks"]
    ds, dl3 = dog["season"]["ranks"], dog["last3"]["ranks"]
    f3 = [m for m in METRICS if (fl3.get(m) or 999) <= STRONG]
    d3 = [m for m in METRICS if (dl3.get(m) or 0) >= WEAK_FROM]
    f_miss = [m for m in METRICS if (fs.get(m) or 999) > SEASON_FLOOR]
    d_top = [m for m in METRICS if (ds.get(m) or 999) <= STRONG]
    checks_b = [
        (len(f3) >= 3, f"{fa} last-3 top 25 in {len(f3)} of 4"),
        (len(d3) >= 3, f"{da} last-3 bottom 25 (114+) in {len(d3)} of 4"),
        (not f_miss, f"{fa} season top half (1-{SEASON_FLOOR}) in all four" if not f_miss
         else f"{fa} season outside 1-{SEASON_FLOOR}: " + ", ".join(f"{LABELS[m]} #{fs.get(m) or '–'}" for m in f_miss)),
        (not d_top, f"{da} season top 25 in none" if not d_top
         else f"{da} season top 25: " + ", ".join(f"{LABELS[m]} #{ds.get(m)}" for m in d_top)),
    ]
    mismatch = all(ok for ok, _ in checks_b)
    by_score = score >= QUALIFY_SCORE
    routes = ([f"Score {score}"] if by_score else []) + (["Last-3 mismatch"] if mismatch else [])
    injury_fails = [f"{fa} key player out: " + ", ".join(fav["key_out"])] if fav.get("key_out") else []
    lt = fav.get("key_long_term") or []
    inj_note = ("Clear" if fav.get("injuries_known") else "No injury report (warning only; health assumed full)") + \
        (f" (out long term, not counted: {', '.join(lt)})" if lt else "")
    pw = power_gate(dog.get("power"))
    power_fail = f"{da} ranked: " + ", ".join(pw) if pw else None
    dp = dog.get("power") or {}
    power_info = ", ".join(f"{label} #{dp[k]}" for label, k in (("consensus", "consensus"),) + POWER_SOURCES if dp.get(k) is not None)
    if injury_fails or power_fail:
        tier = "out"
    elif routes:
        tier = "qualified"
    elif score >= WATCH_SCORE:
        tier = "watch"
    else:
        tier = "out"
    rules = [
        {"rule": "A", "name": f"Hero score {QUALIFY_SCORE}+", "pass": by_score, "detail": f"Score {score} of 40"},
        {"rule": "B", "name": "Last-3 mismatch (3 of 4) with a season floor", "pass": mismatch,
         "detail": " · ".join(("✓ " if ok else "✗ ") + txt for ok, txt in checks_b)},
        {"rule": "Injury", "name": "No key player newly out or doubtful for the favorite", "pass": not injury_fails,
         "detail": "; ".join(injury_fails) or inj_note},
        {"rule": "Power", "name": "Underdog not in the AP Top 25, the CFP rankings or FPI top 25", "pass": not power_fail,
         "detail": power_fail or (f"{da}: {power_info}" if power_info else "No rankings available (gate passes)")},
    ]
    if tier == "qualified":
        fails = []
    elif injury_fails or power_fail:
        fails = ([power_fail] if power_fail else []) + injury_fails + (
            [f"Would be {'qualified' if routes else 'on the watch list'} otherwise (score {score})"]
            if routes or score >= WATCH_SCORE else [])
    elif tier == "watch":
        fails = [f"Watch list: score {score} (qualifies at {QUALIFY_SCORE})"]
    else:
        fails = [f"Score {score} (watch list from {WATCH_SCORE}, qualifies at {QUALIFY_SCORE})"]
    return {"qualified": tier == "qualified", "tier": tier, "tier_label": TIER_LABEL[tier],
            "tracked": tier in LIVE_TIERS, "alerts": tier in ALERT_TIERS, "routes": routes, "route": " + ".join(routes),
            "rules": rules, "fails": fails, "power_fail": power_fail}


def flags(fav: dict, dog: dict, power_fail: str | None) -> list[dict]:
    f = [{"level": "red", "text": power_fail + " (no track)"}] if power_fail else []
    for side, t in (("Favorite", fav), ("Underdog", dog)):
        if t.get("qb_reset"):
            f.append({"level": "red", "text": f"{side} QB change ({t['qb_reset']})" + (": call capped at Lean" if side == "Favorite" else "")})
    if not fav.get("injuries_known"):
        f.append({"level": "yellow", "text": f"No injury report for {fav.get('abbr')} yet: check before betting"})
    for side, t in (("Favorite", fav), ("Underdog", dog)):
        if t.get("key_questionable"):
            f.append({"level": "yellow", "text": f"{side} questionable: " + ", ".join(t["key_questionable"][:4])})
    return f


def score_game(game: dict, fav: dict, dog: dict, n_fbs: int = 138) -> dict:
    hs = hero_score(game, fav, dog)
    q = qualification(fav, dog, hs["score"], n_fbs)
    call = TIER_LABEL[q["tier"]]
    if q["tier"] == "qualified" and fav.get("qb_reset"):
        call = "Lean (QB cap)"
    return {**{k: v for k, v in q.items() if k != "power_fail"}, "hero": hs, "call": call,
            "flags": flags(fav, dog, q["power_fail"])}
