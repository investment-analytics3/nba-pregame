"""NHL: range filter, scoring, goalie cap, metrics, power consensus, scheduler, checkpoint logic.

    python -m unittest tests.test_nhl -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from nhl import metrics, power, scoring  # noqa: E402
import nhl_scheduler  # noqa: E402
import nhl_live  # noqa: E402

M = metrics.METRICS


def team(abbr, season, last5, rank, **kw):
    return {"abbr": abbr, "season": {"ranks": dict(zip(M, season))}, "last5": {"ranks": dict(zip(M, last5))},
            "l3_record": kw.pop("l3", "2-1-0"), "power": {"rank": rank}, "goalie_status": kw.pop("goalie", "regular"),
            "games": 20, "injuries_known": True, "key_out": [], **kw}


class Scoring(unittest.TestCase):
    def strong(self, **kw):
        return (team("FAV", (1, 2, 3, 4), (2, 3, 1, 5), 2, l3="3-0-0", **kw),
                team("DOG", (30, 31, 28, 25), (29, 32, 27, 24), 30, goalie="backup"))

    def test_range(self):
        self.assertTrue(scoring.in_range(7, 26))
        self.assertFalse(scoring.in_range(8, 32))
        self.assertFalse(scoring.in_range(1, 25))

    def test_max_score_qualifies(self):
        fav, dog = self.strong()
        sc = scoring.score_game({"favorite": "home"}, fav, dog, "2026-12-01")
        self.assertEqual(sc["score"], 40)
        self.assertEqual(sc["tier"], "qualified")
        self.assertTrue(sc["alerts"])

    def test_out_of_range_never_qualifies(self):
        fav, dog = self.strong()
        dog["power"]["rank"] = 20
        sc = scoring.score_game({"favorite": "home"}, fav, dog, "2026-12-01")
        self.assertEqual(sc["tier"], "out")
        self.assertIn("Not a top-7 vs bottom-7", sc["fails"][0])

    def test_backup_goalie_caps_at_watch_list(self):
        fav, dog = self.strong(goalie="backup")
        sc = scoring.score_game({"favorite": "home"}, fav, dog, "2026-12-01")
        self.assertEqual(sc["tier"], "watch")
        self.assertTrue(sc["would_qualify"])
        self.assertTrue(sc["flags"])
        self.assertFalse(sc["alerts"])

    def test_back_to_back_caps_at_watch_list(self):
        fav, dog = self.strong(b2b=True)
        sc = scoring.score_game({"favorite": "home"}, fav, dog, "2026-12-01")
        self.assertEqual(sc["tier"], "watch")
        self.assertIn("played yesterday", sc["fails"][0])

    def test_cutoff_is_22(self):
        fav = team("FAV", (6, 7, 8, 9), (6, 7, 8, 9), 3, l3="2-1-0")          # metrics 8
        dog = team("DOG", (24, 25, 26, 27), (24, 25, 26, 27), 30)            # 16
        sc = scoring.score_game({"favorite": "away"}, fav, dog, "2026-12-01")   # + goalie 2 + health 1 + rest 1 + form 1 = 29? no home
        self.assertEqual(sc["score"], 8 + 16 + 2 + 1 + 1 + 1)
        self.assertEqual(sc["tier"], "qualified")
        dog = team("DOG", (12, 13, 14, 15), (12, 13, 14, 15), 30)            # 8
        sc = scoring.score_game({"favorite": "away"}, fav, dog, "2026-12-01")
        self.assertEqual(sc["score"], 21)
        self.assertEqual(sc["tier"], "out")

    def test_goalie_not_posted_is_a_warning(self):
        fav, dog = self.strong(goalie="unknown")
        sc = scoring.score_game({"favorite": "home"}, fav, dog, "2026-12-01")
        self.assertTrue(any("not posted" in w for w in sc["warnings"]))

    def test_parts(self):
        fav = team("FAV", (8, 9, 12, 20), (6, 11, 15, 30), 5, l3="2-0-1", b2b=True)
        dog = team("DOG", (12, 23, 5, 10), (22, 23, 9, 11), 28)
        sc = scoring.score_game({"favorite": "away"}, fav, dog, "2026-12-01")
        parts = {p["key"]: p["points"] for p in sc["parts"]}
        self.assertEqual(parts["fav_metrics"], 1 + 1 + 1)        # 8, 9, 6 (6-10 = 1 each)
        self.assertEqual(parts["dog_metrics"], 1 + 2 + 0 + 0 + 1 + 2 + 0 + 1)
        self.assertEqual(parts["home"], 0)
        self.assertEqual(parts["rest"], 0)
        self.assertEqual(parts["form"], 1)


class Metrics(unittest.TestCase):
    def test_game_rows_and_compute(self):
        rows = []
        for i, (gid, d, h, a, ev, side, sk) in enumerate([
            (1, "2026-10-10", "AAA", "BBB", "SHOT", "home", (5, 5)), (1, "2026-10-10", "AAA", "BBB", "GOAL", "home", (5, 5)),
            (1, "2026-10-10", "AAA", "BBB", "BLOCKED_SHOT", "away", (5, 5)), (1, "2026-10-10", "AAA", "BBB", "FACEOFF", "home", (5, 4)),
            (1, "2026-10-10", "AAA", "BBB", "GOAL", "home", (5, 4))]):
            rows.append({"game_id": gid, "event_type": ev, "event_team_type": side, "period": 1, "period_type": "REGULAR", "game_seconds": i * 10,
                         "home_skaters": sk[0], "away_skaters": sk[1], "empty_net": False, "home_goalie_id": 1.0, "away_goalie_id": 2.0,
                         "event_goalie_id": 2.0 if side == "home" else 1.0, "xg": 0.2 if ev in ("SHOT", "GOAL") else None,
                         "season_type": "R", "game_date": d, "home_abbr": h, "away_abbr": a, "home_score": 0, "away_score": 0})
        r = metrics.game_rows(pd.DataFrame(rows))
        home = r[r["team"] == "AAA"].iloc[0]
        self.assertEqual(home["cf"], 2)
        self.assertEqual(home["ca"], 1)
        self.assertEqual(home["ppo"], 1)
        self.assertEqual(home["ppg"], 1)
        sched = pd.DataFrame([{"game_id": 1, "home_score": 2, "away_score": 0}])
        res = metrics.attach_results(r, sched)
        tm = metrics.compute(res, before="2026-10-11")
        self.assertEqual(tm["AAA"]["record"], "1-0-0")
        self.assertEqual(tm["AAA"]["season"]["ranks"]["xgf"], 1)
        self.assertNotIn("AAA", metrics.compute(res, before="2026-10-10"))


class Power(unittest.TestCase):
    def test_consensus_and_stale(self):
        own = {"A": 1, "B": 2, "C": 3}
        pub = {"espn": {"date": "2026-11-10", "url": "", "ranks": {"A": 3, "B": 1, "C": 2}}}
        c = power.consensus(own, pub)
        self.assertEqual(c["B"]["rank"], 1)        # (2+1)/2 = 1.5 beats A (1+3)/2 = 2
        with tempfile.TemporaryDirectory() as d:
            old = power.DIR
            power.DIR = d
            with open(os.path.join(d, "2026-11-01.json"), "w") as f:
                json.dump({"sources": {"espn": {"date": "2026-11-01", "ranks": {"A": 1}}}}, f)
            self.assertIn("espn", power.published(date(2026, 11, 5)))
            self.assertNotIn("espn", power.published(date(2026, 11, 20)))   # stale after 10 days
            power.DIR = old


class Scheduler(unittest.TestCase):
    def ctx(self, day, tips, tracked=True):
        return {"date": day, "games": [{"id": str(i), "tip_utc": t.isoformat(), "tracked": tracked, "locked": False} for i, t in enumerate(tips)]}

    def test_alert_after_1pm_weekday(self):
        now = datetime(2026, 11, 18, 21, 10, tzinfo=timezone.utc)       # Wed 1:10 PM PT
        d = nhl_scheduler.decide(now, self.ctx("2026-11-18", [now + timedelta(hours=3)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["alert"])
        now = datetime(2026, 11, 18, 19, 10, tzinfo=timezone.utc)       # 11:10 AM PT
        d = nhl_scheduler.decide(now, self.ctx("2026-11-18", [now + timedelta(hours=5)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertFalse(d["alert"])

    def test_weekend_9am(self):
        now = datetime(2026, 11, 21, 17, 5, tzinfo=timezone.utc)        # Sat 9:05 AM PT
        d = nhl_scheduler.decide(now, self.ctx("2026-11-21", [now + timedelta(hours=1)]), {"last_build": (now - timedelta(hours=2)).isoformat()})
        self.assertTrue(d["alert"])


class Checkpoints(unittest.TestCase):
    def test_marks(self):
        ev = {"state": "in", "status": "STATUS_END_PERIOD", "period": 1, "clock": "0:00"}
        self.assertEqual(nhl_live.due_checkpoints(ev, {}), [("end1", False)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 2, "clock": "12:00"}
        self.assertEqual(nhl_live.due_checkpoints(ev, {}), [("end1", True)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 3, "clock": "9:40"}
        self.assertEqual(nhl_live.due_checkpoints(ev, {"end1": {}, "end2": {}}), [("p3_10", False)])
        ev = {"state": "in", "status": "STATUS_IN_PROGRESS", "period": 3, "clock": "7:00"}
        self.assertEqual(nhl_live.due_checkpoints(ev, {"end1": {}, "end2": {}}), [("p3_10", True)])
        self.assertEqual(nhl_live.due_checkpoints({"state": "post"}, {}), [])

    def test_push_only_qualified(self):
        self.assertTrue(nhl_live.push_ok({"alerts": True}))
        self.assertFalse(nhl_live.push_ok({"alerts": False, "tracked": True}))


if __name__ == "__main__":
    unittest.main()
