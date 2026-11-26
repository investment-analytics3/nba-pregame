"""Tennis board settings (owner decisions Nov 26 2026, voice + chat).

Gate: any Kalshi singles match whose favorite is priced -800 or shorter (89c+). Tracking only, never trades.
Tiers by price: -800 to -1000 (watch), -1000 to -2000 (main), shorter than -2000 (also tracked).
Qualified = -1000 to -2000 AND favorite Elo rank 1-25 AND opponent Elo rank 51+ (or unrated).
Two tabs: main tour + Challenger (hero 0-20, four metrics) and ITF (hero 0-10, Elo + form).
"""
from __future__ import annotations

# Kalshi series -> (tab, tour label, gender). "main" = ATP/WTA tour + Challengers, "itf" = ITF.
SERIES = {
    "KXATPMATCH": ("main", "ATP", "m"),
    "KXWTAMATCH": ("main", "WTA", "w"),
    "KXATPCHALLENGERMATCH": ("main", "ATP Challenger", "m"),
    "KXCHALLENGERMATCH": ("main", "ATP Challenger", "m"),
    "KXWTACHALLENGERMATCH": ("main", "WTA 125", "w"),
    "KXITFMATCH": ("itf", "ITF Men", "m"),
    "KXITFWMATCH": ("itf", "ITF Women", "w"),
}
TABS = {"main": "Main tour + Challenger", "itf": "ITF"}
MAX_SCORE = {"main": 20, "itf": 10}
BANDS = {"main": (15, 10), "itf": (8, 5)}            # green at/above first, yellow at/above second, else red

# Prices: Kalshi cents -> American moneyline. -800 = 88.9c, -1000 = 90.9c, -2000 = 95.2c.
GATE_PRICE = 800 / 900
TIERS = [("t800", "-800 to -1000", 800 / 900, 1000 / 1100), ("t1000", "-1000 to -2000", 1000 / 1100, 2000 / 2100 + 1e-9),
         ("t2000", "Shorter than -2000", 2000 / 2100 + 1e-9, 1.0)]

# Ranking tiers (owner): favorite 1-25 green, 26-50 yellow, 51+ red; opponent flipped. Elo rank is the power rank.
TOP, MID = 25, 50
COMEBACK = (50, 2.0, 25)      # Elo rank <= 50 and official rank >= 2 x Elo rank + 25 -> "Elo well above ranking" flag

# When the pregame price is logged: the first tick 3-6 h before Kalshi's listed start (listed starts are estimates;
# some matches start well before them, so closer to the start risks an in-play price).
LOG_WINDOW_H = (3.0, 6.0)
LATE_LOG_H = 1.0              # first seen inside 3 h of the listed start: logged as "late" down to 1 h before
STAKE = 100.0                 # results in dollars per $100 risked on the favorite
FEE_RATE = 0.07

# Metrics (main tab 5 points each; ITF uses Elo + form)
ELO_PTS = [(350, 5), (250, 4), (175, 3), (100, 2), (50, 1)]
SR_PTS = [(8.0, 5), (5.0, 4), (3.0, 3), (1.5, 2), (0.0, 1)]          # serve+return points won gap (pct points)
SR_ABS_PTS = [(108, 4), (104, 3), (101, 2), (98, 1)]                 # opponent has no stats: favorite's own level
FORM_PTS = [(0.85, 5), (0.70, 4), (0.60, 3), (0.50, 2), (0.34, 1)]
THIN_FORM = 4                 # fewer matches over the last two tournaments = "thin form", form capped at 3
FATIGUE_BASE = 3              # 3 + (opponent load - favorite load) / 90 minutes, capped 0-5
FORM_WEIGHT = [(50, 1.5), (150, 1.2), (400, 1.0)]                    # opponent rank -> weight; else 0.8

ELO_URL = {"m": "https://tennisabstract.com/reports/atp_elo_ratings.html",
           "w": "https://tennisabstract.com/reports/wta_elo_ratings.html"}
FRAGS_URL = "https://www.tennisabstract.com/jsfrags/{key}.js"
PLAYER_URL = {"m": "https://www.tennisabstract.com/cgi-bin/player.cgi?p={key}",
              "w": "https://www.tennisabstract.com/cgi-bin/wplayer.cgi?p={key}"}
LWOS_URL = "https://lastwordonsports.com/tennis/category/predictions/"
PLAYER_TTL_H = 48             # refresh a player's Tennis Abstract file at most every 2 days
FRAGS_PER_TICK = 14
HISTORY_DAYS = 28             # Kalshi settled history kept for form/fatigue (ITF) and grading
SITE = "https://investment-analytics3.github.io/nba-pregame"
