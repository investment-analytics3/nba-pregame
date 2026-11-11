"""College football board: scoring, tiers, gates, scheduler, logger checkpoints, Kalshi matching.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb import kalshi, scoring  # noqa: E402
from cfb.metrics import METRICS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def team(season, last3, abbr="T", l2=("W", "W"), power=None, key_out=(), injuries_known=False, rest=7, qb_reset=None):
    t = {"abbr": abbr, "season": {"ranks": dict(zip(METRICS, season))}, "last3": {"ranks": dict(zip(METRICS, last3))},
         "last2_results": list(l2), "power": power or {}, "key_out": list(key_out), "injuries_known": injuries_known,
         "rest": rest, "qb_active": True}
    if qb_reset:
        t["qb_reset"] = qb_reset
    return t


class Rules(unittest.TestCase):
    def test_spread_band(self):
        self.assertTrue(scoring.in_band(-10) and scoring.in_band(20) and scoring.in_band(-14.5))
        self.assertFalse(scoring.in_band(-9.5) or scoring.in_band(-20.5) or scoring.in_band(None))

    def test_points_scale_to_138(self):
        self.assertEqual([scoring.fav_points(r) for r in (10, 11, 25, 26)], [2, 1, 1, 0])
        self.assertEqual([scoring.dog_points(r) for r in (25, 26, 113, 114)], [0, 1, 1, 2])

    def test_score_and_tiers(self):
        fav, dog = team((5,) * 4, (5,) * 4, "UGA"), team((120,) * 4, (120,) * 4, "VAN")
        r = scoring.score_game({"fav_home": True}, fav, dog)
        self.assertEqual(r["hero"]["score"], 16 + 16 + 4 + 1 + 0 + 2)              # health assumed full without a report
        self.assertEqual((r["tier"], r["call"], r["alerts"]), ("qualified", "Qualified", True))
        self.assertEqual(r["flags"][0]["text"], "No injury report for UGA yet: check before betting")
        mid = scoring.score_game({"fav_home": True}, team((30,) * 4, (30,) * 4), team((60,) * 4, (60,) * 4))
        self.assertEqual(mid["hero"]["score"], 0 + 8 + 4 + 1 + 2)
        self.assertEqual((mid["tier"], mid["tracked"], mid["alerts"]), ("out", False, False))
        watch = scoring.score_game({"fav_home": True}, team((20,) * 4, (20,) * 4), team((60,) * 4, (60,) * 4))
        self.assertEqual((watch["hero"]["score"], watch["tier"], watch["tracked"], watch["alerts"]), (23, "watch", True, False))

    def test_last3_mismatch_route_with_top_half_floor(self):
        fav = team((60, 69, 50, 40), (20, 25, 25, 90), "A", l2=("L", "L"))
        dog = team((40, 80, 90, 100), (114, 120, 130, 50), "B")
        q = scoring.qualification(fav, dog, 12)
        self.assertEqual((q["tier"], q["routes"]), ("qualified", ["Last-3 mismatch"]))
        fav70 = team((60, 70, 50, 40), (20, 25, 25, 90), "A")                       # 70th: outside the top half
        self.assertNotIn("Last-3 mismatch", scoring.qualification(fav70, dog, 12)["routes"])
        dog25 = team((25, 80, 90, 100), (114, 120, 130, 50), "B")                   # dog season top 25 once
        self.assertNotIn("Last-3 mismatch", scoring.qualification(fav, dog25, 12)["routes"])

    def test_power_gate_ap_cfp_fpi(self):
        fav = team((5,) * 4, (5,) * 4)
        for p, txt in (({"ap": 22}, "AP Top 25 #22"), ({"cfp": 25}, "CFP #25"), ({"fpi": 19}, "ESPN FPI #19")):
            q = scoring.qualification(fav, team((120,) * 4, (120,) * 4, "MIZ", power=p), 40)
            self.assertEqual(q["tier"], "out")
            self.assertEqual(q["fails"][0], f"MIZ ranked: {txt}")
        q = scoring.qualification(fav, team((120,) * 4, (120,) * 4, power={"fpi": 26, "consensus": 3}), 40)
        self.assertEqual(q["tier"], "qualified")                                    # consensus alone doesn't block

    def test_injury_gate_new_injuries_only(self):
        dog = team((120,) * 4, (120,) * 4)
        q = scoring.qualification(team((5,) * 4, (5,) * 4, "OSU", key_out=["Star WR"], injuries_known=True), dog, 30)
        self.assertEqual((q["tier"], q["fails"][0]), ("out", "OSU key player out: Star WR"))
        q = scoring.qualification(team((5,) * 4, (5,) * 4, "OSU", injuries_known=False), dog, 30)
        self.assertEqual(q["tier"], "qualified")                                    # no report = warning, not a block
        self.assertIn("No injury report", q["rules"][2]["detail"])

    def test_qb_reset_caps_call(self):
        r = scoring.score_game({"fav_home": True}, team((5,) * 4, (5,) * 4, qb_reset="J Doe out"), team((120,) * 4, (120,) * 4))
        self.assertEqual((r["tier"], r["call"]), ("qualified", "Lean (QB cap)"))


class Scheduler(unittest.TestCase):
    def setUp(self):
        self.s = _load("cfb_scheduler")

    def g(self, kick, tracked=True, locked=False):
        return {"kick_utc": kick.isoformat(), "tracked": tracked, "locked": locked}

    def test_morning_and_evening_on_weekdays(self):
        now = datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc)          # Tue 7:30 AM PT
        ctx = {"games": [self.g(datetime(2026, 11, 14, 20, tzinfo=timezone.utc))]}
        d = self.s.decide(now, ctx, {"last_build": "2026-11-10T02:00:00+00:00"})   # last build Mon 6 PM PT
        self.assertTrue(d["build"])
        d = self.s.decide(now, ctx, {"last_build": "2026-11-10T15:10:00+00:00"})   # already built this morning
        self.assertFalse(d["build"])
        eve = datetime(2026, 11, 11, 2, 5, tzinfo=timezone.utc)                    # Tue 6:05 PM PT
        self.assertTrue(self.s.decide(eve, ctx, {"last_build": "2026-11-10T15:10:00+00:00"})["build"])

    def test_lock_window_and_live(self):
        kick = datetime(2026, 11, 14, 20, tzinfo=timezone.utc)
        ctx = {"games": [self.g(kick)]}
        now = kick - timedelta(minutes=70)
        d = self.s.decide(now, ctx, {"last_build": (now - timedelta(minutes=20)).isoformat()})
        self.assertEqual((d["build"], d["why"]), (True, "kickoff within 75 min: lock tiers"))
        self.assertTrue(self.s.decide(kick - timedelta(minutes=10), ctx, {})["live"])
        self.assertFalse(self.s.decide(kick - timedelta(minutes=10), {"games": [self.g(kick, tracked=False)]}, {})["live"])

    def test_game_day_alert_once(self):
        kick = datetime(2026, 11, 14, 20, tzinfo=timezone.utc)
        now = datetime(2026, 11, 14, 14, 5, tzinfo=timezone.utc)                    # Sat 6:05 AM PT
        d = self.s.decide(now, {"games": [self.g(kick)]}, {"last_build": "2026-11-14T02:00:00+00:00"})
        self.assertTrue(d["build"] and d["alert"])
        d = self.s.decide(now, {"games": [self.g(kick)]}, {"last_build": "2026-11-14T02:00:00+00:00", "alerted": "2026-11-14"})
        self.assertFalse(d["alert"])


class Logger(unittest.TestCase):
    def setUp(self):
        self.L = _load("cfb_live")

    def test_checkpoints(self):
        ev = lambda per, st, state="in": {"period": per, "status": st, "state": state}  # noqa: E731
        self.assertEqual(self.L.due_checkpoints(ev(1, "STATUS_END_PERIOD"), {}), [("q1", False)])
        self.assertEqual(self.L.due_checkpoints(ev(2, "STATUS_HALFTIME"), {"q1": 1}), [("half", False)])
        self.assertEqual(self.L.due_checkpoints(ev(2, "STATUS_IN_PROGRESS"), {}), [("q1", True)])
        self.assertEqual(self.L.due_checkpoints(ev(3, "STATUS_END_PERIOD"), {"q1": 1, "half": 1}), [("q3", False)])
        self.assertEqual(self.L.due_checkpoints(ev(4, "STATUS_IN_PROGRESS", "post"), {}), [])

    def test_pushes_qualified_only(self):
        self.assertTrue(self.L.push_ok({"alerts": True}))
        self.assertFalse(self.L.push_ok({"alerts": False, "tracked": True}))


class Kalshi(unittest.TestCase):
    EVENTS = [{"event_ticker": "KXNCAAFGAME-26NOV14BUFFBALL", "markets": [
                  {"ticker": "KXNCAAFGAME-26NOV14BUFFBALL-BALL", "yes_sub_title": "Ball St.", "yes_bid_dollars": "0.47", "yes_ask_dollars": "0.48"},
                  {"ticker": "KXNCAAFGAME-26NOV14BUFFBALL-BUFF", "yes_sub_title": "Buffalo", "yes_bid_dollars": "0.52", "yes_ask_dollars": "0.53"}]},
              {"event_ticker": "KXNCAAFGAME-26NOV14MIAOHOH", "markets": [
                  {"ticker": "KXNCAAFGAME-26NOV14MIAOHOH-MIAOH", "yes_sub_title": "Miami (OH)", "yes_bid_dollars": "0.80", "yes_ask_dollars": "0.82"},
                  {"ticker": "KXNCAAFGAME-26NOV14MIAOHOH-OHIO", "yes_sub_title": "Ohio", "yes_bid_dollars": "0.18", "yes_ask_dollars": "0.20"}]}]

    def test_match_by_code_then_names(self):
        d = date(2026, 11, 14)
        e = kalshi.match(self.EVENTS, d, {"abbr": "BUFF", "names": ["Buffalo"]}, {"abbr": "BALL", "names": ["Ball State"]})
        self.assertEqual(e["event_ticker"], "KXNCAAFGAME-26NOV14BUFFBALL")
        e2 = kalshi.match(self.EVENTS, d, {"abbr": "M-OH", "names": ["Miami (OH)", "Miami (OH) RedHawks"]},
                          {"abbr": "OHIO", "names": ["Ohio", "Ohio Bobcats"]})
        self.assertEqual(e2["event_ticker"], "KXNCAAFGAME-26NOV14MIAOHOH")
        p = kalshi.prices(e2, {"abbr": "M-OH", "names": ["Miami (OH)"]}, {"abbr": "OHIO", "names": ["Ohio"]})
        self.assertEqual((p["away"]["prob"], p["home"]["prob"]), (0.81, 0.19))
        self.assertIsNone(kalshi.match(self.EVENTS, date(2026, 11, 15), {"abbr": "BUFF", "names": ["Buffalo"]},
                                       {"abbr": "BALL", "names": ["Ball State"]}))


if __name__ == "__main__":
    unittest.main()
