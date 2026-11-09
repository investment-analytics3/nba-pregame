# Expert picks (against the spread), one file per week

Written by the owner's weekly Claude scheduled task ("NFL expert picks", Thursday and Saturday mornings):
a Claude session searches this week's published pick articles (CBS Sports, Bleacher Report, Covers,
SI, Yahoo; others only if one is missing), tallies each expert's ATS pick per game, saves
`<season>-w<WW>.json` here, commits it and starts the NFL tick (`nfl.yml`, `force_build=true`) so the
board shows it. The board treats votes as information only.

```json
{
  "season": 2026, "week": 10, "updated": "2026-11-12T09:50-08:00",
  "games": {
    "<ESPN event id from data/nfl/context.json>": {
      "matchup": "WSH @ NYG", "favorite": "NYG",
      "fav_votes": 9, "dog_votes": 8,
      "sources": [{"site": "CBS Sports", "url": "https://...", "fav": 5, "dog": 3}],
      "notes": "SI article gives moneyline picks only; not counted"
    }
  },
  "missing_sources": ["Yahoo: Week 10 article not published yet"]
}
```
