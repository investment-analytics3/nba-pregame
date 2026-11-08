"""Running record: grade every game that appeared on a sent board.

Each game is graded from the last board sent before tip (the Final if there
was one, otherwise the midday board): favorite, spread, Analysis Score, whether
it was above the line, and both teams' four metrics (season and last 3) as they
stood on that board. Results come from the saved box scores.

Stored in data/results.json (kept forever). Exported to docs/results.csv with
two rows per game (favorite, then opponent) for spreadsheets.
"""
from __future__ import annotations

import csv
import os

from . import scoring, store

JSON_NAME = "results.json"
METRIC_COLS = [(w, m) for w in ("season", "last3") for m in scoring.METRICS]
LABEL = {"efg": "eFG%", "off": "Off Eff", "opp_efg": "Opp eFG%", "def": "Def Eff"}


def load() -> list[dict]:
    return store.read_json(JSON_NAME, []) or []


def save(games: list[dict]) -> list[dict]:
    games = sorted(games, key=lambda g: (g["date"], g.get("tip_utc") or "", g["game_id"]), reverse=True)
    store.write_json(JSON_NAME, games)
    return games


def _fmt_spread(x) -> str:
    if x is None:
        return ""
    return "PK" if float(x) == 0 else f"-{float(x):g}"


def grade(log: dict, team_rows: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    done = {(g["date"], g["game_id"]) for g in existing}
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
            margin = fr["pts"] - fr["opp_pts"]
            spread = s.get("spread_num")
            if spread is None:
                covered = ""
            else:
                covered = "Yes" if margin > float(spread) else ("Push" if margin == float(spread) else "No")
            page = sent.get("page") or ""
            added.append({
                "date": day, "game_id": gid, "tip_utc": s.get("tip_utc"),
                "favorite": s["fav"], "opponent": s["dog"],
                "fav_site": "neutral" if s.get("neutral") else (s.get("fav_side") or ""),
                "spread": _fmt_spread(spread), "spread_num": spread,
                "fav_pts": fr["pts"], "opp_pts": fr["opp_pts"],
                "winner": s["fav"] if margin > 0 else s["dog"], "fav_won": "Yes" if margin > 0 else "No",
                "covered": covered, "above_line": "Yes" if s.get("qualified") else "No",
                "analysis_score": s.get("score"), "band": s.get("band") or "",
                "qualifiers": s.get("qualifiers") or [],
                "fav_metrics": s.get("fav_metrics") or {}, "opp_metrics": s.get("dog_metrics") or {},
                "board_page": f"{page}#g-{gid}" if page else "",
            })
    return existing + added, len(added)


def metric_color(rank, role: str) -> str:
    return scoring.fav_color(rank) if role == "Favorite" else scoring.dog_color(rank)


def export_csv(games: list[dict], path: str, pages_url: str = ""):
    """Two rows per game: the favorite, then the opponent."""
    cols = ["date", "game_id", "role", "team", "favorite", "opponent", "spread", "fav_pts", "opp_pts",
            "winner", "fav_won", "covered", "above_line", "analysis_score", "analysis_color"]
    for w, m in METRIC_COLS:
        base = f"{'season' if w == 'season' else 'last3'}_{m}"
        cols += [base, base + "_rank", base + "_color"]
    cols += ["board_link"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w_ = csv.DictWriter(f, fieldnames=cols)
        w_.writeheader()
        for g in games:
            for role, team, mk in (("Favorite", g["favorite"], "fav_metrics"), ("Opponent", g["opponent"], "opp_metrics")):
                row = {k: g.get(k, "") for k in cols}
                row.update(role=role, team=team, analysis_color=g.get("band", ""),
                           board_link=(pages_url + g["board_page"]) if g.get("board_page") else "")
                for w, m in METRIC_COLS:
                    base = f"{'season' if w == 'season' else 'last3'}_{m}"
                    cell = ((g.get(mk) or {}).get(w) or {}).get(m) or {}
                    row[base] = cell.get("v", "")
                    row[base + "_rank"] = cell.get("r", "")
                    row[base + "_color"] = metric_color(cell.get("r"), role) if cell.get("r") else ""
                w_.writerow(row)


def summary(games: list[dict]) -> dict:
    def block(gs):
        n = len(gs)
        w = sum(g["fav_won"] == "Yes" for g in gs)
        cov = sum(g["covered"] == "Yes" for g in gs)
        push = sum(g["covered"] == "Push" for g in gs)
        ats = sum(g["covered"] in ("Yes", "No") for g in gs)
        return {"games": n, "won": w, "lost": n - w, "covered": cov, "not_covered": ats - cov, "push": push}
    return {"above": block([g for g in games if g["above_line"] == "Yes"]),
            "below": block([g for g in games if g["above_line"] == "No"]),
            "all": block(games)}
