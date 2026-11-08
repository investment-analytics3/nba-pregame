"""Running record: grade every game that appeared on a sent board.

Each game is graded from the last board sent before tip (the Final if there
was one, otherwise the midday board): who the favorite was, the spread, the
hero score and whether it was above the line. Results come from the saved box
scores. Rows are kept forever in data/results.csv (also published as
docs/results.csv for spreadsheets).
"""
from __future__ import annotations

import csv
import os

from . import store

COLS = ["date", "game_id", "favorite", "opponent", "fav_site", "spread", "fav_pts", "opp_pts",
        "winner", "fav_won", "covered", "above_line", "hero_score", "band", "qualifiers", "board_page"]
CSV_PATH = os.path.join(store.DATA, "results.csv")


def load() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict], *paths: str):
    rows = sorted(rows, key=lambda r: (r["date"], r["game_id"]), reverse=True)
    for p in (CSV_PATH,) + paths:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return rows


def _fmt_spread(x) -> str:
    if x is None or x == "":
        return ""
    x = float(x)
    return "PK" if x == 0 else f"-{x:g}"


def grade(log: dict, team_rows: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    done = {(r["date"], r["game_id"]) for r in existing}
    by_game = {(r["game_id"], r["team"]): r for r in team_rows}
    added = []
    for day, entry in sorted(log.items()):
        sent = entry.get("final") or entry.get("board")
        if not sent:
            continue
        for gid, s in (sent.get("snapshot") or {}).items():
            if (day, gid) in done or not s.get("fav") or not s.get("dog"):
                continue
            fr = by_game.get((gid, s["fav"]))
            if not fr:
                continue  # not final yet (or postponed); try again next run
            fav_pts, opp_pts = fr["pts"], fr["opp_pts"]
            margin = fav_pts - opp_pts
            spread = s.get("spread_num")
            if spread is None:
                covered = ""
            elif margin > float(spread):
                covered = "Yes"
            elif margin == float(spread):
                covered = "Push"
            else:
                covered = "No"
            site = "neutral" if s.get("neutral") else (s.get("fav_side") or "")
            page = sent.get("page") or ""
            added.append({
                "date": day, "game_id": gid, "favorite": s["fav"], "opponent": s["dog"], "fav_site": site,
                "spread": _fmt_spread(spread), "fav_pts": fav_pts, "opp_pts": opp_pts,
                "winner": s["fav"] if margin > 0 else s["dog"], "fav_won": "Yes" if margin > 0 else "No",
                "covered": covered, "above_line": "Yes" if s.get("qualified") else "No",
                "hero_score": s.get("score") if s.get("score") is not None else "",
                "band": s.get("band") or "", "qualifiers": "; ".join(s.get("qualifiers") or []),
                "board_page": f"{page}#g-{gid}" if page else "",
            })
    return existing + added, len(added)


def summary(rows: list[dict]) -> dict:
    def block(rs):
        n = len(rs)
        w = sum(r["fav_won"] == "Yes" for r in rs)
        cov = sum(r["covered"] == "Yes" for r in rs)
        push = sum(r["covered"] == "Push" for r in rs)
        ats_n = sum(r["covered"] in ("Yes", "No") for r in rs)
        return {"games": n, "won": w, "lost": n - w, "win_pct": round(100 * w / n) if n else None,
                "covered": cov, "not_covered": ats_n - cov, "push": push,
                "cover_pct": round(100 * cov / ats_n) if ats_n else None}
    return {"above": block([r for r in rows if r["above_line"] == "Yes"]),
            "below": block([r for r in rows if r["above_line"] == "No"]),
            "all": block(rows)}
