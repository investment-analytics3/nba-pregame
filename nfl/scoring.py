"""NFL qualification rules, the 40-point hero score, the official stat edge and flags.

Spec: "NFL Stat Edge v2" (Claude Doc). Nothing here changes the official edge or its
thresholds; the adjusted edge is shown in shadow only.

Qualification (mirrors the NBA six; all must pass):
  1. favorite top 10 (season) in at least one of the four metrics
  2. favorite also top 10 over its last 3 games in that SAME metric
  3. underdog not top 10 in ANY of the four over its last 3
  4. underdog not 3-0 in its last 3
  5. favorite has no key starter Out or Doubtful
  6. injury information available for the favorite
  7. opponent power gate (owner Nov 10, as on the NBA board): underdog not top 10 in ANY
     power ranking (consensus, ESPN FPI, NFL.com); the note names the ranking(s)

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


def band(score: int) -> str:
    return "green" if score >= 28 else ("yellow" if score >= 18 else "red")


def fav_points(rank) -> int:
    return 0 if rank is None else (2 if rank <= 5 else (1 if rank <= 10 else 0))


def dog_points(rank) -> int:
    return 0 if rank is None else (2 if rank >= 23 else (1 if rank > 10 else 0))


def qualification(fav: dict, dog: dict) -> dict:
    rules, qualifiers = [], []
    fs, fl3 = fav["season"]["ranks"], fav["last3"]["ranks"]
    r1 = [m for m in METRICS if (fs.get(m) or 99) <= 10]
    rules.append({"n": 1, "ok": bool(r1), "text": "Favorite top 10 (season) in a core metric",
                  "detail": ", ".join(LABELS[m] for m in r1) or "none"})
    r2 = [m for m in r1 if (fl3.get(m) or 99) <= 10]
    qualifiers = r2
    rules.append({"n": 2, "ok": bool(r2), "text": "Also top 10 over its last 3 in that same metric",
                  "detail": ", ".join(LABELS[m] for m in r2) or "none"})
    dl3 = dog["last3"]["ranks"]
    r3 = [m for m in METRICS if (dl3.get(m) or 99) <= 10]
    rules.append({"n": 3, "ok": not r3, "text": "Underdog not top 10 in any metric over its last 3",
                  "detail": ", ".join(LABELS[m] for m in r3) or "none"})
    last3 = dog.get("last3_results") or []
    r4 = not (len(last3) == 3 and all(x == "W" for x in last3))
    rules.append({"n": 4, "ok": r4, "text": "Underdog not 3-0 in its last 3", "detail": wl(last3) if last3 else "?"})
    out = fav.get("key_out") or []
    rules.append({"n": 5, "ok": not out, "text": "Favorite has no key starter out",
                  "detail": ", ".join(out) or "none"})
    rules.append({"n": 6, "ok": bool(fav.get("injuries_known")), "text": "Injury info available for the favorite",
                  "detail": "yes" if fav.get("injuries_known") else "missing"})
    pw = power_gate(dog.get("power"))
    has_power = any((dog.get("power") or {}).get(k) is not None for k in ("consensus", "fpi", "nfl_com"))
    rules.append({"n": 7, "ok": not pw, "text": "Underdog not top 10 in any power ranking",
                  "detail": ", ".join(pw) if pw else ("none" if has_power else "no power rankings available"),
                  "fail": f"{dog.get('abbr', 'Underdog')} top 10 in power ranking: " + ", ".join(pw) if pw else None})
    failed = [r for r in rules if not r["ok"]]
    return {"qualified": not failed, "rules": rules, "qualifiers": qualifiers,
            "failed": [r.get("fail") or f"Rule {r['n']}: {r['text']}" for r in failed],
            "power_fail": next((r["fail"] for r in failed if r["n"] == 7), None)}


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
    if not qual["qualified"]:
        return "No track"
    if fav.get("qb_reset") or dog.get("qb_reset"):
        return "Lean (QB cap)"
    return "Edge clears" if edge.get("clears") else "Track, edge short"


def score_game(game: dict, fav: dict, dog: dict) -> dict:
    q = qualification(fav, dog)
    hs = hero_score(game, fav, dog)
    e = stat_edge(fav, dog, game.get("spread"))
    return {"qualified": q["qualified"], "rules": q["rules"], "qualifiers": q["qualifiers"], "failed": q["failed"],
            "hero": hs, "edge": e, "flags": flags(game, fav, dog, e, q.get("power_fail")), "under": under_gate(game, fav, dog),
            "call": call(q, e, fav, dog)}
