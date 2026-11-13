#!/usr/bin/env python3
"""NHL checkpoint logger: Kalshi price logging + phone pushes for Qualified games.

While games are on, every ~60 s it reads ESPN's NHL scoreboard and, for each tracked game on today's board
(Qualified + Watch list, data/nhl/context.json), records once each:
    end1   end of the 1st period: the engine's readout (score, shots, expected goals, who controls play) and,
           for a one-goal lead, the 1st-period hold check level (strong / meets / none; information only)
    end2   end of the 2nd period: second readout
    p3_10  10:00 left in the 3rd: the decision point, with the engine's read and checklist
Each record holds the score, the favorite's lead, the favorite's REAL Kalshi price at that moment
(bid/ask/mid) and the engine read. ESPN has no hockey win probability, so the logged Kalshi price is the
only market check: the backtest (Nov 13) says top-7 favorites up 2+ at 10:00 of the 3rd won 74 of 74,
the log shows what Kalshi charges for that. The final score is recorded too.

Hold-check pushes (owner Nov 13): Qualified AND Watch list games, information only (never a bet signal):
    hold1  end of the 1st: favorite up exactly one and the 1st-period hold check meets or is strong (score 22+ and
           outshot or opponent defense 23-32; strong adds a top-5 defense): shots, both defense ranks, control, Kalshi
    hold2  end of the 2nd: the check met and the lead is still 1 or 2: 2nd-period shots, the same facts, Kalshi
Phone pushes for the decision: Qualified games only (Watch list = logged + Live page, no pushes), at most two per game,
never repeated across restarts:
    p3_10  10:00 of the 3rd: score, lead, Kalshi price, the engine's read and checklist
    bet    the first BET signal (3rd period 10:00-5:00, lead 2+) from the live engine (docs/nhl/engine.js)
Every poll it also publishes live Kalshi prices to the "nhl-live-data" branch (prices.json) for the
Live page, which can't call Kalshi from a browser.

Writes data/nhl/live/<season>.json and docs/nhl/live.json, committed to main.

    python scripts/nhl_live.py [--max-minutes 330] [--once] [--no-push] [--no-alert]
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
from nhl import espn, kalshi  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
CTX = os.path.join(ROOT, "data", "nhl", "context.json")
POLL = 60
PAGES = "https://investment-analytics3.github.io/nba-pregame/"
LABEL = {"end1": "End of 1st", "end2": "End of 2nd", "p3_10": "3rd period 10:00"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def log_path(season: int) -> str:
    return os.path.join(ROOT, "data", "nhl", "live", f"{season}.json")


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
    os.makedirs(os.path.join(ROOT, "docs", "nhl"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "nhl", "live.json"), "w") as f:
        f.write(body)


def push(msg: str):
    if "--no-push" in sys.argv:
        return
    try:
        run = lambda *a: subprocess.run(a, cwd=ROOT, check=True, capture_output=True)  # noqa: E731
        run("git", "add", "data/nhl/live", "docs/nhl/live.json")
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
    a = {"abbr": g.get("away_espn") or g["away_abbr"], "names": g.get("away_names") or []}     # Kalshi uses ESPN's codes
    h = {"abbr": g.get("home_espn") or g["home_abbr"], "names": g.get("home_names") or []}
    ticker = g.get("kalshi_event")
    if not ticker:
        try:
            if _k_events is None:
                _k_events = kalshi.open_events()
            e = kalshi.match(_k_events, datetime.fromisoformat(g["tip_utc"]).astimezone(ET).date(), a, h)
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
    """The live engine's read for this moment (Node + docs/nhl/engine.js)."""
    if not summ:
        return None
    st = g.get("fav_goalie_status")
    ctx = {"fav": g.get("fav_espn") or g.get("fav_abbr"), "date": g.get("day_et"), "favB2B": g.get("fav_b2b"),
           "score": g.get("score"), "favDefRank": g.get("fav_def_rank"), "dogDefRank": g.get("dog_def_rank"),
           "score": g.get("score"), "favDefRank": g.get("fav_def_rank"), "dogDefRank": g.get("dog_def_rank"),
           "favGoalieOk": None if st in (None, "unknown") else st == "regular", "favGoalieName": g.get("fav_goalie")}
    try:
        with tempfile.TemporaryDirectory() as d:
            sp, cp = os.path.join(d, "s.json"), os.path.join(d, "c.json")
            with open(sp, "w") as f:
                json.dump(summ, f)
            with open(cp, "w") as f:
                json.dump(ctx, f)
            out = subprocess.run(["node", os.path.join(ROOT, "scripts", "nhl_engine_cli.js"), sp, cp],
                                 capture_output=True, timeout=60, check=True)
            return json.loads(out.stdout)
    except Exception as e:      # the checkpoint is still logged without a grade
        print(f"! engine: {type(e).__name__}: {str(e)[:120]}")
        return None


