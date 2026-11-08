# NBA Pregame Dashboard

Daily pregame board for an NBA moneyline system: pulls free data, applies the six
qualification rules and the 39-point hero score, and emails an HTML page with
a phone alert. Everything runs on GitHub Actions at no cost; no AI is used in
the daily runs.

## What's built so far

| Piece | Status |
|---|---|
| Data sources confirmed from GitHub's servers | Done |
| Data engine: box scores, metrics, ranks, power rank, slate | Done |
| Scoring: six rules, hero score | Next |
| Board: HTML page, email, phone alert, schedule | After scoring |

## How the data works

- **`data/team_games.csv`** - one row per team per game: the box-score totals the
  formulas need. **`data/player_games.csv`** - minutes, points and starts per player
  (used to decide who is a "key" player).
- **`data/metrics.json`** - season and last-3 values and 1-30 ranks for eFG%,
  opponent eFG%, offensive and defensive efficiency, and net rating.
- **`data/power.json`** - consensus power rank from our own net-rating rank, ESPN BPI
  and NBA.com's weekly rankings (half weight; dropped and flagged "split" when it
  disagrees by more than 5 spots or a top/middle/bottom-10 bucket).
- **`data/slates/<date>.json`** - everything for one day's games: tip time, spread
  and favorite, records, back-to-backs, metrics, power ranks, last-3 game logs
  with opponent power rank, and injuries with a key-player flag.

Formulas: eFG% = (FGM + 0.5 x 3PM) / FGA; possessions = FGA - OREB + TOV + 0.44 x FTA
(averaged with the opponent's); efficiency = points per 100 possessions. Ranks match
NBA.com's closely (checked Nov 2026: net-rating ranks within 2 spots, 0.3 on average);
values run about 2 points lower than NBA.com because NBA.com counts possessions from
play-by-play.

## Workflows (Actions tab)

| Workflow | When | What |
|---|---|---|
| Overnight data update | 3 AM Pacific daily, or by hand | Catches up box scores, recomputes metrics and power ranks, builds today's slate, saves to `data/` |
| Check data sources | By hand | Confirms every free source still answers |
| Test phone alert | By hand | Sends a test ntfy alert |
| Fetch samples (developer) | By hand | Saves raw source responses to the `dev-samples` branch |

## Secrets (Settings > Secrets and variables > Actions)

| Name | Used for |
|---|---|
| `NTFY_TOPIC` | Phone alerts (set) |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | Emailing the board (needed for the board step) |
| `ODDS_API_KEY` | Optional backup spread source |

## Sources

ESPN public JSON (scores, box scores, spreads via DraftKings, injuries, BPI; unofficial),
the official NBA injury report PDFs, and NBA.com weekly power rankings.
