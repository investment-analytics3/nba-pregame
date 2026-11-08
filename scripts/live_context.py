#!/usr/bin/env python3
"""Write docs/live/context.json: what the Live page and the live tracker need.

  - today's above-the-line games (from the day's slate)
  - closers per team (data/closers.json; your edits win over the draft)
  - season points-per-minute for each closer (for "is he playing well?")

Run by run_board.py whenever a board is published, and at the start of every
live tracker run.
    python scripts/live_context.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import store  # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "live", "context.json")


def closers_by_team() -> dict:
    """{"BOS": {"closers": [...], "elite": [...], "source": "draft"|"yours"}}.

    Accepts the older list form {"BOS": ["A", "B"]} too.
    """
    raw = store.read_json("closers.json", {}) or {}
    out = {}
    for abbr, v in raw.items():
        if abbr.startswith("_"):
            continue
        if isinstance(v, list):
            v = {"closers": v, "elite": [], "source": "yours"}
        out[abbr] = {"closers": list(v.get("closers", [])), "elite": list(v.get("elite", [])),
                     "source": v.get("source", "yours")}
    return out


def season_ppm(players: list[dict], before: str) -> dict:
    """{team_abbr: {player_name: {"ppm": pts per minute, "ppg": .., "gp": ..}}}"""
    acc: dict = {}
    for p in players:
        if p["game_date"] >= before or str(p.get("dnp")) == "1":
            continue
        mins = float(p["minutes"] or 0)
        if mins <= 0:
            continue
        a = acc.setdefault(p["team"], {}).setdefault(p["player"], [0.0, 0.0, 0])
        a[0] += float(p["pts"] or 0)
        a[1] += mins
        a[2] += 1
    return {t: {n: {"ppm": round(v[0] / v[1], 3), "ppg": round(v[0] / v[2], 1), "gp": v[2]}
                for n, v in d.items() if v[1] > 0} for t, d in acc.items()}


def build(day: date) -> dict:
    slate = store.read_json(f"slates/{day.isoformat()}.json", {}) or {}
    closers = closers_by_team()
    ppm = season_ppm(store.load_player_games(), day.isoformat())
    games = []
    for g in slate.get("games", []):
        sc = g.get("scoring") or {}
        if not sc.get("qualified"):
            continue
        fav_team = g["away"] if g["away"].get("abbr") == sc.get("fav") else g["home"]
        fav_def = {w: {m: {"v": (fav_team.get(w) or {}).get(m), "r": ((fav_team.get(w) or {}).get("ranks") or {}).get(m)}
                       for m in ("opp_efg", "def")} for w in ("season", "last3")}
        games.append({
            "id": g["id"], "fav_defense": fav_def, "fav_b2b": fav_team.get("b2b"), "tip_utc": g.get("tip_utc"), "tip_pt": g.get("tip_pt"),
            "fav": sc.get("fav"), "dog": sc.get("dog"), "qualifiers": sc.get("qualifiers", []),
            "score": sc.get("score"), "band": sc.get("band"),
            "spread": (g.get("spread") or {}).get("details") if isinstance(g.get("spread"), dict) else g.get("spread"),
            "away": {k: g["away"].get(k) for k in ("id", "abbr", "name", "display", "record")},
            "home": {k: g["home"].get(k) for k in ("id", "abbr", "name", "display", "record")},
        })
    teams = {t for g in games for t in (g["away"]["abbr"], g["home"]["abbr"])}
    ctx_closers = {}
    for t in teams:
        c = closers.get(t)
        if c:
            ctx_closers[t] = dict(c, season={n: ppm.get(t, {}).get(n) for n in c["closers"] if ppm.get(t, {}).get(n)})
    metrics = store.read_json("metrics.json", {}) or {}
    league = {m: sorted(t["season"][m] for t in (metrics.get("teams") or {}).values()
                        if (t.get("season") or {}).get(m) is not None) for m in ("opp_efg", "def")}
    bt = store.read_json("live/backtest.json", {}) or {}
    return {"date": day.isoformat(), "built_at": datetime.now(ET).isoformat(timespec="seconds"),
            "games": games, "closers": ctx_closers, "league": league, "fav10": bt.get("fav10")}


def main() -> int:
    day = date.fromisoformat(sys.argv[sys.argv.index("--date") + 1]) if "--date" in sys.argv else datetime.now(ET).date()
    ctx = build(day)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(ctx, f, indent=1)
    print(f"Live context for {day}: {len(ctx['games'])} tracked game(s): "
          + ", ".join(f"{g['away']['abbr']} @ {g['home']['abbr']}" for g in ctx["games"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
