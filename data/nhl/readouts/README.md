# NHL analyst readouts, one file per day (Eastern date)

Written by the owner's daily Claude scheduled task "NHL analyst readouts": a Claude session reads the day's
board (`data/nhl/boards/<date>.json`), and for every game that is tracked (Qualified or Watch list) OR scores over
20 (`scoring.readout_ok`), searches published previews and predictions, writes a short readout, saves
`<date>.json` here, commits only this file and dispatches `nhl.yml` with force_build=true, send=false.
Shown at the bottom of each game's L2 page as information only; it never changes a tier.

Same format and rules as `data/readouts/README.md` (NBA): keys are ESPN event ids; fields `matchup`, `summary`
(under ~60 words), `pick`, `predicted_score`, `injuries` (include the starting goalies when an article names
them), `sources` [{site, url}]; top-level `date`, `updated`, `notes`. Only facts from articles actually opened;
every source linked; no guessed scores; leave a field out rather than invent it.
