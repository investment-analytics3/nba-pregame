# Game Boards (NBA + NFL) — project guide for Claude

Owner: the account holder (GitHub user `investment-analytics3`). Keep this repo
anonymous: no personal names, emails or locations in code, docs, data or commits.

## What it is

A free, fully automated pregame board for a moneyline NBA system. Python on GitHub
Actions pulls free data, scores every game (six pass/fail rules + a 40-point
Analysis Score), publishes an interactive page on GitHub Pages, emails it and sends
a phone alert. No AI runs in the daily pipeline; Claude is only used to change code.

- Landing page (L0, both sports): https://investment-analytics3.github.io/nba-pregame/  (`docs/index.html`, static)
- NBA board (latest): https://investment-analytics3.github.io/nba-pregame/nba/
- NFL board: https://investment-analytics3.github.io/nba-pregame/nfl/ · checkpoint log: .../nfl/live.html
- NFL design spec (Claude Doc): https://claude.ai/artifact/A4Q6Nana5hMZYbJCSVNix2
- College football spec (Claude Doc, DRAFT, not built yet): https://claude.ai/artifact/GjAffxfWQBwayGjJ4s4gGc · backtest: `scripts/cfb_backtest.py` → `data/cfb/backtest.json` (+ `_games.csv`); early entry (end Q1 / half / end Q3 vs in-game WP, Nov 10: no edge vs a fair price; next = log real Kalshi prices): `scripts/cfb_early_backtest.py` → `data/cfb/early_backtest.json`; data = sportsdataverse `espn_cfb_pbp` release (reachable from the workspace) + cfbfastR-data schedules
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
| `nba/scoring.py` | Qualification tiers (Qualified / Watch list / No track), the 40-point Analysis Score, pill colors, `LIVE_TIERS` |
| `nba/board.py` | Page data, rule-based written summaries, snapshots, "what changed", rendering |
| `nba/results.py` | Grades sent boards into `data/results.json`; CSV export (2 rows per game) |
| `nba/notify.py` | Gmail SMTP (app password) and ntfy.sh phone alerts |
| `scripts/update_data.py` | 3 AM catch-up of box scores, metrics, power ranks |
| `scripts/build_slate.py` | Builds + scores a day's slate (`build(day, retro=True)` rebuilds a past day from pre-tip data) |
| `scripts/board_decide.py` | When to send: board 12 PM weekdays / 10 AM weekends, Final 90 min before first tip |
| `scripts/run_board.py` | Publishes `docs/nba/index.html` + `docs/boards/<date>-<kind>.html`, emails, alerts, logs snapshot (the site root is the landing page) |
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

## NFL (separate pipeline: own folder, workflows and concurrency group; never touches NBA files)

