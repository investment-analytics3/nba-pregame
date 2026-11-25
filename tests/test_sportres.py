"""Results pages for NFL / college football / college basketball / NHL (owner Nov 24)."""
import unittest

import sportres


def team(abbr, fav, **kw):
    t = {"abbr": abbr, "code": abbr, "is_fav": fav, "record": "5-2", "l3_record": "2-1", "b2b": False,
         "power": {"rank": 8, "consensus": 8}, "last2_results": ["W", "L"], "rest": 7,
         "season": {"off": 110.0, "ranks": {"off": 12}}, "last3": {"off": 112.0, "ranks": {"off": 4}},
         "last5": {"xgf60": 2.5, "ranks": {"xgf60": 3}}}
    t.update(kw)
    return t


class Grade(unittest.TestCase):
    def test_cbb_spread_cover_and_colors(self):
        g = {"id": 1, "day_et": "2026-11-24", "tip_utc": "2026-11-25T00:00:00Z", "favorite": "home", "state": "post",
             "home": team("AAA", True), "away": team("BBB", False), "spread": {"spread": 12.5},
             "score": {"home": 80, "away": 66}, "scoring": {"tier": "watch", "score": 21, "band": "yellow", "matchups": []}}
        r = sportres.row("cbb", g, "2026-11-24", "2026-11-24")
        self.assertEqual((r["favorite"], r["fav_pts"], r["opp_pts"], r["covered"], r["fav_won"]), ("AAA", 80, 66, "Yes", "Yes"))
        self.assertEqual(r["fav_metrics"]["season"]["off"]["c"], "green")       # rank 12 <= 50
        self.assertEqual(r["opp_metrics"]["last3"]["off"]["c"], "deep-red")     # opponent rank 4 <= elite 20

    def test_nhl_puck_line_and_pending(self):
        g = {"id": 2, "day_et": "2026-11-24", "tip_utc": "2026-11-25T00:00:00Z", "favorite": "away", "state": "in",
             "home": team("HHH", False), "away": team("AAA", True, goalie={"name": "A Goalie"}, goalie_status="regular"),
             "line": {"ml_away": -180, "fav_price": 0.64}, "score": {"home": 1, "away": 2},
             "scoring": {"tier": "out", "score": 23, "band": "green", "matchups": [{"met": True}]}}
        r = sportres.row("nhl", g, "2026-11-24", "2026-11-24")
        self.assertFalse(r.get("final"))
        self.assertEqual((r["line"], r["flag"], r["fav_team"]["goalie"], r["fav_team"]["goalie_role"]), ("-180", "Yes", "A Goalie", "Starter"))
        sportres.finish(r, 1, 2, "nhl")                    # won by one: no puck line
        self.assertEqual((r["fav_won"], r["covered"]), ("Yes", "No"))

    def test_football_favorite_by_code(self):
        g = {"id": 3, "day_et": "2026-11-22", "kick_utc": "2026-11-22T18:00:00Z", "fav": "AAA", "state": "post",
             "home": team("HHH", False), "away": team("AAA", True), "spread": -7.5, "score": {"home": 20, "away": 27},
             "scoring": {"tier": "qualified", "hero": {"score": 29, "band": "green"}, "matchups": []}}
        r = sportres.row("nfl", g, "2026-w11", "Week 11")
        self.assertEqual((r["favorite"], r["fav_site"], r["line"], r["covered"], r["score"]), ("AAA", "away", "-7.5", "No", 29))


if __name__ == "__main__":
    unittest.main()