# -------------------------------------------------------------- checkpoints --
def due_checkpoints(ev: dict, entry: dict) -> list[tuple[str, bool]]:
    out = []
    st, per, clock = ev.get("status") or "", ev.get("period") or 0, _secs(ev.get("clock"))
    if ev.get("state") != "in":
        return out                       # a missed checkpoint stays missing; never back-filled from a final
    between = st in ("STATUS_END_PERIOD", "STATUS_INTERMISSION") or (clock == 0)
    for kind, p in (("end1", 1), ("end2", 2)):
        if kind not in entry:
            if per == p and between:
                out.append((kind, False))
            elif per > p:
                out.append((kind, True))
    if "p3_10" not in entry:
        if per == 3 and clock is not None and clock <= 600 and not between:
            out.append(("p3_10", clock < 540))      # more than a minute past 10:00 = late
        elif per >= 4 or (per == 3 and between):
            out.append(("p3_10", True))
    return out


def snapshot(g: dict, ev: dict, kind: str, late: bool) -> dict:
    hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
    lead = (hs - as_) if g["fav_home"] else (as_ - hs)
    per = ev.get("period") or 0
    rec = {"at": datetime.now(PT).isoformat(timespec="seconds"), "late": late,
           "clock": f"{'OT' if per > 3 else 'P' + str(per)} {ev.get('clock') or ''}".strip(),
           "score": f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}", "lead": lead}
    try:
        summ = espn.summary(ev["id"])
    except FetchError:
        summ = None
    rec["engine"] = engine_grade(summ, g)
    k = fav_price(g, kalshi_prices(g))
    if k:
        rec["kalshi"] = k
    if kind == "end1" and (rec["engine"] or {}).get("hold1"):
        rec["hold1"] = rec["engine"]["hold1"]["level"]      # 1st-period hold check (information only)
    return rec


# ------------------------------------------------------------------ alerts --
def push_ok(g: dict) -> bool:
    return bool(g.get("alerts"))


def alert(entry: dict, g: dict, kind: str, title: str, body: str, priority: str = "default", ok: bool | None = None) -> bool:
    sent = entry.setdefault("alerts", {})
    if kind in sent or not (push_ok(g) if ok is None else ok) or "--no-alert" in sys.argv:
        return False
    try:
        notify.send_alert(title, body, click=f"{PAGES}nhl/game.html?g={g['id']}", priority=priority, tags="hockey")
    except Exception as e:      # logged either way; never retried into a flood
        print(f"! alert not sent ({kind}): {e}")
        sent[kind] = {"at": datetime.now(PT).isoformat(timespec="seconds"), "error": str(e)[:120]}
        return False
    sent[kind] = {"at": datetime.now(PT).isoformat(timespec="seconds"), "title": title}
    print(f"  ALERT {kind}: {title} | {body}")
    return True


def p3_alert(entry: dict, g: dict, rec: dict):
    en = rec.get("engine") or {}
    k = rec.get("kalshi") or {}
    ck = en.get("checklist") or {}
    signal = ("up 2+: top-7 favorites won 74 of 74 in the backtest" if rec["lead"] >= 2 else
              ("up 1: won about 94% in the backtest, but a one-goal lead is not a bet" if rec["lead"] == 1 else "no lead"))
    title = f"NHL 3rd period 10:00: {rec['score']}"
    body = (f"{g['fav_abbr']} lead {rec['lead']:+d} ({signal})."
            + (f" Kalshi {g['fav_abbr']} {round(k['prob'] * 100)}¢." if k.get("prob") else " No Kalshi price.")
            + (f" Checklist {ck['verdict']}." if ck.get("verdict") else "")
            + (f" Read: {en['headline']} ({en.get('grade', '–')})." if en.get("headline") else ""))
    alert(entry, g, "p3_10", title, body, "high" if rec["lead"] >= 2 else "default")


