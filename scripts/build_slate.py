#!/usr/bin/env python3
"""Build the slate file for one day: every game with everything the board needs.

For each game: tip time, teams, spread and favorite, records, back-to-backs,
season and last-3 metrics with ranks, consensus power rank, last-3 game logs
(opponent power rank in parentheses), and injuries with a "key player" flag.

    python scripts/build_slate.py              # today (Eastern date)
    python scripts/build_slate.py 2026-11-09   # a specific date
Writes data/slates/YYYY-MM-DD.json
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import espn, injury_report, metrics, power, store  # noqa: E402
from nba.injury_report import norm  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
OUT_STATUSES = {"Out", "Doubtful"}
# NBA tricodes in the injury PDF that differ from ESPN's abbreviations
NBA_TO_ESPN = {"NOP": "NO", "UTA": "UTAH", "GSW": "GS", "SAS": "SA", "NYK": "NY", "WAS": "WSH"}


# --------------------------------------------------------------------------- #
# Key players
# --------------------------------------------------------------------------- #
def key_players(player_rows: list[dict], team_games: dict[str, int]) -> dict[str, dict]:
    """{team_id: {norm_name: info}} for players who matter.

    Key = started at least half of the team's games, or is top 5 on the team
    in minutes per game (min. 3 games played).
    """
    agg = defaultdict(lambda: {"starts": 0, "games": 0, "minutes": 0, "pts": 0, "name": "", "team_id": ""})
    for r in player_rows:
        a = agg[(r["team_id"], r["player_id"])]
        a["name"], a["team_id"] = r["player"], r["team_id"]
        a["starts"] += r["starter"]
        if not r["dnp"] and r["minutes"] > 0:
            a["games"] += 1
            a["minutes"] += r["minutes"]
            a["pts"] += r["pts"]
    by_team = defaultdict(list)
    for a in agg.values():
        a["mpg"] = a["minutes"] / a["games"] if a["games"] else 0
        a["ppg"] = a["pts"] / a["games"] if a["games"] else 0
        by_team[a["team_id"]].append(a)
    out = {}
    for tid, players in by_team.items():
        tg = max(team_games.get(tid, 1), 1)
        top5 = sorted((p for p in players if p["games"] >= 3), key=lambda p: -p["mpg"])[:5]
        top5_names = {p["name"] for p in top5}
        out[tid] = {}
        for p in players:
            is_starter = p["starts"] >= max(2, tg / 2)
            if is_starter or p["name"] in top5_names:
                out[tid][norm(p["name"])] = {
                    "name": p["name"], "starts": p["starts"], "team_games": tg,
                    "mpg": round(p["mpg"], 1), "ppg": round(p["ppg"], 1),
                    "why": "starter" if is_starter else "top-5 minutes",
                }
    return out


# --------------------------------------------------------------------------- #
# Injuries
# --------------------------------------------------------------------------- #
def load_injuries(teams: list[dict], game_day: date, playing: set[str]) -> dict:
    """Official PDF first; ESPN for any team the PDF doesn't cover."""
    by_key = {norm(t["display"]): t["id"] for t in teams}
    by_abbr = {t["abbr"]: t["id"] for t in teams}
    result = {"source": None, "report_time": None, "url": None, "teams": defaultdict(list),
              "covered": set()}
    try:
        url, pdf = injury_report.latest_report()
        if pdf:
            rep = injury_report.parse(pdf)
            day = game_day.strftime("%m/%d/%Y")
            for r in rep["rows"]:
                tid = by_key.get(r["team_key"])
                if tid and r["game_date"] == day:
                    result["teams"][tid].append({"player": r["player"], "key": r["player_key"],
                                                 "status": r["status"], "reason": r["reason"]})
                # a game listed in the report means BOTH teams have reported, even
                # if one of them has nobody on it
                if r["game_date"] == day and "@" in r["matchup"]:
                    for tri in r["matchup"].split("@"):
                        tid2 = by_abbr.get(NBA_TO_ESPN.get(tri.strip(), tri.strip()))
                        if tid2:
                            result["covered"].add(tid2)
            for ns in rep["not_submitted"]:
                tid = by_key.get(norm(ns["team"]))
                result["covered"].discard(tid)  # not submitted yet: use ESPN for this team
            result.update(source="NBA official injury report", report_time=rep["report_time"], url=url)
    except Exception as e:  # PDF problems fall back to ESPN below
        print(f"  ! official injury report unavailable: {e}")
    try:
        espn_rows = espn.injuries()
        used = False
        for r in espn_rows:
            tid = r["team_id"] or by_key.get(norm(r["team_name"] or ""))
            if tid and tid in playing and tid not in result["covered"]:
                result["teams"][tid].append({"player": r["player"], "key": norm(r["player"] or ""),
                                             "status": r["status"], "reason": r["detail"],
                                             "source": "ESPN"})
                used = True
        if used:
            result["source"] = (result["source"] + " + ESPN backup") if result["source"] else "ESPN injuries"
    except Exception as e:
        print(f"  ! ESPN injuries unavailable: {e}")
    return result


