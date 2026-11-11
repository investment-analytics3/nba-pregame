# College basketball analyst readouts, one file per game day

Written by the owner's Claude scheduled task "College hoops analyst readouts" (every morning at 6:13 AM PT;
it does nothing on days without a tracked game). A Claude session reads `data/cbb/context.json`, and for
each game that tips today (Eastern date `day_et`) and is tracked (`tracked`: Qualified or Watch list),
searches published previews, predictions and injury news, writes a short readout, saves `<date>.json`
here, commits only that file and rebuilds the board (`cbb.yml`, `force_build=true`, `send=false`).
Qualified games first, then the Watch list by score (highest first), at most 12 games a day.
ESPN publishes no college injury lists, so the `injuries` line is the board's only injury check: name
any key player reported out or doubtful. Shown at the bottom of each game's page (Pregame tab) as
information only; it never changes a tier.

```json
{
  "date": "2026-11-14", "updated": "2026-11-14T06:20-08:00",
  "games": {
    "<ESPN event id = \"id\" in data/cbb/context.json>": {
      "matchup": "ORU @ TLSA",
      "summary": "2-4 short sentences: who analysts expect to win and why, any split on the spread.",
      "pick": "Favorite to win (all sources); split on the spread",
      "predicted_score": "78-62 (one model)",
      "injuries": "Statuses for both teams, as reported (or: none reported)",
      "sources": [{"site": "CBS Sports", "url": "https://..."}]
    }
  },
  "notes": "anything missing or uncertain"
}
```

Rules: only facts from articles actually opened; every source linked; no guessed scores; leave a
field out rather than invent it. Keep `summary` under ~60 words.
