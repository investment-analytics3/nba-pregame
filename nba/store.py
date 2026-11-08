"""Saved game data: two CSV files in data/, committed to the repo."""
from __future__ import annotations

import csv
import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TEAM_GAMES = os.path.join(DATA, "team_games.csv")
PLAYER_GAMES = os.path.join(DATA, "player_games.csv")

TEAM_COLS = ["game_id", "game_date", "site", "team_id", "team", "opp_id", "opp", "pts", "opp_pts",
             "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov", "ast"]
PLAYER_COLS = ["game_id", "game_date", "team_id", "team", "player_id", "player",
               "starter", "dnp", "minutes", "pts"]
INT_COLS = {"pts", "opp_pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
            "tov", "ast", "starter", "dnp", "minutes"}


def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in INT_COLS & set(r):
            r[k] = int(r[k])
    return rows


def _write(path: str, cols: list[str], rows: list[dict], key: tuple[str, ...]):
    os.makedirs(DATA, exist_ok=True)
    rows = sorted(rows, key=lambda r: tuple(str(r[k]) for k in ("game_date",) + key))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_team_games() -> list[dict]:
    return _read(TEAM_GAMES)


def load_player_games() -> list[dict]:
    return _read(PLAYER_GAMES)


def add_games(team_rows: list[dict], player_rows: list[dict]):
    """Append new games, replacing any existing rows for the same game."""
    new_ids = {r["game_id"] for r in team_rows}
    teams = [r for r in load_team_games() if r["game_id"] not in new_ids] + team_rows
    players = [r for r in load_player_games() if r["game_id"] not in new_ids] + player_rows
    _write(TEAM_GAMES, TEAM_COLS, teams, ("game_id", "team_id"))
    _write(PLAYER_GAMES, PLAYER_COLS, players, ("game_id", "team_id", "player_id"))


def saved_game_ids() -> set[str]:
    return {r["game_id"] for r in load_team_games()}


def write_json(name: str, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")


def read_json(name: str, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)
