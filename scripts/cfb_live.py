#!/usr/bin/env python3
"""College football checkpoint logger: Kalshi price logging + phone pushes for Qualified games.

While college games are on, every ~60 s it reads ESPN's FBS scoreboard and, for each tracked
game on this week's board (Qualified + Watch list, data/cfb/context.json), records once each:
    q1     end of the 1st quarter
    half   halftime
    q3     end of the 3rd quarter
Each record holds the score, the favorite's lead, the favorite's REAL Kalshi price at that moment
(bid/ask/mid), ESPN's win probability, and (q3) the live engine's grade. The point (owner Nov 10):
the backtest showed favorites -10 to -20 win at the rate a fair price implies at every checkpoint,
so value exists only if Kalshi prices these moments below fair. A few weekends of real prices
answer that. If a poll misses the exact moment, the first poll after it is used and the record is
marked "late". The final score is recorded too.

Phone pushes: Qualified games only (Watch list = logged + Live page, no pushes), at most two per
game, never repeated across restarts:
    end3  end of the 3rd: score, lead, Kalshi price, the engine's read
    bet   the first BET signal in the 4th (15:00-5:00) from the live engine (docs/nfl/engine.js)
Every poll it also publishes live Kalshi prices to the "cfb-live-data" branch (prices.json) for the
Live page, which can't call Kalshi from a browser.

Writes data/cfb/live/<season>.json and docs/cfb/live.json, committed to main.

    python scripts/cfb_live.py [--max-minutes 330] [--once] [--no-push] [--no-alert]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import notify  # noqa: E402
from nba.http import FetchError  # noqa: E402
from cfb import espn, kalshi  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
CTX = os.path.join(ROOT, "data", "cfb", "context.json")
POLL = 60
PAGES = "https://investment-analytics3.github.io/nba-pregame/"
LABEL = {"q1": "End of 1st", "half": "Halftime", "q3": "End of 3rd"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def log_path(season: int) -> str:
    return os.path.join(ROOT, "data", "cfb", "live", f"{season}.json")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(season: int, book: dict):
    os.makedirs(os.path.dirname(log_path(season)), exist_ok=True)
    body = json.dumps(book, indent=1)
    with open(log_path(season), "w") as f:
        f.write(body)
    os.makedirs(os.path.join(ROOT, "docs", "cfb"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "cfb", "live.json"), "w") as f:
        f.write(body)


def push(msg: str):
    if "--no-push" in sys.argv:
        return
    try:
        run = lambda *a: subprocess.run(a, cwd=ROOT, check=True, capture_output=True)  # noqa: E731
        run("git", "add", "data/cfb/live", "docs/cfb/live.json")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
            return
        run("git", "-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-q", "-m", msg)
        for _ in range(3):
            if subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=ROOT).returncode == 0 and \
               subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
                return
            time.sleep(5)
        print("! push failed; will retry with the next checkpoint")
    except subprocess.CalledProcessError as e:
        print(f"! git: {e.stderr.decode()[:200]}")


def _secs(c):
    try:
        m, s = str(c).split(":")
        return int(m) * 60 + int(float(s))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Kalshi --
_k_events = None


def kalshi_prices(g: dict) -> dict | None:
    """{"event", "away": {...}, "home": {...}} for a game, fresh from Kalshi."""
    global _k_events
    a = {"abbr": g["away_abbr"], "names": g.get("away_names") or []}
    h = {"abbr": g["home_abbr"], "names": g.get("home_names") or []}
    ticker = g.get("kalshi_event")
    if not ticker:
        try:
            if _k_events is None:
                _k_events = kalshi.open_events()
            e = kalshi.match(_k_events, datetime.fromisoformat(g["kick_utc"]).astimezone(ET).date(), a, h)
        except FetchError:
            e = None
        if not e:
            return None
        ticker = g["kalshi_event"] = e["event_ticker"]
    markets = kalshi.event_prices(ticker)
    return kalshi.prices({"event_ticker": ticker, "markets": markets}, a, h) if markets else None


def fav_price(g: dict, k: dict | None) -> dict | None:
    side = "home" if g["fav_home"] else "away"
    return {"event": k["event"], **k[side]} if k and k.get(side) else None


# ------------------------------------------------------------------ engine --
def engine_grade(summ: dict, g: dict) -> dict | None:
    """The live engine's read for this moment (Node + docs/nfl/engine.js; football rules)."""
    if not summ:
        return None
    ctx = {"fav": g.get("fav_abbr"), "dog": g.get("dog_abbr"), "fav_defense_top10": g.get("fav_defense_top10"),
           "qbs": g.get("qbs") or {}, "date": g.get("day_et")}
    try:
        with tempfile.TemporaryDirectory() as d:
            sp, cp = os.path.join(d, "s.json"), os.path.join(d, "c.json")
            with open(sp, "w") as f:
                json.dump(summ, f)
            with open(cp, "w") as f:
                json.dump(ctx, f)
            out = subprocess.run(["node", os.path.join(ROOT, "scripts", "nfl_engine_cli.js"), sp, cp],
                                 capture_output=True, timeout=60, check=True)
            return json.loads(out.stdout)
    except Exception as e:      # the checkpoint is still logged without a grade
        print(f"! engine: {type(e).__name__}: {str(e)[:120]}")
        return None


