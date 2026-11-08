"""Unit tests for the six rules and the 40-point hero score.

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
    def test_clean_qualifier(self):
        fav = team("BOS", (4, 2, 7, 5), (3, 6, 9, 4), net=2, l3="3-0", power=3)
        dog = team("WAS", (27, 28, 29, 30), (22, 25, 26, 28), l3="0-3", power=30)
        r = scoring.score_game(game(fav, dog))
        self.assertTrue(r["qualified"])
        self.assertEqual(r["qualifiers"], ["eFG%", "Off Eff", "Opp eFG%", "Def Eff"])

    def test_rule2_needs_same_metric(self):
        fav = team("A", (5, 15, 15, 15), (15, 5, 15, 15))  # season eFG top 10, last-3 only Off
        r = scoring.score_game(game(fav, team("B", (20,) * 4, (20,) * 4, l3="1-2")))
        self.assertFalse(r["qualified"])
        self.assertIn("not top 10 over last 3", r["fails"][0])

    def test_rule3_hot_underdog(self):
        fav = team("A", (5, 5, 5, 5), (5, 5, 5, 5))
        dog = team("B", (20,) * 4, (20, 8, 20, 20), l3="1-2")
        r = scoring.score_game(game(fav, dog))
        self.assertFalse(r["qualified"])
        self.assertIn("Off Eff", r["fails"][0])

    def test_rule4_underdog_three_oh(self):
        fav = team("A", (5,) * 4, (5,) * 4)
        r = scoring.score_game(game(fav, team("B", (20,) * 4, (20,) * 4, l3="3-0")))
        self.assertIn("Underdog is 3-0 in its last 3", r["fails"])

    def test_rule5_out_fails_questionable_warns(self):
        dog = team("B", (20,) * 4, (20,) * 4, l3="1-2")
        out = scoring.score_game(game(team("A", (5,) * 4, (5,) * 4, key_out=["Star"]), dog))
        self.assertFalse(out["qualified"])
        q = scoring.score_game(game(team("A", (5,) * 4, (5,) * 4, key_q=["Star"]), dog))
        self.assertTrue(q["qualified"])
        self.assertTrue(any("questionable" in w for w in q["warnings"]))

    def test_underdog_injury_is_info_only(self):
        dog = team("B", (20,) * 4, (20,) * 4, l3="1-2", key_out=["Their star"])
        r = scoring.score_game(game(team("A", (5,) * 4, (5,) * 4), dog))
        self.assertTrue(r["qualified"])
        self.assertTrue(any(w.startswith("Info:") for w in r["warnings"]))

    def test_no_spread(self):
        r = scoring.score_game({"favorite": None, "home": {}, "away": {}})
        self.assertFalse(r["scored"])


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
