# Published NHL power rankings, one file per update

Written by the owner's Claude scheduled task "NHL power rankings" (weekly, Tuesday 10:47 AM PT; fired once by hand Nov 13). It reads
the newest ESPN and NHL.com NHL power rankings articles, saves `<YYYY-MM-DD>.json` here (the run date),
commits only that file and rebuilds the board (`nhl.yml`, `force_build=true`, `send=false`).
The board averages these with our own daily rank (`nhl/power.py`); a list older than 10 days is left out.

```json
{
  "date": "2026-11-17", "updated": "2026-11-17T06:50-08:00",
  "sources": {
    "espn":    {"date": "2026-11-17", "url": "https://www.espn.com/nhl/story/_/id/...", "ranks": {"CAR": 1, "COL": 2, "...": 3}},
    "nhl_com": {"date": "2026-11-14", "url": "https://www.nhl.com/news/...", "ranks": {"CAR": 1, "...": 2}}
  },
  "notes": "anything missing or uncertain"
}
```

Rules: all 32 teams per source, NHL team codes (ANA BOS BUF CAR CBJ CGY CHI COL DAL DET EDM FLA LAK MIN MTL NJD
NSH NYI NYR OTT PHI PIT SEA SJS STL TBL TOR UTA VAN VGK WPG WSH), each source's own publication date, ranks
copied from the article actually opened. If a source hasn't published a new list, keep its last one (copy it
with its original date) or leave it out; never guess a rank.
