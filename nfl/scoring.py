"""NFL qualification rules, the 40-point hero score, the official stat edge and flags.

Spec: "NFL Stat Edge v2" (Claude Doc). Nothing here changes the official edge or its
thresholds; the adjusted edge is shown in shadow only.

Tiers (owner Nov 10, mirroring the NBA board):
  Qualified   route A: hero score 28+, OR route B (last-3 mismatch): over the last 3 the favorite is
              top 10 in >= 3 of the 4 metrics and the underdog is bottom 10 (ranks 23-32) in >= 3 of 4;
              season floor: favorite top 15 in all four, underdog top 10 in none
  Watch list  hero score 18-27
  No track    everything else
  Gates on every tier (fail = No track):
    injury gate  favorite key starter Out/Doubtful/IR (or no injury info)
    power gate   underdog top 10 in ANY power ranking (consensus, ESPN FPI, NFL.com); note names it
  Live: Qualified + Watch list are tracked (Live page, checkpoint log); phone pushes for
  Qualified only (owner Nov 10). The earlier six checks are kept as `context` only.

Hero score (max 40), bands red 0-17 / yellow 18-27 / green 28-40:
  favorite strength 16   4 metrics x (season, last 3): top 5 = 2, ranks 6-10 = 1
  underdog weakness 16   4 metrics x (season, last 3): outside top 10 = 1, bottom 10 (23-32) = 2
  health 4               starting QB active 2, no other key starter out 1, no interior OL out 1 (0 if no injury info)
  home 1                 favorite at home (none at neutral sites)
  rest 1                 favorite off a bye, or underdog on a short week while the favorite isn't
  recent form 2          favorite's last 2: 2-0 = 2, 1-1 = 1

Official edge: gap = favorite net EPA - underdog net EPA; must clear +0.35 for every
favorite, whatever the spread (owner decision Nov 9: no large-favorite bar, no max spread).
QB reset caps the call at Lean.
"""
from __future__ import annotations

from .metrics import LABELS, METRICS, wl
from .power import gate as power_gate

EDGE_REQUIRED = 0.35       # one bar for every spread
BYE_REST = 13             # days of rest that means the favorite is coming off a bye
SHORT_WEEK = 5            # days of rest at or under this = short week
UNDER_RANK = 21           # Under gate: both offenses ranked 21st or worse (season off EPA)
UNDER_MOVE = 3.0          # ...and the total moved by less than 3 points
INTERIOR_OL = {"G", "C", "OG"}
OUT = {"Out", "Doubtful", "Injured Reserve", "Suspension", "Physically Unable to Perform"}
KEY_NUMBERS = (3, 7, 10)
QUALIFY_SCORE = 28
WATCH_SCORE = 18
TIER_LABEL = {"qualified": "Qualified", "watch": "Watch list", "out": "No track"}
LIVE_TIERS = ("qualified", "watch")       # followed live (Live page, checkpoint log)
ALERT_TIERS = ("qualified",)              # phone pushes (owner Nov 10: not the watch list)
SEASON_FLOOR = 15                          # route B: favorite season top 15 in all four
DOG_BOTTOM = 23                            # bottom 10 of 32


def band(score: int) -> str:
    return "green" if score >= 28 else ("yellow" if score >= 18 else "red")


def fav_points(rank) -> int:
    return 0 if rank is None else (2 if rank <= 5 else (1 if rank <= 10 else 0))


def dog_points(rank) -> int:
    return 0 if rank is None else (2 if rank >= 23 else (1 if rank > 10 else 0))


def context_checks(fav: dict, dog: dict) -> tuple[list, list]:
    """The earlier NFL six (minus the injury checks, now the gate): kept for context only."""
    rules = []
    fs, fl3 = fav["season"]["ranks"], fav["last3"]["ranks"]
    r1 = [m for m in METRICS if (fs.get(m) or 99) <= 10]
    rules.append({"n": 1, "ok": bool(r1), "text": "Favorite top 10 (season) in a core metric",
                  "detail": ", ".join(LABELS[m] for m in r1) or "none"})
    r2 = [m for m in r1 if (fl3.get(m) or 99) <= 10]
    rules.append({"n": 2, "ok": bool(r2), "text": "Also top 10 over its last 3 in that same metric",
                  "detail": ", ".join(LABELS[m] for m in r2) or "none"})
    dl3 = dog["last3"]["ranks"]
    r3 = [m for m in METRICS if (dl3.get(m) or 99) <= 10]
    rules.append({"n": 3, "ok": not r3, "text": "Underdog not top 10 in any metric over its last 3",
                  "detail": ", ".join(LABELS[m] for m in r3) or "none"})
    last3 = dog.get("last3_results") or []
    r4 = not (len(last3) == 3 and all(x == "W" for x in last3))
    rules.append({"n": 4, "ok": r4, "text": "Underdog not 3-0 in its last 3", "detail": wl(last3) if last3 else "?"})
    return rules, r2


