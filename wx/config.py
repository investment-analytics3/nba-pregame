"""Series, groups and rule settings for the weather paper board.

Rule (owner Nov 26 2026, from the backtest in the project doc "weather/kalshi_longshot_backtest_findings.md"):
the day before the event, about 32 hours before the market closes (~5 PM local), buy NO on every
bracket whose best YES bid is 1-4c. Paper only: the system never trades.

Close time (checked against 2,496 archived events, Oct-Nov 2026): 1:00 AM local clock time the day after the event
(Phoenix, no daylight saving: midnight). Event KXHIGHNY-26NOV27 closes 2026-11-28 06:00Z. The tick still reads each
market's own close_time and decides from that.
"""
from __future__ import annotations

# series -> (city label, time zone of the station)
MAIN = {
    "KXHIGHNY": ("New York", "America/New_York"), "KXHIGHCHI": ("Chicago", "America/Chicago"), "KXHIGHMIA": ("Miami", "America/New_York"),
    "KXHIGHAUS": ("Austin", "America/Chicago"), "KXHIGHDEN": ("Denver", "America/Denver"), "KXHIGHLAX": ("Los Angeles", "America/Los_Angeles"),
    "KXHIGHPHIL": ("Philadelphia", "America/New_York"),
}
OTHER_HIGHS = {
    "KXHIGHTATL": ("Atlanta", "America/New_York"), "KXHIGHTBOS": ("Boston", "America/New_York"), "KXHIGHTDAL": ("Dallas", "America/Chicago"),
    "KXHIGHTDC": ("Washington", "America/New_York"), "KXHIGHTEWR": ("Newark", "America/New_York"), "KXHIGHTHOU": ("Houston", "America/Chicago"),
    "KXHIGHTLV": ("Las Vegas", "America/Los_Angeles"), "KXHIGHTMIN": ("Minneapolis", "America/Chicago"), "KXHIGHTNOLA": ("New Orleans", "America/Chicago"),
    "KXHIGHTOKC": ("Oklahoma City", "America/Chicago"), "KXHIGHTPHX": ("Phoenix", "America/Phoenix"), "KXHIGHTSAN": ("San Diego", "America/Los_Angeles"),
    "KXHIGHTSATX": ("San Antonio", "America/Chicago"), "KXHIGHTSDF": ("Louisville", "America/New_York"), "KXHIGHTSEA": ("Seattle", "America/Los_Angeles"),
    "KXHIGHTSFO": ("San Francisco", "America/Los_Angeles"), "KXHIGHTTTN": ("Trenton", "America/New_York"),
}
LOWS = {
    "KXLOWTATL": ("Atlanta", "America/New_York"), "KXLOWTAUS": ("Austin", "America/Chicago"), "KXLOWTBOS": ("Boston", "America/New_York"),
    "KXLOWTCHI": ("Chicago", "America/Chicago"), "KXLOWTDAL": ("Dallas", "America/Chicago"), "KXLOWTDC": ("Washington", "America/New_York"),
    "KXLOWTDEN": ("Denver", "America/Denver"), "KXLOWTEWR": ("Newark", "America/New_York"), "KXLOWTHOU": ("Houston", "America/Chicago"),
    "KXLOWTLAX": ("Los Angeles", "America/Los_Angeles"), "KXLOWTLV": ("Las Vegas", "America/Los_Angeles"), "KXLOWTMIA": ("Miami", "America/New_York"),
    "KXLOWTMIN": ("Minneapolis", "America/Chicago"), "KXLOWTNOLA": ("New Orleans", "America/Chicago"), "KXLOWTNYC": ("New York", "America/New_York"),
    "KXLOWTOKC": ("Oklahoma City", "America/Chicago"), "KXLOWTPHIL": ("Philadelphia", "America/New_York"), "KXLOWTPHX": ("Phoenix", "America/Phoenix"),
    "KXLOWTSAN": ("San Diego", "America/Los_Angeles"), "KXLOWTSATX": ("San Antonio", "America/Chicago"), "KXLOWTSDF": ("Louisville", "America/New_York"),
    "KXLOWTSEA": ("Seattle", "America/Los_Angeles"), "KXLOWTSFO": ("San Francisco", "America/Los_Angeles"), "KXLOWTTTN": ("Trenton", "America/New_York"),
}
ALL_SERIES = {**MAIN, **OTHER_HIGHS, **LOWS}

# Forecast point for the main cities (the NWS stations Kalshi settles on; Open-Meteo, display only).
STATIONS = {
    "KXHIGHNY": (40.7789, -73.9692, "America/New_York"), "KXHIGHCHI": (41.7868, -87.7522, "America/Chicago"),
    "KXHIGHMIA": (25.7959, -80.2870, "America/New_York"), "KXHIGHAUS": (30.1945, -97.6699, "America/Chicago"),
    "KXHIGHDEN": (39.8466, -104.6562, "America/Denver"), "KXHIGHLAX": (33.9382, -118.3866, "America/Los_Angeles"),
    "KXHIGHPHIL": (39.8721, -75.2411, "America/New_York"),
}

# group -> label, series, hours before close the price is taken. "main" is the rule; the rest are
# comparison groups (paper only, never pushed) so the Results page shows whether the edge is confined to it.
GROUPS = {
    "main": {"label": "Main rule", "short": "Main", "series": MAIN, "target_h": 32.0,
             "note": "7 original high-temp cities, day before ~5 PM local"},
    "evening": {"label": "Main cities, evening", "short": "Evening", "series": MAIN, "target_h": 26.5,
                "note": "same 7 cities, day before ~10:30 PM local"},
    "other_highs": {"label": "Other high-temp cities", "short": "Other highs", "series": OTHER_HIGHS, "target_h": 32.0,
                    "note": "17 newer high-temp cities, day before ~5 PM local"},
    "lows": {"label": "Low-temp markets", "short": "Lows", "series": LOWS, "target_h": 32.0,
             "note": "24 low-temp cities, day before ~5 PM local"},
}
WINDOW_H = 3.0            # a tick up to 3 h after the target still logs (the 15-minute clock can be late)
YES_BID_MIN, YES_BID_MAX = 0.01, 0.04
CONTRACTS = 25            # paper size per bet (owner Nov 26); fewer if the book shows less at the best price
FEE_RATE = 0.07           # Kalshi taker fee: ceil(0.07 x C x P x (1-P)) to the cent, per order
PUSH_HOUR_PT = 17         # daily summary push from 5:20 PM PT, after the last main city (Los Angeles) is checked
PUSH_MINUTE_PT = 20

# Backtest reference shown on the Results page (Oct 6 - Nov 25 2026, settlement inferred from final prices).
BACKTEST = {
    "main": {"trades": 645, "losses": 4, "win": 99.4, "price": 98.3, "cpc": 0.88, "ci": "+0.37 to +1.39"},
    "evening": {"trades": 437, "losses": 6, "win": 98.6, "price": 98.3, "cpc": 0.20, "ci": "-0.71 to +1.11"},
    "other_highs": {"trades": 1810, "losses": 30, "win": 98.3, "price": 98.3, "cpc": -0.16, "ci": "-0.65 to +0.33"},
    "lows": {"trades": 1871, "losses": 38, "win": 98.0, "price": 98.2, "cpc": -0.41, "ci": "-0.95 to +0.12"},
}
SITE = "https://investment-analytics3.github.io/nba-pregame"