# --------------------------------------------------------------------------- #
# Slate
# --------------------------------------------------------------------------- #
def team_block(side: dict, m: dict, pw: dict, b2b: bool, injuries: list, keys: dict, power_rank) -> dict:
    tid = side["id"]
    tm = m["teams"].get(tid, {})
    inj = []
    for i in injuries:
        k = keys.get(tid, {}).get(i["key"]) or next(
            (v for n, v in keys.get(tid, {}).items() if n == i["key"]), None)
        inj.append({**{k2: v for k2, v in i.items() if k2 != "key"},
                    "is_key": bool(k), "key_info": k, "counts_as_out": i["status"] in OUT_STATUSES})
    inj.sort(key=lambda x: (not x["is_key"], x["status"] not in OUT_STATUSES, x["player"]))
    p = pw.get(tid, {})
    return {
        "id": tid, "abbr": side["abbr"], "name": side["name"], "display": side["display"],
        "record": side.get("record") or tm.get("record"), "l3_record": tm.get("l3_record"),
        "b2b": b2b,
        "season": tm.get("season"), "last3": tm.get("last3"),
        "power": {"rank": p.get("rank"), "split": p.get("split", False), "own_net": p.get("own_net"),
                  "bpi": p.get("bpi"), "nba_com": p.get("nba_com")},
        "last3_games": [{**g, "opp_power_rank": power_rank(g["opp_id"])} for g in tm.get("last3_games", [])],
        "injuries": inj,
        "key_out": [i["player"] for i in inj if i["is_key"] and i["counts_as_out"]],
        "key_questionable": [i["player"] for i in inj if i["is_key"] and i["status"] == "Questionable"],
    }


def build(day: date) -> dict:
    teams = store.read_json("teams.json") or espn.teams()
    rows = store.load_team_games()
    m = metrics.compute(rows, before=day.isoformat())
    pw = (store.read_json("power.json") or {}).get("teams", {})
    power_rank = lambda tid: (pw.get(tid) or {}).get("rank")  # noqa: E731
    played_yesterday = {r["team_id"] for r in rows if r["game_date"] == (day - timedelta(days=1)).isoformat()}
    try:  # also check yesterday's scoreboard in case last night's games aren't saved yet
        for e in espn.parse_events(espn.scoreboard(day - timedelta(days=1))):
            played_yesterday |= {e["home"]["id"], e["away"]["id"]}
    except Exception as e:
        print(f"  ! yesterday's scoreboard unavailable: {e}")
    team_games = {tid: d["games"] for tid, d in m["teams"].items()}
    keys = key_players(store.load_player_games(), team_games)
    events = [e for e in espn.parse_events(espn.scoreboard(day)) if e["season_type"] == espn.REGULAR_SEASON]
    playing = {e[s]["id"] for e in events for s in ("home", "away")}
    inj = load_injuries(teams, day, playing)

    games = []
    for e in events:
        if e["season_type"] != espn.REGULAR_SEASON:
            continue
        tip_utc = datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00"))
        home = team_block(e["home"], m, pw, e["home"]["id"] in played_yesterday,
                          inj["teams"].get(e["home"]["id"], []), keys, power_rank)
        away = team_block(e["away"], m, pw, e["away"]["id"] in played_yesterday,
                          inj["teams"].get(e["away"]["id"], []), keys, power_rank)
        odds = e["odds"]
        fav_id = odds["favorite_id"] if odds else None
        games.append({
            "id": e["id"], "state": e["state"], "neutral": e["neutral"],
            "tip_utc": e["start_utc"], "tip_pt": tip_utc.astimezone(PT).strftime("%-I:%M %p PT"),
            "away": away, "home": home,
            "spread": odds,
            "favorite": "home" if fav_id == e["home"]["id"] else ("away" if fav_id == e["away"]["id"] else None),
        })
    games.sort(key=lambda g: g["tip_utc"])
    return {
        "date": day.isoformat(),
        "built_at": datetime.now(PT).isoformat(timespec="seconds"),
        "metrics_through": m["games_through"],
        "injury_source": inj["source"], "injury_report_time": inj["report_time"], "injury_url": inj["url"],
        "first_tip_utc": games[0]["tip_utc"] if games else None,
        "games": games,
    }


def main() -> int:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(ET).date()
    slate = build(day)
    os.makedirs(os.path.join(store.DATA, "slates"), exist_ok=True)
    store.write_json(f"slates/{day.isoformat()}.json", slate)
    lines = [f"## Slate for {day:%a %b %d, %Y}", "",
             f"{len(slate['games'])} games · metrics through {slate['metrics_through']} · "
             f"injuries: {slate['injury_source']} ({slate['injury_report_time']})", "",
             "| Tip | Matchup | Spread | Fav | B2B | Power (away/home) | Key out |", "|---|---|---|---|---|---|---|"]
    for g in slate["games"]:
        a, h = g["away"], g["home"]
        sp = g["spread"]["details"] if g["spread"] else "none"
        fav = (a if g["favorite"] == "away" else h)["abbr"] if g["favorite"] else "?"
        b2b = ", ".join(t["abbr"] for t in (a, h) if t["b2b"]) or "-"
        out = "; ".join(f"{t['abbr']}: {', '.join(t['key_out'])}" for t in (a, h) if t["key_out"]) or "-"
        lines.append(f"| {g['tip_pt']} | {a['abbr']} @ {h['abbr']}{' (neutral)' if g['neutral'] else ''} | "
                     f"{sp} | {fav} | {b2b} | #{a['power']['rank']} / #{h['power']['rank']} | {out} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
