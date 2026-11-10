"""When the board and final emails go out.  Run: python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from board_decide import decide  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=PT)


class Schedule(unittest.TestCase):
    # Nov 10, 2026 is a Tuesday; Nov 8 is a Sunday
    def test_weekday_two_emails(self):
        tips = [at(10, "16:00"), at(10, "19:00")]
        self.assertEqual(decide(at(10, "11:59"), tips, {})[0], "none")
        self.assertEqual(decide(at(10, "12:04"), tips, {})[0], "board")
        self.assertEqual(decide(at(10, "13:00"), tips, {"board": {}})[0], "none")
        self.assertEqual(decide(at(10, "14:34"), tips, {"board": {}})[0], "final")   # 90 min before 4:00
        self.assertEqual(decide(at(10, "15:00"), tips, {"board": {}, "final": {}})[0], "none")

    def test_no_board_the_evening_before(self):
        # 9 PM PT on Nov 9 is already Nov 10 in the East; the Nov 10 board waits for noon Nov 10
        from datetime import date
        tips = [at(10, "16:30"), at(10, "19:00")]
        self.assertEqual(decide(at(9, "21:00"), tips, {}, date(2026, 11, 10))[0], "none")
        self.assertEqual(decide(at(9, "23:45"), tips, {}, date(2026, 11, 10))[0], "none")
        self.assertEqual(decide(at(10, "12:04"), tips, {}, date(2026, 11, 10))[0], "board")

    def test_weekend_board_at_10(self):
        tips = [at(8, "12:30"), at(8, "18:00")]                # first tip 12:30, final at 11:00
        self.assertEqual(decide(at(8, "10:04"), tips, {})[0], "board")
        self.assertEqual(decide(at(8, "11:04"), tips, {"board": {}})[0], "final")

    def test_early_tip_only_final(self):
        tips = [at(8, "09:00")]                                # final 7:30, before the 10:00 board
        self.assertEqual(decide(at(8, "07:34"), tips, {})[0], "final")

    def test_close_together_merge(self):
        tips = [at(10, "13:00")]                               # final 11:30 is within 45 min of 12:00
        self.assertEqual(decide(at(10, "11:34"), tips, {})[0], "final")
        self.assertEqual(decide(at(10, "12:04"), tips, {"final": {}})[0], "none")

    def test_late_run_still_sends_final(self):
        tips = [at(10, "16:00"), at(10, "19:00")]
        self.assertEqual(decide(at(10, "16:20"), tips, {"board": {}})[0], "final")
        self.assertEqual(decide(at(10, "19:20"), tips, {"board": {}})[0], "none")

    def test_no_games(self):
        self.assertEqual(decide(at(10, "12:04"), [], {})[0], "none")


if __name__ == "__main__":
    unittest.main()
