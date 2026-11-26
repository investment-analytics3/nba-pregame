"""Weather paper board: rule, fees, timing windows, settlement, results math, a full tick with a fake Kalshi."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wx import config as C  # noqa: E402
from wx import paper  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "wx_markets.json")


def markets():
    with open(FIX) as f:
        return json.load(f)


class Rule(unittest.TestCase):
    def test_fee_matches_kalshi_formula(self):
        self.assertEqual(paper.fee(0.98, 25), 0.04)    # 0.07*25*.98*.02 = 0.0343 -> 4c
        self.assertEqual(paper.fee(0.99, 10), 0.01)
        self.assertEqual(paper.fee(0.50, 1), 0.02)

    def test_qualifies_only_1_to_4c(self):
        q = {m["ticker"] for m in markets() if paper.qualifies(m)}
        self.assertEqual(q, {"KXHIGHNY-26NOV20-T69", "KXHIGHNY-26NOV20-B62.5", "KXHIGHLAX-26NOV20-T69",
                             "KXHIGHLAX-26NOV20-B62.5"})

    def test_make_bet_prices_no_at_one_minus_yes_bid(self):
        m = next(x for x in markets() if x["ticker"] == "KXHIGHNY-26NOV20-T69")
        now = datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc)
        b = paper.make_bet("main", "KXHIGHNY", m, now, 61.0)
        self.assertEqual(b["no_price"], 0.98)
        self.assertEqual(b["contracts"], 25)
        self.assertEqual(b["date"], "2026-11-20")
        self.assertAlmostEqual(b["cost"], 0.98 * 25 + b["fee"], places=2)
        self.assertEqual(b["forecast"], 61.0)
        self.assertEqual(b["bracket"], "70° or above")

    def test_thin_book_caps_contracts(self):
        m = dict(markets()[0], yes_bid_size_fp="7.5")
        b = paper.make_bet("main", "KXHIGHNY", m, datetime(2026, 11, 19, 22, tzinfo=timezone.utc))
        self.assertEqual(b["contracts"], 7)

    def test_settle_win_and_loss(self):
        m = next(x for x in markets() if x["ticker"] == "KXHIGHNY-26NOV20-T69")
        now = datetime(2026, 11, 19, 22, tzinfo=timezone.utc)
        w = paper.settle(paper.make_bet("main", "KXHIGHNY", m, now), "no", now)
        self.assertEqual(w["status"], "won")
        self.assertAlmostEqual(w["pnl"], 25 * 0.02 - w["fee"], places=2)
        lo = paper.settle(paper.make_bet("main", "KXHIGHNY", m, now), "yes", now)
        self.assertEqual(lo["status"], "lost")
        self.assertAlmostEqual(lo["pnl"], -25 * 0.98 - lo["fee"], places=2)


class Timing(unittest.TestCase):
    def test_close_time_matches_markets(self):
        for m in markets():
            s = m["event_ticker"].split("-")[0]
            real = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            self.assertEqual(paper.close_utc(s, paper.event_date(m["event_ticker"])), real)

    def test_due_window(self):
        # New York closes 2026-11-21 06:00Z for the Nov 20 event: the main check is due 32 h before (Nov 19 22:00Z)
        self.assertFalse(any(s == "KXHIGHNY" for s, _, _ in
                             paper.due_events("main", datetime(2026, 11, 19, 21, 45, tzinfo=timezone.utc), set())))
        due = paper.due_events("main", datetime(2026, 11, 19, 22, 5, tzinfo=timezone.utc), set())
        self.assertIn(("KXHIGHNY", date(2026, 11, 20)), [(s, d) for s, d, _ in due])
        # logged events are skipped; past the 3-hour window nothing is due
        self.assertNotIn("KXHIGHNY", [s for s, _, _ in paper.due_events(
            "main", datetime(2026, 11, 19, 22, 5, tzinfo=timezone.utc), {("main", "KXHIGHNY-26NOV20")})])
        self.assertNotIn(("KXHIGHNY", date(2026, 11, 20)), [(s, d) for s, d, _ in paper.due_events(
            "main", datetime(2026, 11, 20, 1, 30, tzinfo=timezone.utc), set())])

    def test_close_rule_across_daylight_saving(self):
        self.assertEqual(paper.close_utc("KXHIGHNY", date(2026, 10, 31)), datetime(2026, 11, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(paper.close_utc("KXHIGHNY", date(2026, 11, 2)), datetime(2026, 11, 3, 6, tzinfo=timezone.utc))
        self.assertEqual(paper.close_utc("KXHIGHTPHX", date(2026, 11, 2)), datetime(2026, 11, 3, 7, tzinfo=timezone.utc))
        self.assertEqual(paper.close_utc("KXHIGHLAX", date(2026, 11, 26)), datetime(2026, 11, 27, 9, tzinfo=timezone.utc))

    def test_every_series_has_a_city_and_close_hour(self):
        for s, (city, hr) in C.ALL_SERIES.items():
            self.assertTrue(city)
            self.assertTrue(hr.startswith("America/"))
        self.assertEqual(set(C.STATIONS), set(C.MAIN))


class Stats(unittest.TestCase):
    def test_stats(self):
        bets = [{"status": "won", "pnl": 0.46, "contracts": 25, "no_price": 0.98, "cost": 24.54},
                {"status": "won", "pnl": 0.21, "contracts": 25, "no_price": 0.99, "cost": 24.76},
                {"status": "lost", "pnl": -24.54, "contracts": 25, "no_price": 0.98, "cost": 24.54},
                {"status": "open", "pnl": None, "contracts": 25, "no_price": 0.97, "cost": 24.3}]
        s = paper.stats(bets)
        self.assertEqual((s["settled"], s["won"], s["lost"], s["open"]), (3, 2, 1, 1))
        self.assertAlmostEqual(s["pnl"], -23.87, places=2)
        self.assertAlmostEqual(s["win"], 66.7, places=1)
        self.assertIsNotNone(s["ci"])


class Tick(unittest.TestCase):
    """A full tick against a fake Kalshi: scan logs bets, a later tick settles them, pages build."""

    def test_tick_end_to_end(self):
        import scripts.wx_tick as tick
        from wx import pages
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(paper, "DATA", os.path.join(tmp, "data")), \
                    mock.patch.object(pages, "DOCS", os.path.join(tmp, "docs")), \
                    mock.patch.object(pages, "BOARD", os.path.join(tmp, "docs", "index.html")), \
                    mock.patch.object(tick, "STATE", os.path.join(tmp, "data", "state.json")), \
                    mock.patch("wx.kalshi.open_markets", side_effect=lambda s: [m for m in markets() if m["ticker"].startswith(s + "-")]), \
                    mock.patch("wx.kalshi.forecast_highs", return_value={"2026-11-20": 60.0}):
                led = paper.load()
                now = datetime(2026, 11, 19, 22, 5, tzinfo=timezone.utc)
                n = tick.scan(led, now, log=lambda *a: None)
                self.assertEqual(n, 2)                                   # New York's two 1-4c brackets
                self.assertEqual(tick.scan(led, now, log=lambda *a: None), 0)   # never twice
                later = datetime(2026, 11, 20, 1, 5, tzinfo=timezone.utc)   # Los Angeles' window
                self.assertEqual(tick.scan(led, later, log=lambda *a: None), 2)
                res = {"KXHIGHNY-26NOV20-T69": "no", "KXHIGHNY-26NOV20-B62.5": "no",
                       "KXHIGHLAX-26NOV20-T69": "no", "KXHIGHLAX-26NOV20-B62.5": "yes"}
                with mock.patch("wx.kalshi.market", side_effect=lambda t: {"result": res[t]}):
                    self.assertEqual(tick.settle_open(led, datetime(2026, 11, 21, 6, 30, tzinfo=timezone.utc), log=lambda *a: None), 0)
                    self.assertEqual(tick.settle_open(led, datetime(2026, 11, 21, 16, tzinfo=timezone.utc), log=lambda *a: None), 4)
                s = paper.stats([b for b in led["bets"] if b["group"] == "main"])
                self.assertEqual((s["won"], s["lost"]), (3, 1))
                paper.save(led)
                pages.publish(led, datetime(2026, 11, 21, 16, tzinfo=timezone.utc))
                with open(os.path.join(tmp, "docs", "results.html")) as f:
                    html = f.read()
                self.assertIn("3-1", html)
                self.assertIn("noindex", html)
                with open(os.path.join(tmp, "docs", "summary.json")) as f:
                    summ = json.load(f)
                self.assertEqual((summ["won"], summ["lost"]), (3, 1))
                title, text = tick.push_text(led, datetime(2026, 11, 21, 16, tzinfo=timezone.utc))
                self.assertIn("Running: 3-1", text)


if __name__ == "__main__":
    unittest.main()