| Path | Purpose |
|---|---|
| `nfl/sources.py` | nflverse play-by-play / snap counts (GitHub releases) and schedule (`games.csv`: ESPN ids, rest, neutral, lines); team-code maps ESPN↔nflverse↔Kalshi |
| `nfl/metrics.py` | Four core metrics (off EPA/play, off success, def EPA allowed, def success allowed), season + last 3, ranks 1-32, as of a week (no hindsight); official net EPA; SHADOW adjusted edge; records, QBs, key starters |
| `nfl/scoring.py` | Tiers (Qualified / Watch list / No track) + gates, 40-point hero score, official stat edge (one +0.35 bar), flags, Under gate, pregame call |
| `nfl/power.py` | Power rankings: ESPN FPI (daily, FITT powerindex) + NFL.com weekly article (`rankedCards` JSON), consensus = average re-ranked 1-32; opponent power gate |
| `nfl/espn.py`, `nfl/kalshi.py` | ESPN week scoreboard, DK open/current lines, game-day injuries, FPI; Kalshi `KXNFLGAME` prices |
| `nfl/board.py` | Builds the week's board → `docs/nfl/index.html`, `docs/nfl/summary.json` (landing), `data/nfl/context.json` (logger), `data/nfl/boards/<season>-w<week>.json` |
| `nfl/early_path.py` | Early blowout path grading (halftime, end of 3rd) — same rules as the 2024-25 backtest |
| `scripts/nfl_scheduler.py` | What's due each tick: build (hourly on game days, daily otherwise), alert (first game-day build), grade (daily), live |
| `scripts/nfl_build.py` | Build + publish; `--alert` sends one ntfy push (game days only) |
| `scripts/nfl_live.py` | Checkpoint logger (shadow): halftime, end of 3rd, 4th 10:00, 4th 6:00 — score, lead, real Kalshi price, ESPN win %, live engine grade; publishes live Kalshi prices to the `nfl-live-data` branch (`prices.json`) for the Live page; phone alerts for tracked games (end of 3rd + first bet signal) |
| `docs/nfl/engine.js` | NFL live engine (runs/answers, control 3 of 5, momentum + fake comeback, QB closer, windows, A+/A/B/C + gates, early-path read). ONE file for the Live page (browser) and the logger (`scripts/nfl_engine_cli.js`, Node). Tests: `node --test tests/nfl_engine.test.js` (fixture: CIN@ATL 2026 W9) |
| `docs/nfl/ep.json`, `scripts/nfl_ep_table.py` | Expected points by down × distance × field position (nflverse 2024-25). ESPN has no EPA: the engine estimates EPA = EP after − EP before (TD 7, opp TD −7, safety −2); success = EPA > 0. Matches nflverse within ~0.05 EPA/play per team-half |
| `docs/nfl/game.html` | NFL Live page (`nfl/game.html?g=<espn id>`; list without `g`): grades in the browser from ESPN, refreshes every 30 s |
| `scripts/nfl_grade.py` | Next day: grades logged checkpoints from nflverse (control 3 of 5, QB edge, defense) + results |
| `scripts/nfl_backtest.py` | The Nov 8 2024-25 backtest (one-off; not scheduled) |
| `templates/nfl_board.html.j2`, `docs/nfl/live.html`, `docs/site.css` | NFL pages + shared stylesheet (landing, NFL) |

Workflows: `nfl.yml` (the NFL tick, dispatched by the NBA Scheduler's last step every 15 min; concurrency `nfl-data`;
inputs `force_build`, `send`), `nfl-live.yml` (logger; concurrency `nfl-live`), `nfl-probe.yml` (raw ESPN/Kalshi samples →
`dev-nfl-samples` branch). Requirements: `requirements-nfl.txt` (pandas, pyarrow). nflverse is reachable from Claude's
workspace; ESPN and Kalshi are not (test with workflows).

NFL rules (spec "NFL Stat Edge v2", decisions Nov 8 2026):
- Official edge unchanged: net EPA = off EPA/play − def EPA/play allowed (all scrimmage plays, nflverse = RBSDM method);
  gap must clear +0.35 for EVERY favorite, whatever the spread (owner Nov 9: no large-favorite bar, no max spread).
  nflverse replaces Muffed as the source (owner approved Nov 9). QB reset (regular QB out or <100 dropbacks) caps at Lean.