def qualification(fav: dict, dog: dict, score: int | None = None) -> dict:
    """Tier (qualified / watch / out), routes, gates and fail notes. `score` = hero score."""
    if score is None:
        score = 0
    fa, da = fav.get("abbr", "Favorite"), dog.get("abbr", "Underdog")
    fs, fl3 = fav["season"]["ranks"], fav["last3"]["ranks"]
    ds, dl3 = dog["season"]["ranks"], dog["last3"]["ranks"]
    # route B: last-3 mismatch with a season floor
    f3 = [m for m in METRICS if (fl3.get(m) or 99) <= 10]
    d3 = [m for m in METRICS if (dl3.get(m) or 0) >= DOG_BOTTOM]
    f_miss = [m for m in METRICS if (fs.get(m) or 99) > SEASON_FLOOR]
    d_top = [m for m in METRICS if (ds.get(m) or 99) <= 10]
    checks_b = [
        (len(f3) >= 3, f"{fa} last-3 top 10 in {len(f3)} of 4"),
        (len(d3) >= 3, f"{da} last-3 bottom 10 (23-32) in {len(d3)} of 4"),
        (not f_miss, f"{fa} season top 15 in all four" if not f_miss
         else f"{fa} season outside top 15: " + ", ".join(f"{LABELS[m]} #{fs.get(m) or '–'}" for m in f_miss)),
        (not d_top, f"{da} season top 10 in none" if not d_top
         else f"{da} season top 10: " + ", ".join(f"{LABELS[m]} #{ds.get(m)}" for m in d_top)),
    ]
    mismatch = all(ok for ok, _ in checks_b)
    by_score = score >= QUALIFY_SCORE
    routes = ([f"Score {score}"] if by_score else []) + (["Last-3 mismatch"] if mismatch else [])
    # gates
    injury_fails = []
    if not fav.get("injuries_known"):
        injury_fails.append(f"No injury report for {fa} yet")
    if fav.get("key_out"):
        injury_fails.append(f"{fa} key starter out: " + ", ".join(fav["key_out"]))
    pw = power_gate(dog.get("power"))
    power_fail = f"{da} top 10 in power ranking: " + ", ".join(pw) if pw else None
    dp = dog.get("power") or {}
    power_info = ", ".join(f"{label} #{dp[k]}" for label, k in (("consensus", "consensus"), ("ESPN FPI", "fpi"),
                                                                ("NFL.com", "nfl_com")) if dp.get(k) is not None)
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
        {"rule": "Injury", "name": "No key starter out for the favorite", "pass": not injury_fails,
         "detail": "; ".join(injury_fails) or "Clear"},
        {"rule": "Power", "name": "Underdog not top 10 in any power ranking", "pass": not power_fail,
         "detail": power_fail or (f"{da}: {power_info}" if power_info else "No power rankings available (gate passes)")},
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
    context, qualifiers = context_checks(fav, dog)
    return {"qualified": tier == "qualified", "tier": tier, "tier_label": TIER_LABEL[tier],
            "tracked": tier in LIVE_TIERS, "alerts": tier in ALERT_TIERS,
            "routes": routes, "route": " + ".join(routes), "rules": rules, "context": context,
            "qualifiers": qualifiers, "fails": fails, "failed": fails, "power_fail": power_fail}


def hero_score(game: dict, fav: dict, dog: dict) -> dict:
    parts = {}
    parts["fav_strength"] = sum(fav_points(fav[w]["ranks"].get(m)) for w in ("season", "last3") for m in METRICS)
    parts["dog_weakness"] = sum(dog_points(dog[w]["ranks"].get(m)) for w in ("season", "last3") for m in METRICS)
    h = 0                                   # no injury info = no health points
    if not fav.get("injuries_known"):
        h = -99
    h += 2 if fav.get("qb_active", True) else 0
    h += 1 if not [p for p in (fav.get("key_out") or []) if p not in (fav.get("qb_out") or [])] else 0
    h += 1 if not fav.get("interior_ol_out") else 0
    parts["health"] = max(h, 0)
    parts["home"] = 1 if (game.get("fav_home") and not game.get("neutral")) else 0
    fr, dr = fav.get("rest"), dog.get("rest")
    dog_short = dr is not None and dr <= SHORT_WEEK and fr is not None and fr - dr >= 2   # a rest edge, not a shared short week
    parts["rest"] = 1 if ((fr is not None and fr >= BYE_REST) or dog_short) else 0
    l2 = fav.get("last2_results") or []
    w2 = l2.count("W")
    parts["form"] = 2 if (len(l2) == 2 and w2 == 2) else (1 if w2 == 1 else 0)
    score = sum(parts.values())
    return {"score": score, "band": band(score), "parts": parts, "max": 40}


