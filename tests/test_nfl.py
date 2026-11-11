"""NFL rules, hero score, edge, flags, scheduler and checkpoint timing.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nfl import scoring  # noqa: E402
from scripts import nfl_scheduler as sch  # noqa: E402
from scripts import nfl_live  # noqa: E402
from scripts.nfl_live import due_checkpoints  # noqa: E402

M = scoring.METRICS


def team(season, last3, net=0.1, last2=("W", "W"), last3r=("W", "L", "W"), rest=7, **kw):
    t = {"season": {"ranks": dict(zip(M, season))}, "last3": {"ranks": dict(zip(M, last3))}, "net": net,
         "last2_results": list(last2), "last3_results": list(last3r), "rest": rest, "injuries_known": True,
         "key_out": [], "qb_out": [], "qb_active": True, "interior_ol_out": [], "key_questionable": []}
    t.update(kw)
    return t


class Rules(unittest.TestCase):
    """Tiers (owner Nov 10, as on the NBA board): qualified = score 28+ or last-3 mismatch; watch = 18-27;
    injury + power gates on every tier."""

    def test_score_route_and_watch_list(self):
        fav, dog = team((1,) * 4, (1,) * 4, abbr="KC"), team((20,) * 4, (20,) * 4, abbr="ATL")
        q = scoring.qualification(fav, dog, 30)
        self.assertEqual((q["tier"], q["routes"], q["tracked"], q["alerts"]), ("qualified", ["Score 30"], True, True))
        q = scoring.qualification(fav, dog, 23)
        self.assertEqual((q["tier"], q["tracked"], q["alerts"]), ("watch", True, False))      # live read, no pushes
        self.assertEqual(q["fails"], ["Watch list: score 23 (qualifies at 28)"])
        self.assertEqual(scoring.qualification(fav, dog, 17)["tier"], "out")

    def test_last3_mismatch_route(self):
        fav = team((12, 14, 15, 13), (9, 9, 9, 18), abbr="A")
        dog = team((11, 18, 20, 16), (23, 25, 15, 30), abbr="B")
        q = scoring.qualification(fav, dog, 12)
        self.assertEqual((q["tier"], q["routes"]), ("qualified", ["Last-3 mismatch"]))
        dog22 = team((11, 18, 20, 16), (22, 25, 15, 30), abbr="B")                      # 22 is not bottom 10 of 32
        self.assertNotIn("Last-3 mismatch", scoring.qualification(fav, dog22, 12)["routes"])
        weak_season = team((12, 14, 16, 13), (9, 9, 9, 18), abbr="A")                     # one season rank 16
        self.assertNotIn("Last-3 mismatch", scoring.qualification(weak_season, dog, 12)["routes"])
        dog_top10 = team((10, 18, 20, 16), (23, 25, 15, 30), abbr="B")
        self.assertNotIn("Last-3 mismatch", scoring.qualification(fav, dog_top10, 12)["routes"])

    def test_injury_gate(self):
        dog = team((20,) * 4, (20,) * 4)
        q = scoring.qualification(team((1,) * 4, (1,) * 4, abbr="JAX", key_out=["A Player"]), dog, 30)
        self.assertEqual(q["tier"], "out")
        self.assertEqual(q["fails"], ["JAX key starter out: A Player", "Would be qualified otherwise (score 30)"])
        q = scoring.qualification(team((1,) * 4, (1,) * 4, abbr="JAX", injuries_known=False), dog, 20)
        self.assertEqual(q["tier"], "out")
        self.assertIn("No injury report for JAX yet", q["fails"])

    def test_injured_reserve_does_not_block(self):
        # owner Nov 10: IR is long term (already in the stats); only new Out/Doubtful blocks a game
        from nfl import board
        keys = {"JAX": [{"name": "Anton Harrison", "pos": "T"}, {"name": "Eric Murray", "pos": "S"},
                        {"name": "Travis Hunter", "pos": "WR"}]}
        qbs = {"JAX": {"regular": "T.Lawrence", "dropbacks": 300}}
        inj = [{"name": "Anton Harrison", "pos": "T", "status": "Injured Reserve"},
               {"name": "Eric Murray", "pos": "S", "status": "Injured Reserve"}]
        t = board._team_block("JAX", {}, {}, qbs, keys, inj, 7)
        self.assertEqual((t["key_out"], t["key_long_term"]), ([], ["Anton Harrison", "Eric Murray"]))
        t.update(abbr="JAX", season={"ranks": {m: 1 for m in scoring.METRICS}}, last3={"ranks": {m: 1 for m in scoring.METRICS}})
        q = scoring.qualification(t, team((20,) * 4, (20,) * 4), 28)
        self.assertEqual(q["tier"], "qualified")
        self.assertIn("not counted: Anton Harrison, Eric Murray", q["rules"][2]["detail"])
        t2 = board._team_block("JAX", {}, {}, qbs, keys, inj + [{"name": "Travis Hunter", "pos": "WR", "status": "Doubtful"}], 7)
        self.assertEqual(t2["key_out"], ["Travis Hunter"])                     # a new injury still blocks
        qb_ir = board._team_block("JAX", {}, {}, qbs, keys, [{"name": "Trevor Lawrence", "pos": "QB", "status": "Injured Reserve"}], 7)
        self.assertEqual((qb_ir["key_out"], qb_ir["qb_reset"]), ([], "Trevor Lawrence out"))   # QB reset still applies

    def test_context_checks_kept(self):
        q = scoring.qualification(team((3, 12, 15, 20), (8, 1, 30, 30)), team((20,) * 4, (15, 14, 25, 11)), 20)
        self.assertEqual(q["qualifiers"], ["off_epa"])
        self.assertEqual([r["ok"] for r in q["context"]], [True, True, True, True])

    def test_opponent_power_gate(self):
        fav = team((1,) * 4, (1,) * 4)
        dog = team((20,) * 4, (20,) * 4, abbr="WSH", power={"consensus": 12, "fpi": 9, "nfl_com": 14})
        q = scoring.qualification(fav, dog, 30)
        self.assertEqual(q["tier"], "out")
        self.assertEqual(q["fails"][0], "WSH top 10 in power ranking: ESPN FPI #9")
        r = scoring.score_game({"spread": -3}, fav, dog)
        self.assertEqual(r["call"], "No track")
        self.assertEqual(r["flags"][0], {"level": "red", "text": "WSH top 10 in power ranking: ESPN FPI #9 (no track)"})
        dog["power"] = {"consensus": 11, "fpi": 12, "nfl_com": 11}
        self.assertEqual(scoring.qualification(fav, dog, 30)["tier"], "qualified")
        dog["power"] = {}                                  # no rankings at all: the gate passes
        self.assertEqual(scoring.qualification(fav, dog, 30)["tier"], "qualified")

    def test_calls_by_tier(self):
        fav, dog = team((1,) * 4, (1,) * 4, net=0.6), team((30,) * 4, (30,) * 4, net=0.0, last3r=("L", "L", "L"))
        self.assertEqual(scoring.score_game({"spread": -3}, fav, dog)["call"], "Edge clears")
        mid = team((8,) * 4, (8,) * 4, net=0.2)                                   # score in the watch band
        r = scoring.score_game({"spread": -3}, mid, team((15,) * 4, (15,) * 4))
        self.assertEqual((r["tier"], r["call"]), ("watch", "Watch list"))

    def test_logger_pushes_qualified_only(self):
        self.assertTrue(nfl_live.push_ok({"tracked": True, "alerts": True}))
        self.assertFalse(nfl_live.push_ok({"tracked": True, "alerts": False}))     # watch list
        self.assertTrue(nfl_live.push_ok({"tracked": True}))                       # older context

    def test_power_consensus(self):
        from nfl import power
        c = power.consensus({"A": 1, "B": 2, "C": 3}, {"A": 3, "B": 1})
        self.assertEqual((c["B"]["consensus"], c["A"]["consensus"], c["C"]["consensus"]), (1, 2, 3))
        self.assertEqual(power.team_code("Los Angeles Rams"), "LA")
        self.assertEqual(power.team_code("San Francisco 49ers"), "SF")


class Hero(unittest.TestCase):
    def test_max_is_40(self):
        fav = team((1,) * 4, (1,) * 4, rest=14)
        dog = team((30,) * 4, (30,) * 4)
        h = scoring.hero_score({"fav_home": True, "neutral": False}, fav, dog)
        self.assertEqual(h["score"], 40)
        self.assertEqual(h["parts"], {"fav_strength": 16, "dog_weakness": 16, "health": 4, "home": 1, "rest": 1, "form": 2})
        self.assertEqual(h["band"], "green")

    def test_points_and_bands(self):
        self.assertEqual([scoring.fav_points(r) for r in (1, 5, 6, 10, 11, None)], [2, 2, 1, 1, 0, 0])
        self.assertEqual([scoring.dog_points(r) for r in (1, 10, 11, 22, 23, 32)], [0, 0, 1, 1, 2, 2])
        self.assertEqual([scoring.band(s) for s in (17, 18, 27, 28)], ["red", "yellow", "yellow", "green"])

    def test_health_rest_home_form(self):
        fav = team((40,) * 4, (40,) * 4, qb_active=False, qb_out=["QB One"], key_out=["QB One"],
                   interior_ol_out=["G Two"], last2=("W", "L"), rest=7)
        dog = team((1,) * 4, (1,) * 4, rest=4)
        self.assertEqual(scoring.hero_score({}, team((1,) * 4, (1,) * 4, rest=4), dog)["parts"]["rest"], 0)  # both short (Thursday)
        self.assertEqual(scoring.hero_score({}, team((1,) * 4, (1,) * 4, injuries_known=False), dog)["parts"]["health"], 0)
        h = scoring.hero_score({"fav_home": True, "neutral": True}, fav, dog)
        self.assertEqual(h["parts"]["health"], 1)     # QB out (-2), interior OL out (-1); no other key starter out (+1)
        self.assertEqual(h["parts"]["home"], 0)       # neutral site
        self.assertEqual(h["parts"]["rest"], 1)       # underdog on a short week
        self.assertEqual(h["parts"]["form"], 1)


class Edge(unittest.TestCase):
    def test_thresholds(self):
        fav, dog = team((1,) * 4, (1,) * 4, net=0.30), team((1,) * 4, (1,) * 4, net=-0.06)
        for spread in (-3.0, -7.0, -14.0):            # one +0.35 bar, no max spread
            e = scoring.stat_edge(fav, dog, spread)
            self.assertTrue(e["clears"]); self.assertEqual(e["required"], 0.35)
        e = scoring.stat_edge(team((1,) * 4, (1,) * 4, net=0.20), dog, -10.0)
        self.assertFalse(e["clears"]); self.assertEqual(e["short_by"], 0.09)

    def test_key_numbers_and_movement_flag(self):
        self.assertEqual(scoring.crosses_key(-2.5, -3.5), [3])
        self.assertEqual(scoring.crosses_key(-6.5, -6.0), [])
        self.assertEqual(scoring.crosses_key(5.5, -3.0), [3])
        fav, dog = team((1,) * 4, (1,) * 4), team((1,) * 4, (1,) * 4)
        f = scoring.flags({"movement": 4.0, "open_spread": -2.0, "spread": -6.0}, fav, dog, {})
        self.assertTrue(any("4+" in x["text"] for x in f))

    def test_qb_reset_caps_at_lean(self):
        fav = team((1,) * 4, (1,) * 4, net=0.6, qb_reset="QB One out")
        dog = team((30,) * 4, (30,) * 4, net=0.0, last3r=("L", "L", "L"))
        self.assertEqual(scoring.score_game({"spread": -3}, fav, dog)["call"], "Lean (QB cap)")


class Scheduler(unittest.TestCase):
    def ctx(self, *kicks):
        return {"games": [{"kick_utc": k.isoformat()} for k in kicks]}

    def test_game_day_hourly_and_alert(self):
        kick = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)            # 10 AM PT
        now = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)             # 7 AM PT
        d = sch.decide(now, self.ctx(kick), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["build"]); self.assertTrue(d["alert"])
        d = sch.decide(now, self.ctx(kick), {"last_build": (now - timedelta(minutes=20)).isoformat(), "alerted": "2026-11-15"})
        self.assertFalse(d["build"]); self.assertFalse(d["alert"])

    def test_live_window(self):
        kick = datetime(2026, 11, 13, 1, 15, tzinfo=timezone.utc)
        self.assertTrue(sch.decide(kick - timedelta(minutes=10), self.ctx(kick), {})["live"])
        self.assertTrue(sch.decide(kick + timedelta(hours=3), self.ctx(kick), {})["live"])
        self.assertFalse(sch.decide(kick + timedelta(hours=5), self.ctx(kick), {})["live"])

    def test_week_rolls_over(self):
        kick = datetime(2026, 11, 10, 1, 15, tzinfo=timezone.utc)
        d = sch.decide(kick + timedelta(hours=12), self.ctx(kick), {"last_build": kick.isoformat()})
        self.assertTrue(d["build"])


class Checkpoints(unittest.TestCase):
    def test_on_time_and_late(self):
        self.assertEqual(due_checkpoints({"state": "in", "status": "STATUS_HALFTIME", "period": 2}, {}), [("half", False)])
        self.assertEqual(due_checkpoints({"state": "in", "status": "STATUS_END_PERIOD", "period": 3}, {"half": 1}), [("q3", False)])
        self.assertEqual(due_checkpoints({"state": "in", "status": "STATUS_IN_PROGRESS", "period": 4}, {"half": 1}), [("q3", True)])

    def test_fourth_quarter_windows(self):
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 4, "clock": "9:42"}
        self.assertEqual(due_checkpoints(ev, {"half": 1, "q3": 1}), [("q4_10", False)])
        ev["clock"] = "4:10"
        self.assertEqual(due_checkpoints(ev, {"half": 1, "q3": 1}), [("q4_10", True), ("q4_6", True)])
        self.assertEqual(due_checkpoints(ev, {"half": 1, "q3": 1, "q4_10": 1, "q4_6": 1}), [])

    def test_never_backfilled_from_a_final(self):
        self.assertEqual(due_checkpoints({"state": "post", "status": "STATUS_FINAL", "period": 4}, {}), [])


class Alerts(unittest.TestCase):
    G = {"id": "1", "tracked": True, "fav_abbr": "BUF", "away_abbr": "BUF", "home_abbr": "MIN", "kalshi_away": "BUF",
         "kalshi_home": "MIN", "kick_utc": "2026-11-10T01:15:00+00:00", "day_et": "2026-11-09", "fav": "BUF", "away": "BUF", "home": "MIN"}

    def setUp(self):
        self.sent = []
        self._orig = (nfl_live.notify.send_alert, nfl_live.engine_grade, nfl_live.espn.summary, nfl_live.kalshi.game_prices)
        nfl_live.notify.send_alert = lambda title, body, **k: self.sent.append((title, body, k))
        nfl_live.espn.summary = lambda eid: {}
        nfl_live.kalshi.game_prices = lambda *a: {"BUF": {"prob": 0.88}}

    def tearDown(self):
        nfl_live.notify.send_alert, nfl_live.engine_grade, nfl_live.espn.summary, nfl_live.kalshi.game_prices = self._orig

    def ev(self, clock, period=4):
        return {"id": "1", "state": "in", "period": period, "clock": clock, "home": {"score": 10}, "away": {"score": 24}}

    def test_bet_signal_once_tracked_only(self):
        nfl_live.engine_grade = lambda s, g: {"action": "BET", "pick": "BUF", "grade": "A", "clock": "4th 9:30",
                                              "answer": "BUF answered", "control": "BUF"}
        entry = {}
        self.assertTrue(nfl_live.bet_check(entry, self.G, self.ev("9:30")))
        self.assertFalse(nfl_live.bet_check(entry, self.G, self.ev("8:00")))        # never twice
        self.assertEqual(len(self.sent), 1)
        self.assertIn("NFL BET BUF ML (A)", self.sent[0][0]); self.assertIn("priced in", self.sent[0][1])
        self.assertFalse(nfl_live.bet_check({}, dict(self.G, tracked=False), self.ev("9:30")))
        self.assertFalse(nfl_live.bet_check({}, self.G, self.ev("4:59")))            # under 5:00: no new bets
        self.assertEqual(len(self.sent), 1)

    def test_no_alert_without_bet(self):
        nfl_live.engine_grade = lambda s, g: {"action": "NO BET", "grade": "B"}
        self.assertFalse(nfl_live.bet_check({}, self.G, self.ev("9:30")))
        self.assertEqual(self.sent, [])

    def test_end3_push(self):
        entry = {}
        rec = {"score": "BUF 24 - MIN 10", "lead": 14, "kalshi": {"prob": 0.91},
               "engine": {"headline": "LEAN BUF", "grade": "A", "action": "LEAN", "early": {"full": True}, "gates": []}}
        nfl_live.end3_alert(entry, self.G, rec)
        nfl_live.end3_alert(entry, self.G, rec)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("LEAN BUF (grade A)", self.sent[0][1]); self.assertIn("Kalshi BUF 91¢", self.sent[0][1])
        self.assertEqual(self.sent[0][2]["priority"], "high")


if __name__ == "__main__":
    unittest.main()