- Tiers (owner Nov 10, same as NBA; nfl/scoring.py `qualification(fav, dog, score)`): Qualified = hero score 28+ (route A)
  OR last-3 mismatch (route B: last 3 fav top 10 in >= 3 of 4 AND dog ranks 23-32 in >= 3 of 4; season fav top 15 in all
  four AND dog top 10 in none). Watch list = 18-27. No track = the rest. Gates on every tier: injury (fav key starter
  Out/Doubtful, or no injury info; owner Nov 10: injured reserve / PUP / NFI / suspension = `scoring.LONG_TERM`, never
  blocks or costs health points, listed as `key_long_term`; a regular QB there still triggers the QB reset) and power (underdog top 10 in ANY power ranking: consensus, ESPN FPI, NFL.com; the
  fail note and a red flag name it; no power data = passes). The old six checks are `context` only. `tracked` =
  Qualified + Watch list (Live page, checkpoint log); `alerts` = Qualified only (phone pushes, `nfl_live.push_ok`).
  Call: Qualified -> Edge clears / Track, edge short / Lean (QB cap); Watch list -> "Watch list"; else No track.
  Board (Nov 10): L1 sections Qualified / Watch list / Did not qualify / Played; cards open an L2 view (`#g-<id>`) with
  Pregame/Live tabs (Live = `nfl/game.html?g=<id>`), "How it qualifies", score, pills, details. Board pills (Nov 10, as NBA): L2 WW green / split yellow / LL red and PR
  (consensus) 1-10 green / 11-22 yellow / 23-32 red for the favorite; flipped for the underdog. Hero score 40: fav 16 (top 5 = 2, 6-10 = 1), dog 16 (outside top 10 = 1,
  ranks 23-32 = 2), health 4 (QB active 2, other key starter 1, interior OL 1; 0 without injury info), home 1, rest 1 (fav off
  a bye, or dog on a short week while the fav isn't), form 2 (fav last 2). Bands red 0-17, yellow 18-27, green 28-40.
- Adjusted edge = SHADOW only (10-90% WP plays, opponent-adjusted, 0.6/0.4, recency 60/40 last 4); weights not approved.
- Early path (shadow until the owner reviews real Kalshi prices): halftime through end of 3rd (end of Q1 dropped);
  fav up 10+, both defensive metrics top 10 season AND last 3, control 3 of 5, QB edge. Also log "control + QB, any defense".
- Live grading (spec "Live grading", built Nov 8; shown on the Live page, logged, alerts below): run = two straight
  scores for 10+ unanswered, or take the lead and score again; answer = any score after it; strong answer (TD or 10+) needed for A.
  Control = 3 of 5 from the 3rd quarter on (est. EPA/play, success, 3rd-down rate, sacks, avg start field pos). QB +2 top-10
  EPA/db playing well, +1 starter, 0 backup or 2+ turnovers; unclear (another passer 2+ of last 8 dropbacks) caps B in the window.
  Windows: half scout, end of 3rd lean, 4th 15:00-10:00 primary, 10:00-6:00 confirm, 6:00-5:00 last, under 5:00 read only.
  Gates: no answered run = B max; trailing A = no bet; one-score + no QB edge + trading runs = C; 2+ 4th-quarter turnovers = down one;
  lock = answered + lead > 8 + control. Phone alerts ON (owner Nov 9), tracked games only, max two per game: end of 3rd
  (engine read + Kalshi) and the first BET signal in 4th 15:00-5:00 (`nfl_live.py` `end3_alert`/`bet_check`; `entry.alerts`
  stops repeats across restarts; `nfl-live.yml` input `send=false` or `--no-alert` silences tests). Early path stays unalerted.
- Owner confirmed Nov 9: rest point only when the favorite has more rest than a short-week underdog; no injury info = 0 health.
- Expert ATS picks (CBS, Bleacher Report, Covers, SI, Yahoo): no feed and no scraping in the pipeline. The owner's Claude
  scheduled task "NFL expert picks" (Thu + Sat 9:43 AM PT) reads the week's articles, writes `data/nfl/votes/<season>-w<WW>.json`
  (format: `data/nfl/votes/README.md`) and dispatches `nfl.yml`. The board shows them as information only.

## Workflows (Actions)

| Workflow file | Trigger | Notes |
|---|---|---|
| `scheduler.yml` | every 15 min from cron-job.org (workflow_dispatch, input `source`) + GitHub cron backup | the only automatic trigger: nightly update (from 3 AM PT, catch-up, marker `nightly` in `data/state.json`), board/Final (`board_decide`), starts `live.yml`; dispatches `nfl.yml` (continue-on-error); pings healthchecks.io. Input `dry_run=true` decides only. Setup: `SCHEDULER.md` |
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
- Tiers (since Nov 9, nba/scoring.py): Qualified = Analysis Score 28+ (route A) OR last-3 mismatch (route B, revised
  Nov 9: last 3 favorite top 10 in >= 3 of 4 AND underdog bottom 10 = rank 21-30 in >= 3 of 4; season favorite top 15
  in all four AND underdog top 10 in none). Watch list = score 18-27. No track = the rest. Opponent power gate on every
  tier (Nov 10): underdog top 10 in ANY power ranking (consensus `rank`, `own_net`, `bpi`, `nba_com`) -> No track, fail
  note names the ranking(s). Injury gate on every tier: favorite key player Out/Doubtful or no injury report ->
  No track (Questionable = yellow warning; underdog injuries info only). The earlier six checks are kept as
  `context` only; `qualifiers` still lists the favorite's top-10 (season+last 3) metrics. `qualified` stays the
  boolean every consumer reads; `tier`, `route`, `tracked` are new. `LIVE_TIERS` decides what the live tracker,
  L1 live chips and Live page follow (Qualified + Watch list since Nov 9).
- L1: three sections (Qualified green, Watch list amber, Did not qualify red line). B2B Yes = red, No = green.
  Power rank + L3 record are pills (`prPill`/`formPill`): favorite top 10 / 3-0 green, 11-20 / 2-1 yellow, else red;
  underdog flipped (top 10 / 3-0 red, 11-20 / 2-1 yellow, 21-30 / 1-2 / 0-3 green).
- Analyst readout (Nov 10): L2 bottom panel for tracked games (`readoutPanel`), from `data/readouts/<date>.json`
  (format + rules: `data/readouts/README.md`; loaded by `board.load_readouts`). Written by the owner's Claude scheduled
  task "NBA analyst readouts" (daily 9:41 AM PT), which commits only that file and dispatches `board.yml` preview with
  send=false. Information only.
- Board timing: `board_decide.decide(..., day)` puts the board time on the slate's ET date (fixed Nov 10: from 9 PM PT
  the next day's board used to go out the night before).
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
- Live mode (full table in the design spec "Live mode" section): tracks `LIVE_TIERS` games (Qualified + Watch list); BET only
  4th 10:00-4:00 for an A/A+ team that is LEADING (trailing A = comeback signal, not a bet); LEAN = same in the
  3rd / before 10:00; under 4:00 and OT read only. Alerts: phone only, end of 3rd (always) + first BET signal.
  End-of-3rd push = the 7-item checklist verdict (engine `checklist()`): lead 10+ at end of 3rd; opp eFG% top 10
  last 3 AND tonight; Def Eff top 10 last 3 AND tonight; a closer active (played in Q3, <5 fouls); underdog has no
  unanswered run; underdog not hot from 3 (40%+ on 15+ 3PA game, or 45%+ on 6+ last 12 min); favorite not on a
  back-to-back (`fav_b2b` in context). "7 of 7, parlay-ready" (high priority) needs a single-leg Kalshi price <= 94c;
  else "N of 7, skip: reasons". Parlay ceiling 80c for two legs; the tracker quotes the combined price when another
  tracked game was ready and is still on. Logged in alerts.end3 (checklist + Kalshi); backtest `checklist` section
  tracks win rate by score. The Live page card locks to the end-of-3rd snapshot (Kalshi stays live).
  Kalshi: public API (`api.elections.kalshi.com/trade-api/v2`, series `KXNBAGAME`, event
  `KXNBAGAME-26NOV08PHICHI`); it refuses requests that carry a browser `Origin` header, so only the tracker
  calls it. Price check never changes the grade.

## Privacy

- Every page carries `<meta name="robots" content="noindex, ...">`; keep it on all templates.
- No personal names/emails anywhere. History was rewritten once to remove them, and the 32 Actions runs whose
  logs showed the pre-rename username were deleted Nov 9 (owner approved).

## Open items

- Live mode built Nov 8. Owner confirmed Nov 8: trailing-A = no bet; opponent never ran = B max; 6:00-4:00 is a bet window.
  Kalshi is read-only; the system never places bets (Claude can't execute trades).
- Closers: `data/closers.json` is a draft until the owner marks teams `"source":"yours"`.
- Live grades are logged (`data/live/<date>.json`) but not yet shown on the Results page.
- Revisit the Analysis Score bands after 4-6 weeks of real boards (green is rarely reached early season).