# -------------------------------------------------------------- checkpoints --
def due_checkpoints(ev: dict, entry: dict) -> list[tuple[str, bool]]:
    out = []
    st, per = ev.get("status") or "", ev.get("period") or 0
    if ev.get("state") != "in":
        return out                       # a missed checkpoint stays missing; never back-filled from a final
    if "q1" not in entry:
        if per == 1 and st == "STATUS_END_PERIOD":
            out.append(("q1", False))
        elif per == 2:
            out.append(("q1", True))
    if "half" not in entry:
        if st == "STATUS_HALFTIME":
            out.append(("half", False))
        elif per == 3:
            out.append(("half", True))
    if "q3" not in entry:
        if per == 3 and st == "STATUS_END_PERIOD":
            out.append(("q3", False))
        elif per >= 4:
            out.append(("q3", True))
    return out


def snapshot(g: dict, ev: dict, kind: str, late: bool) -> dict:
    hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
    lead = (hs - as_) if g["fav_home"] else (as_ - hs)
    rec = {"at": datetime.now(PT).isoformat(timespec="seconds"), "late": late,
           "clock": f"Q{ev.get('period')} {ev.get('clock') or ''}".strip(),
           "score": f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}", "lead": lead, "espn_wp": None}
    try:
        summ = espn.summary(ev["id"])
    except FetchError:
        summ = None
    wp = (summ or {}).get("winprobability") or []
    if wp:
        try:
            h = float(wp[-1]["homeWinPercentage"])
            rec["espn_wp"] = round(h if g["fav_home"] else 1 - h, 3)
        except (KeyError, TypeError, ValueError):
            pass
    if kind == "q3":
        rec["engine"] = engine_grade(summ, g)
    k = fav_price(g, kalshi_prices(g))
    if k:
        rec["kalshi"] = k
    return rec


# ------------------------------------------------------------------ alerts --
def push_ok(g: dict) -> bool:
    return bool(g.get("alerts"))


def alert(entry: dict, g: dict, kind: str, title: str, body: str, priority: str = "default") -> bool:
    sent = entry.setdefault("alerts", {})
    if kind in sent or not push_ok(g) or "--no-alert" in sys.argv:
        return False
    try:
        notify.send_alert(title, body, click=f"{PAGES}cfb/game.html?g={g['id']}", priority=priority, tags="football")
    except Exception as e:      # logged either way; never retried into a flood
        print(f"! alert not sent ({kind}): {e}")
        sent[kind] = {"at": datetime.now(PT).isoformat(timespec="seconds"), "error": str(e)[:120]}
        return False
    sent[kind] = {"at": datetime.now(PT).isoformat(timespec="seconds"), "title": title}
    print(f"  ALERT {kind}: {title} | {body}")
    return True


def end3_alert(entry: dict, g: dict, rec: dict):
    en = rec.get("engine") or {}
    k = rec.get("kalshi") or {}
    signal = "lead 14+: won 340 of 342 in the backtest" if rec["lead"] >= 14 else (
        "lead 10-13: won 98% in the backtest" if rec["lead"] >= 10 else "lead under 10")
    title = f"College end of 3rd: {rec['score']}"
    body = (f"{g['fav_abbr']} lead {rec['lead']:+d} ({signal})."
            + (f" Kalshi {g['fav_abbr']} {round(k['prob'] * 100)}¢." if k.get("prob") else " No Kalshi price.")
            + (f" Read: {en['headline']} ({en.get('grade', '–')})." if en.get("headline") else ""))
    alert(entry, g, "end3", title, body, "high" if rec["lead"] >= 14 else "default")


def bet_check(entry: dict, g: dict, ev: dict) -> bool:
    if not push_ok(g) or "bet" in entry.get("alerts", {}) or ev.get("period") != 4:
        return False
    clock = _secs(ev.get("clock"))
    if clock is None or clock < 300:
        return False
    try:
        summ = espn.summary(ev["id"])
    except FetchError:
        return False
    en = engine_grade(summ, g)
    if not en or en.get("action") != "BET":
        return False
    hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
    entry["bet_signal"] = {"at": datetime.now(PT).isoformat(timespec="seconds"), "clock": en.get("clock"),
                           "score": f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}", "engine": en}
    k = kalshi_prices(g) or {}
    side = "away" if en.get("pick") == g["away_abbr"] else "home"
    p = (k.get(side) or {}).get("prob")
    title = f"College BET {en['pick']} ML ({en['grade']}) · {en.get('clock', '')}"
    body = (f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}. {en.get('answer', '')}. Control: {en.get('control') or 'none'}."
            + (f" Kalshi {en['pick']} {round(p * 100)}¢" + (" (85%+: mostly priced in)" if p >= 0.85 else "") if p else ""))
    alert(entry, g, "bet", title, body, "high")
    return True


