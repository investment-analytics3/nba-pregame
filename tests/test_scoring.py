"""Unit tests for the qualification tiers and the 40-point hero score.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import scoring  # noqa: E402


def team(abbr, season, last3, net=15, l3="2-1", power=15, b2b=False, key_out=(), key_q=(), split=False):
    s = dict(zip(scoring.METRICS, season))
    s["net"] = net
    return {"abbr": abbr, "season": {"ranks": s}, "last3": {"ranks": dict(zip(scoring.METRICS, last3))},
            "l3_record": l3, "b2b": b2b, "key_out": list(key_out), "key_questionable": list(key_q),
            "power": {"rank": power, "split": split}, "injuries_known": True}


def game(fav, dog, fav_side="home", neutral=False):
    g = {"neutral": neutral, "favorite": fav_side}
    g[fav_side] = fav
    g["away" if fav_side == "home" else "home"] = dog
    return g


class Qualification(unittest.TestCase):
    """Tiers (Nov 9): qualified = score 28+ or all-four mismatch; watch = 18-27; injury gate on all."""

    def test_score_route(self):
        # strong favorite, weak-ish underdog: score 28+ without the all-four mismatch
        fav = team("BOS", (4, 2, 7, 5), (3, 6, 9, 4), net=2, l3="3-0", power=3)
        dog = team("WAS", (27, 28, 29, 15), (22, 25, 26, 15), l3="0-3", power=30)
        r = scoring.score_game(game(fav, dog))
        self.assertGreaterEqual(r["score"], 28)
        self.assertEqual(r["tier"], "qualified")
        self.assertTrue(r["qualified"])
        self.assertEqual(r["routes"], [f"Score {r['score']}"])
        self.assertEqual(r["qualifiers"], ["eFG%", "Off Eff", "Opp eFG%", "Def Eff"])

    def test_mismatch_route_regardless_of_score(self):
        # all four top 10 / bottom 10 in both windows, but only 1-pointers and an away b2b favorite
        fav = team("A", (9, 9, 9, 9), (9, 9, 9, 9), net=15, l3="0-3", power=15, b2b=True)
        dog = team("B", (21, 21, 21, 21), (21, 21, 21, 21), l3="2-1", power=15)
        r = scoring.score_game(game(fav, dog, "away"))
        self.assertLess(r["score"], 28)
        self.assertEqual(r["tier"], "qualified")
        self.assertIn("All-four mismatch", r["routes"])

    def test_mismatch_needs_all_sixteen(self):
        fav = team("A", (9, 9, 9, 9), (9, 9, 9, 11), l3="0-3")       # one last-3 rank misses
        dog = team("B", (21,) * 4, (21,) * 4, l3="2-1")
        r = scoring.score_game(game(fav, dog, "away"))
        self.assertNotIn("All-four mismatch", r["routes"])
        self.assertIn("misses", r["rules"][1]["detail"])

    def test_watch_list(self):
        fav = team("A", (5,) * 4, (5,) * 4, l3="2-1")
        dog = team("B", (20,) * 4, (20,) * 4, l3="1-2")
        r = scoring.score_game(game(fav, dog))
        self.assertTrue(18 <= r["score"] < 28)
        self.assertEqual(r["tier"], "watch")
        self.assertFalse(r["qualified"])
        self.assertIn("Watch list", r["fails"][0])

    def test_low_score_is_no_track_even_if_old_rules_pass(self):
        # top 10 in one metric for season and last 3 only: passed the old rules, score is low now
        fav = team("MEM", (25, 9, 25, 25), (25, 9, 25, 25), l3="1-2", power=18)
        dog = team("TOR", (15,) * 4, (15,) * 4, l3="1-2")
        r = scoring.score_game(game(fav, dog, "away"))
        self.assertLess(r["score"], 18)
        self.assertEqual(r["tier"], "out")
        self.assertTrue(r["context"][0]["pass"])

    def test_injury_gate_on_every_route(self):
        dog = team("B", (30,) * 4, (30,) * 4, l3="0-3", power=30)
        out = scoring.score_game(game(team("A", (1,) * 4, (1,) * 4, net=1, l3="3-0", power=1, key_out=["Star"]), dog))
        self.assertEqual(out["tier"], "out")
        self.assertIn("Favorite key player out: Star", out["fails"])
        q = scoring.score_game(game(team("A", (1,) * 4, (1,) * 4, net=1, l3="3-0", power=1, key_q=["Star"]), dog))
        self.assertEqual(q["tier"], "qualified")
        self.assertTrue(any("questionable" in w for w in q["warnings"]))

    def test_underdog_injury_is_info_only(self):
        dog = team("B", (30,) * 4, (30,) * 4, l3="0-3", power=30, key_out=["Their star"])
        r = scoring.score_game(game(team("A", (1,) * 4, (1,) * 4, net=1, l3="3-0", power=1), dog))
        self.assertTrue(r["qualified"])
        self.assertTrue(any(w.startswith("Info:") for w in r["warnings"]))

    def test_order(self):
        mk = lambda tier, score: {"scoring": {"tier": tier, "score": score}, "tip_utc": "x"}
        games = [mk("out", 30), mk("watch", 20), mk("qualified", 12), mk("watch", 25)]
        self.assertEqual([(g["scoring"]["tier"], g["scoring"]["score"]) for g in scoring.order_games(games)],
                         [("qualified", 12), ("watch", 25), ("watch", 20), ("out", 30)])

    def test_no_spread(self):
        r = scoring.score_game({"favorite": None, "home": {}, "away": {}})
        self.assertFalse(r["scored"])
        self.assertEqual(r["tier"], "out")


class HeroScore(unittest.TestCase):
    def test_perfect_is_40(self):
        fav = team("A", (1, 1, 1, 1), (1, 1, 1, 1), net=1, l3="3-0", power=1)
        dog = team("B", (30,) * 4, (30,) * 4, l3="0-3", power=30)
        r = scoring.score_game(game(fav, dog, "home"))
        self.assertEqual(r["score"], 40)
        self.assertEqual(r["band"], "green")

    def test_tiers_do_not_stack(self):
        self.assertEqual(scoring.fav_points(3), 2)
        self.assertEqual(scoring.fav_points(8), 1)
        self.assertEqual(scoring.fav_points(11), 0)
        self.assertEqual(scoring.dog_points(28), 2)
        self.assertEqual(scoring.dog_points(22), 1)
        self.assertEqual(scoring.dog_points(20), 0)

    def test_home_rest_neutral(self):
        fav = team("A", (15,) * 4, (15,) * 4, net=15, l3="1-2", b2b=True)
        dog = team("B", (15,) * 4, (15,) * 4, l3="1-2")
        away = scoring.score_game(game(fav, dog, "away"))
        parts = {p["key"]: p["points"] for p in away["parts"]}
        self.assertEqual((parts["home"], parts["rest"], parts["health"]), (0, 0, 1))
        neutral = scoring.score_game(game(team("A", (15,) * 4, (15,) * 4, l3="1-2"), dog, "home", neutral=True))
        self.assertEqual({p["key"]: p["points"] for p in neutral["parts"]}["home"], 0)

    def test_bands(self):
        self.assertEqual(scoring.band(28), "green")
        self.assertEqual(scoring.band(27), "yellow")
        self.assertEqual(scoring.band(18), "yellow")
        self.assertEqual(scoring.band(17), "red")

    def test_pill_colors_flip_for_underdog(self):
        self.assertEqual(scoring.fav_color(4), "green")
        self.assertEqual(scoring.dog_color(4), "deep-red")
        self.assertEqual(scoring.dog_color(9), "red")
        self.assertEqual(scoring.dog_color(25), "green")


if __name__ == "__main__":
    unittest.main()
