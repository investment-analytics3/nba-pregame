"""Grading the running record.  Run: python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import results  # noqa: E402


def row(gid, team, opp, pts, opp_pts):
    return {"game_id": gid, "team": team, "opp": opp, "pts": pts, "opp_pts": opp_pts}


def snap(fav, dog, spread, qualified=True, score=25):
    return {"fav": fav, "dog": dog, "fav_side": "home", "spread_num": spread, "qualified": qualified,
            "score": score, "band": "yellow", "qualifiers": ["Def Eff"], "neutral": False}


LOG = {"2026-11-09": {"board": {"page": "boards/2026-11-09-board.html", "snapshot": {"1": snap("BOS", "MIL", 9.5)}},
                      "final": {"page": "boards/2026-11-09-final.html", "snapshot": {
                          "1": snap("BOS", "MIL", 10.0), "2": snap("PHI", "CHI", 4.0, qualified=False, score=12),
                          "3": snap("DEN", "UTAH", 6.0), "4": snap("OKC", "SAC", 8.0)}}}}
GAMES = [row("1", "BOS", "MIL", 120, 108), row("1", "MIL", "BOS", 108, 120),   # won by 12 > 10: covered
         row("2", "PHI", "CHI", 104, 100), row("2", "CHI", "PHI", 100, 104),   # won by 4 = 4: push
         row("3", "DEN", "UTAH", 99, 101), row("3", "UTAH", "DEN", 101, 99)]   # lost
# game 4 has no result yet (pending)


class Grading(unittest.TestCase):
    def setUp(self):
        rows, self.added = results.grade(LOG, GAMES, [])
        self.by = {r["game_id"]: r for r in rows}

    def test_uses_final_board(self):
        self.assertEqual(self.by["1"]["spread"], "-10")
        self.assertIn("2026-11-09-final.html#g-1", self.by["1"]["board_page"])

    def test_cover_push_loss(self):
        self.assertEqual((self.by["1"]["covered"], self.by["1"]["fav_won"]), ("Yes", "Yes"))
        self.assertEqual((self.by["2"]["covered"], self.by["2"]["fav_won"]), ("Push", "Yes"))
        self.assertEqual((self.by["3"]["covered"], self.by["3"]["fav_won"], self.by["3"]["winner"]), ("No", "No", "UTAH"))

    def test_above_line_and_pending(self):
        self.assertEqual(self.by["2"]["above_line"], "No")
        self.assertNotIn("4", self.by)
        self.assertEqual(self.added, 3)

    def test_existing_rows_not_regraded(self):
        rows, _ = results.grade(LOG, GAMES, [])
        again, added = results.grade(LOG, GAMES, rows)
        self.assertEqual(added, 0)
        self.assertEqual(len(again), 3)

    def test_summary(self):
        s = results.summary(list(self.by.values()))
        self.assertEqual((s["above"]["won"], s["above"]["lost"], s["above"]["covered"]), (1, 1, 1))
        self.assertEqual(s["below"]["push"], 1)



class Export(unittest.TestCase):
    def test_two_rows_per_game_with_colors(self):
        import csv, tempfile
        s = snap("BOS", "MIL", 10.0)
        s["fav_metrics"] = {"season": {"efg": {"v": 56.1, "r": 3}}, "last3": {}}
        s["dog_metrics"] = {"season": {"efg": {"v": 55.0, "r": 4}}, "last3": {}}
        games, _ = results.grade({"2026-11-09": {"final": {"page": "p.html", "snapshot": {"1": s}}}}, GAMES, [])
        path = os.path.join(tempfile.mkdtemp(), "r.csv")
        results.export_csv(games, path, "https://x/")
        rows = list(csv.DictReader(open(path)))
        self.assertEqual([r["role"] for r in rows], ["Favorite", "Opponent"])
        self.assertEqual((rows[0]["season_efg_color"], rows[1]["season_efg_color"]), ("green", "deep-red"))
        self.assertEqual(rows[0]["analysis_score"], "25")
        self.assertEqual(rows[0]["board_link"], "https://x/p.html#g-1")


if __name__ == "__main__":
    unittest.main()