# ------------------------------------------------------------ live prices --
_published = None


def publish_prices(games: dict, events: list):
    """Force-push live Kalshi prices to the cfb-live-data branch (one file, one commit)."""
    global _published
    token = os.environ.get("GH_TOKEN")
    out = {"updated": datetime.now(PT).isoformat(timespec="seconds"), "games": {}}
    for ev in events:
        g = games.get(ev["id"])
        if not g or ev["state"] != "in":
            continue
        k = kalshi_prices(g)
        if not k:
            continue
        pr = {g["away_abbr"]: (k.get("away") or {}).get("prob"), g["home_abbr"]: (k.get("home") or {}).get("prob")}
        out["games"][ev["id"]] = {"event": k["event"], "prices": {a: p for a, p in pr.items() if p is not None}}
    body = json.dumps(out["games"], sort_keys=True)
    if not token or body == _published or "--no-push" in sys.argv:
        return
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "prices.json"), "w") as f:
            json.dump(out, f, indent=1)
        try:
            gg = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)  # noqa: E731
            gg("init", "-q", "-b", "cfb-live-data")
            gg("add", "prices.json")
            gg("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
               "commit", "-q", "-m", f"prices {out['updated']}")
            repo = os.environ.get("GITHUB_REPOSITORY", "investment-analytics3/nba-pregame")
            gg("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "HEAD:cfb-live-data")
            _published = body
        except subprocess.CalledProcessError as e:
            print(f"! price publish failed: {e.stderr.decode()[:150]}")


def main() -> int:
    ctx = load(CTX, None)
    if not ctx or not ctx.get("games"):
        print("No college context (build the board first).")
        return 0
    season = ctx["season"]
    games = {g["id"]: g for g in ctx["games"] if g.get("tracked")}
    if not games:
        print("No tracked college games this week.")
        return 0
    book = load(log_path(season), {"season": season, "games": {}})
    stop = time.time() + 60 * float(arg("--max-minutes", 330))
    while True:
        now = datetime.now(timezone.utc)
        days = {now.astimezone(ET).date()}
        if now.astimezone(ET).hour < 5:
            days.add(now.astimezone(ET).date() - timedelta(days=1))
        events = []
        for d in days:
            try:
                events += espn.parse_events(espn.scoreboard_date(f"{d:%Y%m%d}"))
            except FetchError as e:
                print(f"ESPN unavailable: {e}")
        changed = []
        for ev in events:
            g = games.get(ev["id"])
            if not g or ev["state"] == "pre":
                continue
            entry = book["games"].setdefault(ev["id"], {
                "week": ctx["week"], "day_et": g["day_et"], "away": g["away_abbr"], "home": g["home_abbr"],
                "fav": g["fav_abbr"], "dog": g["dog_abbr"], "spread": g["spread"], "tier": g["tier"], "hero": g.get("hero")})
            for kind, late in due_checkpoints(ev, entry):
                entry[kind] = snapshot(g, ev, kind, late)
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} {kind}")
                r = entry[kind]
                print(f"{datetime.now(PT):%-I:%M %p} {g['away_abbr']}@{g['home_abbr']} {kind}{' (late)' if late else ''}: "
                      f"{r['score']} lead {r['lead']} kalshi {(r.get('kalshi') or {}).get('prob')} wp {r['espn_wp']}")
                if kind == "q3" and not (late and (_secs(ev.get("clock")) or 0) < 600):   # skip a stale end-of-3rd push
                    end3_alert(entry, g, r)
            if ev["state"] == "in" and bet_check(entry, g, ev):
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} bet signal")
            if ev["state"] == "post" and "final" not in entry:
                hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
                margin = (hs - as_) if g["fav_home"] else (as_ - hs)
                entry["final"] = {"score": f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}", "fav_margin": margin, "fav_won": margin > 0}
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} final")
        publish_prices(games, events)
        if changed:
            book["updated"] = datetime.now(PT).isoformat(timespec="seconds")
            save(season, book)
            push("College checkpoints: " + ", ".join(changed)[:180])
        live = [e for e in events if e["id"] in games and e["state"] == "in"]
        upcoming = [g for g in games.values()
                    if timedelta(0) < datetime.fromisoformat(g["kick_utc"]) - now < timedelta(minutes=40)]
        if "--once" in sys.argv or time.time() > stop or (not live and not upcoming):
            print(f"Stopping: {len(live)} live, {len(upcoming)} kicking off within 40 min.")
            return 0
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