def stat_edge(fav: dict, dog: dict, spread: float | None) -> dict:
    if fav.get("net") is None or dog.get("net") is None:
        return {"gap": None}
    gap = fav["net"] - dog["net"]
    need = EDGE_REQUIRED
    out = {"gap": round(gap, 3), "required": need, "clears": gap >= need,
           "short_by": round(need - gap, 3) if gap < need else 0}
    fa, da = fav.get("adjusted") or {}, dog.get("adjusted") or {}
    if fa.get("net") is not None and da.get("net") is not None:
        out["adjusted_gap"] = round(fa["net"] - da["net"], 3)
    if fa.get("recent_net") is not None and da.get("recent_net") is not None:
        out["adjusted_recent_gap"] = round(fa["recent_net"] - da["recent_net"], 3)
    return out


def crosses_key(open_line: float | None, cur: float | None) -> list[int]:
    if open_line is None or cur is None:
        return []
    lo, hi = sorted((open_line, cur))      # signed favorite lines (negative = favored)
    if lo == hi:
        return []
    return [k for k in KEY_NUMBERS if lo <= -k <= hi]   # moved onto, off or through 3, 7 or 10


def flags(game: dict, fav: dict, dog: dict, edge: dict, power_fail: str | None = None) -> list[dict]:
    f = [{"level": "red", "text": power_fail + " (no track)"}] if power_fail else []
    for side, t in (("Favorite", fav), ("Underdog", dog)):
        if t.get("qb_reset"):
            f.append({"level": "red", "text": f"{side} QB change ({t['qb_reset']}): call capped at Lean"})
    if fav.get("interior_ol_out"):
        f.append({"level": "yellow", "text": "Favorite interior OL out: " + ", ".join(fav["interior_ol_out"])})
    mv = game.get("movement")
    if mv is not None and mv >= 4:
        f.append({"level": "yellow", "text": f"Line moved {mv:+g} toward the favorite (4+): downgrade"})
    keys = crosses_key(game.get("open_spread"), game.get("spread"))
    if keys:
        f.append({"level": "yellow", "text": "Line crossed key number " + ", ".join(map(str, keys))})
    for side, t in (("Favorite", fav), ("Underdog", dog)):
        if t.get("key_questionable"):
            f.append({"level": "yellow", "text": f"{side} questionable: " + ", ".join(t["key_questionable"][:4])})
    return f


def under_gate(game: dict, fav: dict, dog: dict) -> dict:
    fr, dr = fav["season"]["ranks"].get("off_epa"), dog["season"]["ranks"].get("off_epa")
    tm = game.get("total_move")
    ok_rank = fr is not None and dr is not None and fr >= UNDER_RANK and dr >= UNDER_RANK
    ok_move = tm is not None and abs(tm) < UNDER_MOVE
    return {"passes": bool(ok_rank and ok_move), "ranks": [fr, dr], "total_move": tm,
            "note": "Under numerical gate passes (no Over rule)" if ok_rank and ok_move else ""}


def call(qual: dict, edge: dict, fav: dict, dog: dict) -> str:
    """Pregame call. Context only; the bet decision is still the owner's."""
    if edge.get("gap") is None:
        return "No data"
    if qual["tier"] == "out":
        return "No track"
    if qual["tier"] == "watch":
        return "Watch list"
    if fav.get("qb_reset") or dog.get("qb_reset"):
        return "Lean (QB cap)"
    return "Edge clears" if edge.get("clears") else "Track, edge short"


def score_game(game: dict, fav: dict, dog: dict) -> dict:
    hs = hero_score(game, fav, dog)
    q = qualification(fav, dog, hs["score"])
    e = stat_edge(fav, dog, game.get("spread"))
    return {k: q[k] for k in ("qualified", "tier", "tier_label", "tracked", "alerts", "routes", "route", "rules",
                              "context", "qualifiers", "fails", "failed")} | {
            "hero": hs, "edge": e, "flags": flags(game, fav, dog, e, q.get("power_fail")), "under": under_gate(game, fav, dog),
            "call": call(q, e, fav, dog)}