def _hold_facts(g: dict, en: dict, kind: str) -> str:
    """Shots, both defense ranks and who controls play, for the hold-check pushes."""
    fav, dog = g["fav_abbr"], g["dog_abbr"]
    side, other = ("home", "away") if g["fav_home"] else ("away", "home")
    ps = en.get("period_shots") or []
    bits = []
    if kind == "end1" and ps:
        bits.append(f"1st-period shots {fav} {ps[0].get(side, 0)}-{ps[0].get(other, 0)} {dog}.")
    elif kind == "end2" and len(ps) >= 2:
        s2 = (ps[1].get(side, 0), ps[1].get(other, 0))
        won = "won the 2nd" if s2[0] > s2[1] else ("lost the 2nd" if s2[0] < s2[1] else "even in the 2nd")
        bits.append(f"2nd-period shots {fav} {s2[0]}-{s2[1]} {dog} ({fav} {won}); game {ps[0].get(side, 0) + s2[0]}-{ps[0].get(other, 0) + s2[1]}.")
    bits.append(f"{dog} defense #{g.get('dog_def_rank') or '?'}, {fav} #{g.get('fav_def_rank') or '?'} (xGA/60).")
    if en.get("control"):
        bits.append(f"{en['control']} controls play ({en['xg_share']}% of xG, last 20 min)." if en["control"] == en.get("focus")
                    else f"{en['control']} controls play.")
    else:
        bits.append("Even play by expected goals.")
    return " ".join(bits)


def hold_alerts(entry: dict, g: dict, rec: dict, kind: str):
    """1st-period hold check pushes (owner Nov 13), Qualified + Watch list, information only:
    end1 = favorite up exactly one and the check meets or is strong; end2 = the check met and the lead is still 1 or 2."""
    en = rec.get("engine") or {}
    h1 = en.get("hold1") or {}
    if rec.get("late") or not h1.get("meets"):
        return
    k = (rec.get("kalshi") or {}).get("prob")
    fav = g["fav_abbr"]
    level = {"strong": "strong", "meets": "meets"}.get(h1.get("level"), "meets")
    tier = "Qualified" if g.get("tier") == "qualified" else "Watch list"
    if kind == "end1" and h1.get("lead") == 1:
        bt = h1.get("backtest") or {}
        title = f"NHL hold check ({level}): {rec['score']} after the 1st"
        body = (f"{fav} up 1 · {level} · {tier}, pregame score {g.get('score')}. " + _hold_facts(g, en, "end1")
                + (f" Kalshi {fav} {round(k * 100)}¢ vs {bt.get('pct')}% backtest ({bt.get('n')} games)." if k else
                   f" Backtest held {bt.get('pct')}% ({bt.get('n')} games); no Kalshi price.")
                + " Information only, not a bet.")
        alert(entry, g, "hold1", title, body, ok=bool(g.get("tracked")))
    elif kind == "end2":
        h2 = en.get("hold2") or {}
        if not h2.get("holds"):
            return
        bt = h2.get("backtest") or {}
        title = f"NHL hold check, still holding: {rec['score']} after two"
        body = (f"{fav} up {h2['lead']} ({level} after the 1st). " + _hold_facts(g, en, "end2")
                + (f" Kalshi {fav} {round(k * 100)}¢ vs {bt.get('pct')}% backtest ({bt.get('n')} games)." if k else
                   f" Backtest held {bt.get('pct')}% ({bt.get('n')} games); no Kalshi price.")
                + " Information only, not a bet.")
        alert(entry, g, "hold2", title, body, ok=bool(g.get("tracked")))


def bet_check(entry: dict, g: dict, ev: dict) -> bool:
    """First BET signal in the 3rd period 10:00-5:00 window."""
    if not push_ok(g) or "bet" in entry.get("alerts", {}) or ev.get("period") != 3:
        return False
    clock = _secs(ev.get("clock"))
    if clock is None or clock > 600 or clock < 300:
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
    side = "home" if g["fav_home"] else "away"
    p = (k.get(side) or {}).get("prob")
    title = f"NHL BET {g['fav_abbr']} ML ({en['grade']}) · {en.get('clock', '')}"
    body = (f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}. Control: {en.get('control') or 'even'}. {en.get('reason', '')}"
            + (f" Kalshi {g['fav_abbr']} {round(p * 100)}¢" + (" (85%+: mostly priced in)" if p >= 0.85 else "") if p else ""))
    alert(entry, g, "bet", title, body, "high")
    return True


