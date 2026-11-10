# NBA analyst readouts, one file per day

Written by the owner's daily Claude scheduled task ("NBA analyst readouts", every morning before
the board): a Claude session reads the day's slate, and for each Qualified or Watch list game
(`scoring.tracked` in `data/slates/<date>.json`) searches published previews and predictions,
writes a short readout, saves `<date>.json` here, commits it and republishes the page
(`board.yml`, `kind=preview`, `send=false`). The Final and later boards pick it up automatically.
Shown at the bottom of each game's L2 page as information only; it never changes a tier or grade.

```json
{
  "date": "2026-11-10", "updated": "2026-11-10T09:45-08:00",
  "games": {
    "<ESPN event id from data/slates/<date>.json>": {
      "matchup": "MIL @ NY",
      "summary": "2-4 short sentences: who analysts expect to win and why, any split on the spread.",
      "pick": "NY to win (all sources); split on the 15.5 spread",
      "predicted_score": "NY 118, MIL 104 (one model)",
      "injuries": "MIL: Herro questionable (knee). NY: Hart questionable (ankle).",
      "sources": [{"site": "SI", "url": "https://..."}]
    }
  },
  "notes": "anything missing or uncertain"
}
```

Rules: only facts from articles actually opened; every source linked; no guessed scores; leave a
field out rather than invent it. Keep `summary` under ~60 words.
