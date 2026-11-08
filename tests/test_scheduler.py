"""Scheduler catch-up rules.  Run: python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts.scheduler import live_due, nightly_due  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


def pt(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=PT)


def ev(gid, state, tip_pt, completed=False):
    return {"id": gid, "state": state, "completed": completed,
            "start_utc": tip_pt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}


class Nightly(unittest.TestCase):
    def test_not_before_3am(self):
        self.assertFalse(nightly_due(pt(9, "02:59"), {"nightly": "2026-11-08"}))

    def test_due_at_3am_and_caught_up_later(self):
        self.assertTrue(nightly_due(pt(9, "03:07"), {"nightly": "2026-11-08"}))
        # a missed 3 AM tick is caught up by any later tick that day
        self.assertTrue(nightly_due(pt(9, "09:45"), {"nightly": "2026-11-08"}))
        self.assertTrue(nightly_due(pt(9, "09:45"), {}))

    def test_once_per_day(self):
        self.assertFalse(nightly_due(pt(9, "03:22"), {"nightly": "2026-11-09"}))


class Live(unittest.TestCase):
    def test_only_tracked_games(self):
        events = [ev("1", "in", pt(8, "12:30"))]
        self.assertFalse(live_due(pt(8, "13:00"), events, {"2"}))
        self.assertTrue(live_due(pt(8, "13:00"), events, {"1"}))

    def test_starts_15_min_before_tip(self):
        events = [ev("1", "pre", pt(8, "17:00"))]
        self.assertFalse(live_due(pt(8, "16:40"), events, {"1"}))
        self.assertTrue(live_due(pt(8, "16:46"), events, {"1"}))

    def test_finished_game_stops(self):
        events = [ev("1", "post", pt(8, "12:30"), completed=True)]
        self.assertFalse(live_due(pt(8, "15:00"), events, {"1"}))


if __name__ == "__main__":
    unittest.main()