# ------------------------------------------------------------ live prices --
_published = None


def publish_prices(games: dict, events: list):
    """Force-push live Kalshi prices to the nhl-live-data branch (one file, one commit)."""
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
        pr = {g.get("away_espn") or g["away_abbr"]: (k.get("away") or {}).get("prob"),     # keyed by ESPN codes (the Live page)
              g.get("home_espn") or g["home_abbr"]: (k.get("home") or {}).get("prob")}
        out["games"][ev["id"]] = {"event": k["event"], "prices": {a: p for a, p in pr.items() if p is not None}}
    body = json.dumps(out["games"], sort_keys=True)
    if not token or body == _published or "--no-push" in sys.argv:
        return
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "prices.json"), "w") as f:
            json.dump(out, f, indent=1)
        try:
            gg = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)  # noqa: E731
            gg("init", "-q", "-b", "nhl-live-data")
            gg("add", "prices.json")
            gg("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
               "commit", "-q", "-m", f"prices {out['updated']}")
            repo = os.environ.get("GITHUB_REPOSITORY", "investment-analytics3/nba-pregame")
            gg("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "HEAD:nhl-live-data")
            _published = body
        except subprocess.CalledProcessError as e:
            print(f"! price publish failed: {e.stderr.decode()[:150]}")


def main() -> int:
    ctx = load(CTX, None)
    if not ctx or not ctx.get("games"):
        print("No NHL context (build the board first).")
        return 0
    season = ctx["season"]
    games = {g["id"]: g for g in ctx["games"] if g.get("tracked")}
    if not games:
        print("No tracked NHL games on the board.")
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
                events += espn.parse_events(espn.scoreboard(f"{d:%Y%m%d}"))
            except FetchError as e:
                print(f"ESPN unavailable: {e}")
        changed = []
        for ev in events:
            g = games.get(ev["id"])
            if not g or ev["state"] == "pre":
                continue
            entry = book["games"].setdefault(ev["id"], {
                "day_et": g["day_et"], "away": g["away_abbr"], "home": g["home_abbr"],
                "fav": g["fav_abbr"], "dog": g["dog_abbr"], "fav_rank": g.get("fav_rank"), "dog_rank": g.get("dog_rank"), "tier": g["tier"],
                "score": g.get("score"), "fav_goalie": g.get("fav_goalie"), "fav_goalie_status": g.get("fav_goalie_status")})
            for kind, late in due_checkpoints(ev, entry):
                entry[kind] = snapshot(g, ev, kind, late)
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} {kind}")
                r = entry[kind]
                print(f"{datetime.now(PT):%-I:%M %p} {g['away_abbr']}@{g['home_abbr']} {kind}{' (late)' if late else ''}: "
                      f"{r['score']} lead {r['lead']} kalshi {(r.get('kalshi') or {}).get('prob')}")
                if kind in ("end1", "end2"):
                    hold_alerts(entry, g, r, kind)
                if kind == "p3_10" and not (late and ((ev.get("period") or 0) > 3 or (_secs(ev.get("clock")) or 0) < 420)):
                    p3_alert(entry, g, r)                    # skip a stale push
            if ev["state"] == "in" and bet_check(entry, g, ev):
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} bet signal")
            if ev["state"] == "post" and "final" not in entry:
                hs, as_ = ev["home"]["score"] or 0, ev["away"]["score"] or 0
                margin = (hs - as_) if g["fav_home"] else (as_ - hs)
                entry["final"] = {"score": f"{g['away_abbr']} {as_} - {g['home_abbr']} {hs}", "fav_margin": margin, "fav_won": margin > 0,
                                  "extra": (ev.get("period") or 3) > 3}
                changed.append(f"{g['away_abbr']}@{g['home_abbr']} final")
        publish_prices(games, events)
        if changed:
            book["updated"] = datetime.now(PT).isoformat(timespec="seconds")
            save(season, book)
            push("NHL checkpoints: " + ", ".join(changed)[:180])
        live = [e for e in events if e["id"] in games and e["state"] == "in"]
        upcoming = [g for g in games.values()
                    if timedelta(0) < datetime.fromisoformat(g["tip_utc"]) - now < timedelta(minutes=40)]
        if "--once" in sys.argv or time.time() > stop or (not live and not upcoming):
            print(f"Stopping: {len(live)} live, {len(upcoming)} starting within 40 min.")
            return 0
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
