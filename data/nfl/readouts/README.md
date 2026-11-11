# NFL analyst readouts, one file per game day

Written by the owner's Claude scheduled task "Football analyst readouts" (every morning at 5:43 AM PT; it
does nothing on days without a tracked game). Saturdays it also writes Sunday's NFL readouts a day early; the game-day run then refreshes that file
(updates remaining games, adds newly tracked ones). Files are named by game day, not by the day written. A Claude session reads `data/nfl/context.json`, and for each game that
kicks off today (Eastern date `day_et`) and is tracked (`tracked`: Qualified or Watch list), searches
published previews and predictions, writes a short readout, saves `<date>.json` here, commits only
that file and rebuilds the board (`nfl.yml`, `force_build=true`, `send=false`). Shown at the bottom of
each game's page (Pregame tab) as information only; it never changes a tier or call.

```json
{
  "date": "2026-11-14", "updated": "2026-11-14T05:50-08:00",
  "games": {
    "<ESPN event id = \"id\" in data/nfl/context.json>": {
      "matchup": "KC @ ATL",
      "summary": "2-4 short sentences: who analysts expect to win and why, any split on the spread.",
      "pick": "Favorite to win (all sources); split on the spread",
      "predicted_score": "27-17 (one model)",
      "injuries": "Statuses for both teams, as reported",
      "sources": [{"site": "CBS Sports", "url": "https://..."}]
    }
  },
  "notes": "anything missing or uncertain"
}
```

Rules: only facts from articles actually opened; every source linked; no guessed scores; leave a
field out rather than invent it. Keep `summary` under ~60 words.
