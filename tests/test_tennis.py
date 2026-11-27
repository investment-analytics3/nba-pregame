"""Tennis board: parsers (real samples), prices/tiers, scoring, ledger grading, a full tick with fake sources."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis import config as C  # noqa: E402
from tennis import ledger as L  # noqa: E402
from tennis import scoring  # noqa: E402
from tennis import sources as S  # noqa: E402
from tennis.history import History  # noqa: E402

F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tennis")


def read(name):
    with open(os.path.join(F, name)) as f:
        return f.read()


def elo_m():
    return S.parse_elo(read("atp_elo.html"))


class Parsers(unittest.TestCase):
    def test_elo(self):
        e = elo_m()
        self.assertEqual(e["updated"], "2026-11-23")
        s = e["players"]["jannik sinner"]
        self.assertEqual((s["elo_rank"], s["rank"], s["key"]), (1, 2, "JannikSinner"))
        self.assertGreater(s["elo"], 2200)

    def test_frags_season_and_recent(self):
        f = S.parse_frags(read("frags_LucaNardi.js"), "Nardi", 2026)
        self.assertEqual(f["season"]["m"], 30)                 # 6 tour + 24 Challenger matches with stats
        self.assertAlmostEqual(f["season"]["sr"], 101.8, places=1)
        r = f["recent"][0]
        self.assertEqual((r["tourney"], r["round"], r["won"], r["minutes"]), ("Metz CH", "QF", False, 57))
        self.assertTrue(f["recent"][1]["won"])
        self.assertEqual(f["recent"][1]["opp"], "Kyrian Jacquet")

    def test_kalshi_event(self):
        ev = json.loads(read("kalshi_itf_open.json"))["events"][0]
        m = S.match_from_event(ev)
        self.assertEqual((m["tourney"], m["round"]), ("M25 Bhopal", "Quarterfinal"))
        self.assertEqual(m["players"][0]["name"], "Karan Singh")
        self.assertEqual(S.price_of(m["players"][0]), 0.93)

    def test_settled_and_void(self):
        evs = json.loads(read("kalshi_chall_settled.json"))["events"]
        ms = [S.match_from_event(e) for e in evs]
        self.assertTrue(all(m["winner"] or m["void"] for m in ms if m))
        self.assertEqual(ms[0]["winner"], "Axel Nefve")

    def test_preview_link(self):
        links = S.parse_lwos(read("lwos.html"))
        self.assertIn("zverev-alcaraz", S.preview_link(links, "Alexander Zverev", "Carlos Alcaraz"))
        self.assertIsNone(S.preview_link(links, "Karan Singh", "Grigoriy Lomakin"))


class Prices(unittest.TestCase):
    def test_moneyline(self):
        self.assertEqual([S.moneyline(x) for x in (0.89, 0.91, 0.93, 0.95, 0.96, 0.2)], [-809, -1011, -1329, -1900, -2400, 400])

    def test_tiers(self):
        self.assertEqual(scoring.tier_of(0.88)[0], None)       # -733: under the -800 gate
        self.assertEqual(scoring.tier_of(0.89)[0], "t800")
        self.assertEqual(scoring.tier_of(0.91)[0], "t1000")
        self.assertEqual(scoring.tier_of(0.95)[0], "t1000")    # -1900
        self.assertEqual(scoring.tier_of(0.96)[0], "t2000")


def main_match(price=0.93, opp="Some Qualifier"):
    return {"event": "KXATPMATCH-26NOV30SINQUA", "series": "KXATPMATCH", "title": "Sinner vs Qualifier",
            "start": "2026-11-30T15:00:00Z", "tourney": "ATP Test Open", "round": "Round Of 32",
            "players": [{"name": "Jannik Sinner", "ask": price, "last": price}, {"name": opp, "ask": round(1 - price + 0.02, 2), "last": None}]}


class Scoring(unittest.TestCase):
    def ctx(self, hist=None):
        nardi = S.parse_frags(read("frags_LucaNardi.js"), "Nardi", 2026)
        return {"elo": {"m": elo_m(), "w": {}}, "ta": {"JannikSinner": nardi}, "hist": hist or History([]), "previews": []}

    def test_main_qualified_vs_unrated(self):
        sc = scoring.score_match(main_match(), 0, self.ctx())
        self.assertEqual(sc["tab"], "main")
        self.assertEqual(sc["max"], 20)
        self.assertEqual(sc["tier"], "t1000")
        self.assertEqual(sc["section"], "qualified")            # Elo #1 vs unrated, -1329
        self.assertEqual([m["key"] for m in sc["metrics"]], ["elo", "sr", "form", "fat"])
        self.assertEqual(sc["metrics"][0]["pts"], 5)            # huge Elo gap
        self.assertIn("opponent unrated", sc["metrics"][0]["note"])
        self.assertEqual(sc["ml"], -1329)
        self.assertTrue(sc["readout"].startswith("Sinner:"))

    def test_rank_miss_goes_to_watch(self):
        sc = scoring.score_match(main_match(opp="Carlos Alcaraz"), 0, self.ctx())
        self.assertEqual(sc["section"], "watch")                # opponent Elo #2 (top 25)
        sc = scoring.score_match(main_match(price=0.90), 0, self.ctx())
        self.assertEqual((sc["tier"], sc["section"]), ("t800", "watch"))
        sc = scoring.score_match(main_match(price=0.97), 0, self.ctx())
        self.assertEqual(sc["section"], "short")

    def test_itf_uses_history_form(self):
        rows = [{"e": f"E{i}", "s": "KXITFMATCH", "d": f"2026-11-{20 + i:02d}", "t": "M25 Bhopal" if i > 2 else "M15 Indore",
                 "r": "R", "p": ["Karan Singh", f"Opp {i}"], "w": "Karan Singh" if i != 2 else f"Opp {i}", "v": False} for i in range(1, 6)]
        ev = json.loads(read("kalshi_itf_open.json"))["events"][0]
        m = S.match_from_event(ev)
        sc = scoring.score_match(m, 0, {"elo": {"m": elo_m(), "w": {}}, "ta": {}, "hist": History(rows), "previews": []})
        self.assertEqual((sc["tab"], sc["max"]), ("itf", 10))
        self.assertEqual([x["key"] for x in sc["metrics"]], ["elo", "form"])
        self.assertEqual(sc["fav"]["form"]["w"], 4)
        self.assertEqual(sc["metrics"][0]["pts"], 0)            # neither player rated
        self.assertEqual(sc["max"], 10)

    def test_fatigue_and_comeback(self):
        rows = [{"e": "Y1", "s": "KXATPMATCH", "d": "2026-11-29", "t": "ATP Other Open", "r": "F",
                 "p": ["Some Qualifier", "X"], "w": "Some Qualifier", "v": False}]
        sc = scoring.score_match(main_match(), 0, self.ctx(History(rows)))
        fat = next(m for m in sc["metrics"] if m["key"] == "fat")
        self.assertIn("opp played yesterday", fat["note"])
        self.assertGreaterEqual(fat["pts"], 4)                  # opponent played yesterday + changed events
        self.assertTrue(scoring.comeback({"elo_rank": 10, "rank": 80}))
        self.assertFalse(scoring.comeback({"elo_rank": 1, "rank": 2}))


class Ledger(unittest.TestCase):
    def test_entry_grade_stats(self):
        nardi = S.parse_frags(read("frags_LucaNardi.js"), "Nardi", 2026)
        sc = scoring.score_match(main_match(), 0, {"elo": {"m": elo_m(), "w": {}}, "ta": {"JannikSinner": nardi},
                                                   "hist": History([]), "previews": []})
        now = datetime(2026, 11, 30, 10, tzinfo=timezone.utc)
        r = L.entry(sc, now, late=False)
        self.assertEqual(r["hours_before"], 5.0)
        self.assertEqual(r["contracts"], 108)                   # $100 / 93c
        w = copy.deepcopy(r)
        L.grade(w, "Jannik Sinner", False, now)
        self.assertEqual(w["status"], "won")
        self.assertAlmostEqual(w["pnl"], 108 * 0.07 - w["fee"], places=2)
        lo = copy.deepcopy(r)
        L.grade(lo, "Some Qualifier", False, now)
        v = copy.deepcopy(r)
        L.grade(v, None, True, now)
        early = copy.deepcopy(r)                             # settled 20 min after the log: price was mid-match
        L.grade(early, "Jannik Sinner", False, now, settled_ts=(now + timedelta(minutes=20)).isoformat())
        self.assertEqual((early["status"], early["void_reason"]), ("void", "logged mid-match"))
        ok = copy.deepcopy(r)
        L.grade(ok, "Jannik Sinner", False, now, settled_ts=(now + timedelta(hours=6)).isoformat())
        self.assertEqual(ok["status"], "won")
        st = L.stats([w, lo, v])
        self.assertEqual((st["won"], st["lost"], st["void"], st["n"]), (1, 1, 1, 2))
        self.assertEqual(st["avg_ml"], -1329)


class Tick(unittest.TestCase):
    """Scan logs ITF favorites in the 3-6 h window, history grades them, pages build."""

    def test_tick(self):
        import scripts.tennis_tick as tick
        from tennis import history, pages
        base = json.loads(read("kalshi_itf_open.json"))["events"]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        evs = copy.deepcopy(base)
        for i, e in enumerate(evs):              # ITF listed times 11 h, 17 h, ... from now (window 10-13 h)
            for mk in e["markets"]:
                mk["occurrence_datetime"] = (now + timedelta(hours=11 + 6 * i)).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "data")
            with mock.patch.object(tick, "DATA", d), mock.patch.object(L, "DATA", d), \
                    mock.patch.object(history, "DIR", os.path.join(d, "history")), \
                    mock.patch.object(pages, "DOCS", os.path.join(tmp, "docs")), \
                    mock.patch.object(pages, "BOARD", os.path.join(tmp, "docs", "index.html")), \
                    mock.patch("tennis.sources.kalshi_events", side_effect=lambda s, status="open", **k: evs if (s == "KXITFMATCH" and status == "open") else []), \
                    mock.patch("tennis.sources.fetch_elo", return_value=elo_m()), \
                    mock.patch("tennis.sources.get_text", return_value=read("lwos.html")), \
                    mock.patch("tennis.sources.fetch_frags", return_value=None), \
                    mock.patch.object(sys, "argv", ["tennis_tick.py"]):
                tick.main()
                with open(os.path.join(d, "ledger.json")) as f:
                    led = json.load(f)
                self.assertEqual([r["fav"]["name"] for r in led["matches"]], ["Karan Singh"])   # 93c, 11 h out
                self.assertEqual(led["matches"][0]["tab"], "itf")
                with open(os.path.join(tmp, "docs", "index.html")) as f:
                    html = f.read()
                self.assertIn("Karan Singh", html)
                self.assertIn("noindex", html)
                # settle it through history and re-run offline
                h = History([{"e": led["matches"][0]["event"], "s": "KXITFMATCH", "d": now.date().isoformat(), "t": "M25 Bhopal",
                              "r": "Quarterfinal", "p": ["Karan Singh", "Grigoriy Lomakin"], "w": "Karan Singh", "v": False}])
                h.save(now.date(), os.path.join(d, "history"))
                with mock.patch.object(sys, "argv", ["tennis_tick.py", "--no-fetch", "--force-publish"]):
                    tick.main()
                with open(os.path.join(d, "ledger.json")) as f:
                    self.assertEqual(json.load(f)["matches"][0]["status"], "open")     # --no-fetch does not save
                with open(os.path.join(tmp, "docs", "results.html")) as f:
                    self.assertIn("Tennis results", f.read())


if __name__ == "__main__":
    unittest.main()
