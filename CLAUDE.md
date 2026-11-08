# NBA Pregame Dashboard — project guide for Claude

Owner: the account holder (GitHub user `investment-analytics3`). Keep this repo
anonymous: no personal names, emails or locations in code, docs, data or commits.

## What it is

A free, fully automated pregame board for a moneyline NBA system. Python on GitHub
Actions pulls free data, scores every game (six pass/fail rules + a 40-point
Analysis Score), publishes an interactive page on GitHub Pages, emails it and sends
a phone alert. No AI runs in the daily pipeline; Claude is only used to change code.

- Board (latest): https://investment-analytics3.github.io/nba-pregame/
- Results tracker: https://investment-analytics3.github.io/nba-pregame/results.html
- Design spec (Claude Doc): https://claude.ai/artifact/TQV7dXo9KQM8aEmEEh8v8c
- Original visual mockup (Claude Design): https://claude.ai/artifact/6NEsvvN1QmSv4QMxC2hCyu

## Layout

| Path | Purpose |
|---|---|
| `nba/espn.py` | ESPN public JSON: scoreboard, odds (DraftKings), box scores, injuries, BPI, closing lines (`pickcenter`) |
| `nba/injury_report.py` | Official NBA injury report PDF (ak-static.cms.nba.com), parsed by column x-position |
| `nba/metrics.py` | eFG%, opp eFG%, off/def efficiency, net rating from box scores; season + last-3; ranks 1-30 |
| `nba/power.py` | Power-rank consensus: own net-rating rank + ESPN BPI (daily) + NBA.com weekly (half weight) |
| `nba/scoring.py` | Six qualification rules and the 40-point Analysis Score; pill colors |
| `nba/board.py` | Page data, rule-based written summaries, snapshots, "what changed", rendering |
| `nba/results.py` | Grades sent boards into `data/results.json`; CSV export (2 rows per game) |
| `nba/notify.py` | Gmail SMTP (app password) and ntfy.sh phone alerts |
| `scripts/update_data.py` | 3 AM catch-up of box scores, metrics, power ranks |
| `scripts/build_slate.py` | Builds + scores a day's slate (`build(day, retro=True)` rebuilds a past day from pre-tip data) |
| `scripts/board_decide.py` | When to send: board 12 PM weekdays / 10 AM weekends, Final 90 min before first tip |
| `scripts/run_board.py` | Publishes `docs/index.html` + `docs/boards/<date>-<kind>.html`, emails, alerts, logs snapshot |
| `scripts/update_results.py` | Grades games, writes `docs/results.html` and `docs/results.csv` |
| `scripts/rebuild_days.py` | Rebuilds unsent past days as "rebuilt" boards |
| `scripts/scheduler.py` | The 15-minute tick's decisions: nightly (catch-up), board/final, live |
| `docs/live/engine.js` | Live grading engine (runs, answers, control, closers, A+/A/B/C, gates). ONE file used by the Live page (browser) and the tracker/backtest (Node) |
| `docs/live/index.html` | Live L2 page (`live/?g=<id>`): grades in the browser from ESPN (CORS open); Kalshi comes from `live.json` |
| `docs/live/context.json` | Today's tracked games + closers (+ season pts/min), written by `scripts/live_context.py` (called by run_board) |
| `scripts/live_tracker.js` | Live tracker (live.yml): polls ESPN + Kalshi every 45 s, max 2 alerts/game (end of 3rd; first BET), force-pushes `live.json` to the `live-data` branch, writes `data/live/<date>.json` |
| `scripts/live_backtest.js` | Replays the season through the engine at checkpoints → `data/live/backtest.json`; drafts `data/closers.json` (keeps entries not marked `"source":"draft"`) |
| `templates/` | `board.html.j2` (interactive L1/L2 page, live chips, Pregame/Live tabs), `email.html.j2`, `results.html.j2` |
| `data/` | Committed state: `team_games.csv`, `player_games.csv`, `metrics.json`, `power.json`, `slates/`, `board_log.json`, `results.json` |
| `tests/` | `python -m unittest discover -s tests -v` (rules, schedule, grading, scheduler) and `node --test tests/engine.test.js` (live engine) |

## Workflows (Actions)

| Workflow file | Trigger | Notes |
|---|---|---|
| `scheduler.yml` | every 15 min from cron-job.org (workflow_dispatch, input `source`) + GitHub cron backup | the only automatic trigger: nightly update (from 3 AM PT, catch-up, marker `nightly` in `data/state.json`), board/Final (`board_decide`), starts `live.yml`; pings healthchecks.io. Input `dry_run=true` decides only. Setup: `SCHEDULER.md` |
| `update-data.yml` | manual | catch-up, grading, slate; commits `data/` and `docs/` |
| `board.yml` | manual | inputs: `kind` (preview/board/final/auto), `send` (true/false) |
| `rebuild-days.yml` | manual, input `days` | rebuild + grade unsent past days |
| `live.yml` | dispatched by the Scheduler (or manual: `send`, `once`) | live tracker; concurrency `nba-live`; up to ~5.5 h, the Scheduler restarts it |
| `live-backtest.yml` | manual | backtest + closers draft |
| `probe-live.yml` | manual | raw ESPN live / Kalshi samples + CORS headers → `dev-live-samples` branch |
| `check-sources.yml` | manual | confirms every data source answers from GitHub's servers |
| `fetch-samples.yml` | manual | saves raw source responses to the `dev-samples` branch (read them with `git fetch origin dev-samples`) |
| `test-email.yml`, `test-alert.yml` | manual | delivery checks |

