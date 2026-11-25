# NHL analyst readouts, one file per day (Eastern date)

Written by the owner's daily Claude scheduled task "NHL analyst readouts": a Claude session reads the day's
board (`data/nhl/boards/<date>.json`), and for every game that is tracked (Qualified or Watch list) OR scores over
20 (`scoring.readout_ok`), searches published previews and predictions, writes a short readout, saves
`<date>.json` here, commits only this file and dispatches `nhl.yml` with force_build=true, send=false.
Shown at the bottom of each game's L2 page as information only; it never changes a tier.

## Format v2 (owner Nov 24: longer summary, tables for picks, scores and injuries)

```json
{
  "date": "2026-11-25", "updated": "2026-11-24T22:15-08:00",
  "games": {
    "<ESPN event id>": {
      "matchup": "MIL @ NY",
      "fav": "NY", "dog": "MIL",
      "summary": "4-6 sentences, about 80-140 words: who the analysts expect to win and why; recent form and the key matchup they point to; injuries or rest that matter; any split or contrarian view. For a game that isn't tracked, start with why it's off the list.",
      "picks": [
        {"source": "SI Betting", "side": "fav", "bet": "NY -15.5", "price": "NY -1200 (92%)", "note": "one short reason, under ~15 words"},
        {"source": "Action Network", "side": "dog", "bet": "MIL +15.5", "note": "..."}
      ],
      "scores": [{"source": "Predictem", "fav": 118, "dog": 104}],
      "injuries": {
        "fav": [{"player": "Jalen Brunson", "status": "Questionable", "note": "ankle; game-time decision (Nov 24 report)"}],
        "dog": [],
        "note": "optional: where the statuses came from, or 'none reported' context"
      },
      "goalies": {"fav": {"name": "Igor Shesterkin", "status": "Confirmed"}, "dog": {"name": "...", "status": "Expected"}},
      "sources": [{"site": "SI Betting", "url": "https://..."}]
    }
  },
  "notes": "anything missing or uncertain"
}
```

- `fav` / `dog` = the BOARD's favorite and underdog abbreviations (from the slate/context), so every table lines up.
- `picks`: one row per source that makes a pick. `side` = the team that source expects to WIN ("fav" or "dog"); if a
  source only picks the spread or total, set `side` to the team its spread pick backs and say so in `bet`. `bet` = their
  recommended bet as written (spread, moneyline, total or prop); `price` = moneyline or win probability if the article
  gives one; `note` = their main reason. The page counts the tally (e.g. "4 for NY, 1 for MIL") from `side`.
- `scores`: only predicted final scores an article actually gives (numbers for the board's favorite and underdog).
- `injuries`: a list per team (`fav`, `dog`) of {player, status, note}; empty list = none reported in the sources.
  Status as written (Out, Doubtful, Questionable, Probable, Day-to-day). Put key players first.
- `goalies` (NHL only): each team's starting goalie and "Confirmed" / "Expected" / "Unknown" as the source says.
- Old files may still have the v1 text fields `pick`, `predicted_score` and `injuries` (a string); the page shows them as text.

Rules: only facts from articles actually opened; every source linked; no guessed scores, picks or statuses; leave a field
or row out rather than invent it.
