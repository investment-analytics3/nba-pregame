"""Owner Nov 23: analyst readouts for tracked games and any game scoring over 20 (all boards)."""
import unittest

from nba import scoring as nba
from nfl import scoring as nfl
from cfb import scoring as cfb
from cbb import scoring as cbb
from nhl import scoring as nhl


class ReadoutOk(unittest.TestCase):
    def test_score_over_20_even_when_gated(self):
        for m in (nba, cbb, nhl):
            self.assertTrue(m.readout_ok({"score": 27, "tier": "out", "tracked": False}))
            self.assertFalse(m.readout_ok({"score": 20, "tier": "out", "tracked": False}))
            self.assertTrue(m.readout_ok({"score": 12, "tracked": True}))

    def test_football_hero_score(self):
        for m in (nfl, cfb):
            self.assertTrue(m.readout_ok({"hero": {"score": 21}, "tracked": False}))
            self.assertFalse(m.readout_ok({"hero": {"score": 19}, "tracked": False}))
            self.assertFalse(m.readout_ok(None))