All data-writing workflows share `concurrency: nba-data`. The bot commits to `main`
often: always `git pull --rebase origin main` before pushing.

### Operating from a Claude session

- Start a run: `gh api -X POST repos/investment-analytics3/nba-pregame/actions/workflows/<file>.yml/dispatches -f ref=main [-f "inputs[kind]=preview" -F "inputs[send]=false"]`
- Latest run of a workflow: `gh api "repos/investment-analytics3/nba-pregame/actions/workflows/<file>.yml/runs?per_page=1" --jq '.workflow_runs[0] | "\(.id) \(.status) \(.conclusion)"'`
- Scheduler ticks: `gh api "repos/investment-analytics3/nba-pregame/actions/workflows/scheduler.yml/runs?per_page=5"`. GitHub's own cron
  never fired after the account rename + history rewrite, which is why cron-job.org is the primary clock.
- Test the Scheduler without sending anything: dispatch `scheduler.yml` with `-F "inputs[dry_run]=true"`.
- Logs: `gh run view <id> -R investment-analytics3/nba-pregame --log`
- Artifact downloads are blocked from Claude's workspace; use the `dev-samples` branch pattern instead.
- ESPN, NBA.com and ntfy.sh are NOT reachable from Claude's workspace; test network code by running a workflow.
- When testing, never email or alert the owner unintentionally: use `board.yml` with `send=false`.
- Commit identity: `git -c user.name="investment-analytics3" -c user.email="investment-analytics3@users.noreply.github.com" commit ...`

## Secrets (Settings > Secrets and variables > Actions)

`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (Gmail app password), `NTFY_TOPIC` (phone alerts),
`HEALTHCHECK_URL` (healthchecks.io ping URL for the Scheduler), optional `ODDS_API_KEY`. Never print, log or commit their values.

## Rules and decisions (keep in sync with the design spec)

- Favorite = spread favorite (ESPN favorite flags; spread number is unsigned). Pick'em: moneyline decides.
- Qualification (all must pass): (1) favorite season top 10 in eFG%, Off Eff, Opp eFG% or Def Eff;
  (2) also last-3 top 10 in that same metric; (3) underdog not last-3 top 10 in any of the four;
  (4) underdog not 3-0 in last 3; (5) favorite has no key player Out/Doubtful (Questionable = yellow
  warning only); (6) injury info available for the favorite. Underdog injuries are information only.
- Key player = started at least half of the team's games, or top 5 on the team in minutes per game.
- Analysis Score (max 40): favorite metrics 16 (season+last 3; top 5 = 2, top 10 = 1), underdog
  metrics 16 (bottom 5 = 2, bottom 10 = 1), net rating 2, last-3 form 2 (3-0 = 2, 2-1 = 1),
  power rank 1 (fav top 10 and dog bottom 10), home 1 (none at neutral sites), health 1, rest 1
  (favorite not on a back-to-back). Bands: green 28-40, yellow 18-27, red 0-17.
- Power-rank "split" (amber) only when NBA.com moves a team across a top-10/bottom-10 line.
- Underdog pill colors are flipped (red = strong underdog, deep red pulsing = top 5).
- Results are graded from the last board sent before tip (Final > board > rebuilt).
- Metrics are box-score based (TeamRankings-style possessions), validated against NBA.com:
  ranks match closely; values run ~2 points lower than NBA.com's play-by-play ratings.
- Not used: TeamRankings (terms unclear), stats.nba.com (blocks cloud servers). BPI has no history.
- Live mode (full table in the design spec "Live mode" section): tracks above-the-line games only; BET only
  4th 10:00-4:00 for an A/A+ team that is LEADING (trailing A = comeback signal, not a bet); LEAN = same in the
  3rd / before 10:00; under 4:00 and OT read only. Alerts: phone only, end of 3rd (always) + first BET signal.
  End-of-3rd push: if the pregame favorite leads by 10+ it is its own signal ("<fav> (fav) up N", high priority)
  with the season record (`fav10` in data/live/backtest.json), the favorite's defense (opp eFG% / Def Eff: last-3
  ranks + tonight ranked vs season averages) and the Kalshi price; otherwise "wait for BET signal" / "likely no bet".
  The tracker logs Kalshi at that moment so the backtest can later test whether the price leaves an edge.
  Kalshi: public API (`api.elections.kalshi.com/trade-api/v2`, series `KXNBAGAME`, event
  `KXNBAGAME-26NOV08PHICHI`); it refuses requests that carry a browser `Origin` header, so only the tracker
  calls it. Price check never changes the grade.

## Privacy

- Every page carries `<meta name="robots" content="noindex, ...">`; keep it on all templates.
- No personal names/emails anywhere. History was rewritten once to remove them.

## Open items

- Live mode built Nov 8. Owner confirmed Nov 8: trailing-A = no bet; opponent never ran = B max; 6:00-4:00 is a bet window.
  Kalshi is read-only; the system never places bets (Claude can't execute trades).
- Closers: `data/closers.json` is a draft until the owner marks teams `"source":"yours"`.
- Live grades are logged (`data/live/<date>.json`) but not yet shown on the Results page.
- Revisit the Analysis Score bands after 4-6 weeks of real boards (green is rarely reached early season).
