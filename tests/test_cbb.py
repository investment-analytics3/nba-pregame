"""College basketball: scoring, early-season cap, gates, metrics, scheduler, checkpoint logic.

    python -m unittest tests.test_cbb -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from cbb import metrics, scoring  # noqa: E402
import cbb_scheduler  # noqa: E402
import cbb_live  # noqa: E402

M = ("efg", "off", "opp_efg", "def")


def team(abbr, season, last3, net=None, **kw):
    s = {"ranks": dict(zip(M, season))}
    s["ranks"]["net"] = net
    return {"abbr": abbr, "season": s, "last3": {"ranks": dict(zip(M, last3))}, "l3_record": kw.pop("l3", "2-1"),
            "games": kw.pop("games", 12), "power": kw.pop("power", {}), "injuries_known": False, "key_out": [], **kw}


LATE = "2027-01-20"
EARLY = "2026-11-20"


class Scoring(unittest.TestCase):
    def strong(self, **kw):
        fav = team("FAV", (5, 8, 10, 12), (3, 6, 9, 15), net=4, l3="3-0", **kw)
        dog = team("DOG", (330, 340, 320, 350), (360, 330, 345, 352), power={"rank": 340})
        fav["power"] = {"rank": 6}
        return fav, dog

    def test_band(self):
        self.assertTrue(scoring.in_band(-10))
        self.assertTrue(scoring.in_band(20))
        self.assertFalse(scoring.in_band(9.5))
        self.assertFalse(scoring.in_band(20.5))

    def test_points(self):
        self.assertEqual(scoring.fav_points(20), 2)
        self.assertEqual(scoring.fav_points(21), 1)
        self.assertEqual(scoring.fav_points(51), 0)
        self.assertEqual(scoring.dog_points(50), 0)
        self.assertEqual(scoring.dog_points(51), 1)
        self.assertEqual(scoring.dog_points(316), 2)

    def test_max_score_qualifies_after_cutover(self):
        fav, dog = self.strong()
        sc = scoring.score_game({"favorite": "home", "neutral": False}, fav, dog, LATE)
        self.assertEqual(sc["score"], 40)
        self.assertEqual(sc["tier"], "qualified")
        self.assertTrue(sc["alerts"])

    def test_early_season_holds_on_watch_list(self):
        fav, dog = self.strong()
        sc = scoring.score_game({"favorite": "home", "neutral": False}, fav, dog, EARLY)
        self.assertEqual(sc["tier"], "watch")
        self.assertTrue(sc["would_qualify"])
        self.assertTrue(sc["tracked"])
        self.assertFalse(sc["alerts"])
        self.assertIn("Early season", sc["fails"][0])

    def test_cutover_date(self):
        self.assertTrue(scoring.early_season("2026-12-14"))
        self.assertFalse(scoring.early_season("2026-12-15"))
        self.assertFalse(scoring.early_season("2027-03-01"))
        self.assertTrue(scoring.early_season("2026-11-03"))

    def test_power_gate_ap_and_bpi(self):
        fav, dog = self.strong()
        dog["power"] = {"rank": 300, "ap": 22}
        sc = scoring.score_game({"favorite": "home"}, fav, dog, LATE)
        self.assertEqual(sc["tier"], "out")
        self.assertIn("AP Top 25 #22", sc["fails"][0])
        dog["power"] = {"rank": 300, "bpi": 50}
        self.assertEqual(scoring.score_game({"favorite": "home"}, fav, dog, LATE)["tier"], "out")
        dog["power"] = {"rank": 300, "bpi": 51}
        self.assertEqual(scoring.score_game({"favorite": "home"}, fav, dog, LATE)["tier"], "qualified")

    def test_injury_gate_and_warning(self):
        fav, dog = self.strong()
        sc = scoring.score_game({"favorite": "home"}, fav, dog, LATE)
        self.assertTrue(any("No injury list" in w for w in sc["warnings"]))     # warning only
        fav["key_out"] = ["Star Guard"]
        self.assertEqual(scoring.score_game({"favorite": "home"}, fav, dog, LATE)["tier"], "out")

    def test_route_b(self):
        fav = team("FAV", (100, 120, 150, 170), (30, 40, 45, 60), net=90, l3="2-1")
        dog = team("DOG", (200, 210, 220, 230), (320, 330, 340, 100))
        sc = scoring.score_game({"favorite": "away", "neutral": True}, fav, dog, LATE)
        self.assertIn("Last-3 mismatch", sc["routes"])
        self.assertEqual(sc["tier"], "qualified")
        fav["season"]["ranks"]["def"] = 190         # outside the top half (1-182)
        self.assertNotIn("Last-3 mismatch", scoring.score_game({"favorite": "away"}, fav, dog, LATE)["routes"])

    def test_watch_and_out(self):
        fav = team("FAV", (60, 70, 80, 90), (60, 70, 80, 90), net=60)
        dog = team("DOG", (200, 210, 220, 230), (200, 210, 220, 230))
        sc = scoring.score_game({"favorite": "home"}, fav, dog, LATE)
        self.assertEqual(sc["score"], 8 + 1 + 1 + 1 + 1)   # dog metrics 8, form 1, home, health, rest
        self.assertEqual(sc["tier"], "out")


class Metrics(unittest.TestCase):
    def rows(self):
        def g(gid, day, a, b, ap, bp):
            base = dict(game_id=gid, game_date=day, field_goals_made=25, field_goals_attempted=60, three_point_field_goals_made=7,
                        free_throws_attempted=20, offensive_rebounds=10, total_turnovers=12)
            return [dict(base, team_id=a, opponent_team_id=b, team_home_away="home", team_abbreviation=str(a), team_score=ap),
                    dict(base, team_id=b, opponent_team_id=a, team_home_away="away", team_abbreviation=str(b), team_score=bp)]
        r = g(1, "2026-11-03", 1, 2, 80, 60) + g(2, "2026-11-05", 1, 3, 90, 70) + g(3, "2026-11-07", 2, 3, 70, 75) + g(4, "2026-11-09", 1, 99, 100, 40)
        return metrics.box_rows(pd.DataFrame(r))

    def test_d1_only_metrics_all_games_record(self):
        tm = metrics.compute(self.rows(), {1, 2, 3}, before="2026-11-10")
        self.assertEqual(tm[1]["games"], 2)            # the non-D-I game doesn't count in the metrics
        self.assertEqual(tm[1]["record"], "3-0")       # but counts in the record
        self.assertEqual(tm[1]["season"]["ranks"]["off"], 1)
        self.assertNotIn(99, tm)
        self.assertFalse(tm[1]["last3_games"][0]["d1_opp"])

    def test_no_hindsight(self):
        tm = metrics.compute(self.rows(), {1, 2, 3}, before="2026-11-05")
        self.assertEqual(tm[1]["games"], 1)

    def test_b2b(self):
        r = self.rows()
        self.assertTrue(metrics.b2b(r, 1, "2026-11-10"))
        self.assertFalse(metrics.b2b(r, 1, "2026-11-11"))


class Scheduler(unittest.TestCase):
    def ctx(self, day, tips, tracked=True, locked=False):
        return {"date": day, "games": [{"id": str(i), "tip_utc": t.isoformat(), "tracked": tracked, "locked": locked} for i, t in enumerate(tips)]}

    def test_alert_at_board_time_weekday(self):
        now = datetime(2026, 12, 16, 20, 30, tzinfo=timezone.utc)        # Wed 12:30 PM PT
        d = cbb_scheduler.decide(now, self.ctx("2026-12-16", [now + timedelta(hours=5)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["build"])
        self.assertTrue(d["alert"])

    def test_no_alert_before_board_time_or_twice(self):
        now = datetime(2026, 12, 16, 18, 30, tzinfo=timezone.utc)        # Wed 10:30 AM PT
        d = cbb_scheduler.decide(now, self.ctx("2026-12-16", [now + timedelta(hours=7)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["build"])
        self.assertFalse(d["alert"])
        now2 = now + timedelta(hours=2)
        d = cbb_scheduler.decide(now2, self.ctx("2026-12-16", [now2 + timedelta(hours=5)]),
                                 {"last_build": (now2 - timedelta(hours=2)).isoformat(), "alerted": "2026-12-16"})
        self.assertFalse(d["alert"])

    def test_weekend_board_time_8am(self):
        now = datetime(2026, 12, 19, 16, 15, tzinfo=timezone.utc)        # Sat 8:15 AM PT
        d = cbb_scheduler.decide(now, self.ctx("2026-12-19", [now + timedelta(hours=2)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["alert"])

    def test_lock_builds_every_15_min(self):
        now = datetime(2026, 12, 16, 22, 0, tzinfo=timezone.utc)
        d = cbb_scheduler.decide(now, self.ctx("2026-12-16", [now + timedelta(minutes=50)]), {"last_build": (now - timedelta(minutes=15)).isoformat()})
        self.assertTrue(d["build"])
        self.assertIn("lock", d["why"])

    def test_stale_board_rebuilds(self):
        now = datetime(2026, 12, 17, 15, 0, tzinfo=timezone.utc)
        d = cbb_scheduler.decide(now, self.ctx("2026-12-16", [now - timedelta(hours=15)]), {"last_build": (now - timedelta(minutes=20)).isoformat()})
        self.assertTrue(d["build"])

    def test_live(self):
        now = datetime(2026, 12, 16, 22, 0, tzinfo=timezone.utc)
        self.assertTrue(cbb_scheduler.decide(now, self.ctx("2026-12-16", [now - timedelta(hours=1)]), {})["live"])
        self.assertFalse(cbb_scheduler.decide(now, self.ctx("2026-12-16", [now - timedelta(hours=1)], tracked=False), {})["live"])


class Checkpoints(unittest.TestCase):
    def test_halftime_and_ten_minute_mark(self):
        ev = {"state": "in", "status": "STATUS_HALFTIME", "period": 1, "clock": "0:00"}
        self.assertEqual(cbb_live.due_checkpoints(ev, {}), [("half", False)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 2, "clock": "9:48"}
        self.assertEqual(cbb_live.due_checkpoints(ev, {"half": {}}), [("h2_10", False)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 2, "clock": "7:10"}
        self.assertEqual(cbb_live.due_checkpoints(ev, {"half": {}}), [("h2_10", True)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 2, "clock": "12:00"}
        self.assertEqual(cbb_live.due_checkpoints(ev, {"half": {}}), [])
        self.assertEqual(cbb_live.due_checkpoints({"state": "post"}, {}), [])

    def test_push_only_qualified(self):
        self.assertTrue(cbb_live.push_ok({"alerts": True}))
        self.assertFalse(cbb_live.push_ok({"alerts": False, "tracked": True}))


if __name__ == "__main__":
    unittest.main()
