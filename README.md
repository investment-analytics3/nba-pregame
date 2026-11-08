# NBA Pregame Dashboard

Daily pregame board for an NBA moneyline system: pulls free data, applies the six
qualification rules and the 39-point hero score, and emails an HTML page.

This first piece is the **data source check**. It runs on GitHub's servers (the
same ones the daily job will use) and confirms each free source answers with
the fields the dashboard needs, before we build anything on top of them.

## What it checks

| Source | Used for | Required |
|---|---|---|
| ESPN teams list | All 30 teams and their IDs | yes |
| ESPN scoreboard | Today's slate, tip times, back-to-backs, records | yes |
| ESPN scoreboard odds | Point spread (decides the favorite) | yes |
| ESPN box scores | eFG%, opponent eFG%, offensive/defensive efficiency, net rating, starters | yes |
| NBA official injury report (PDF) | Injuries and status | yes |
| ESPN injuries | Injury backup | no |
| ESPN BPI | Power rank input | no |
| NBA.com power rankings | Weekly power rank input | no |
| The Odds API | Spread backup (only if you add a key) | no |
| stats.nba.com | Not used; recorded to confirm it blocks cloud servers | no |

## One-time setup (about 10 minutes)

1. **Create a repository.** On github.com, click **New repository**, name it
   `nba-pregame`, choose **Public** (unlimited free Actions minutes) or
   **Private** (free monthly minutes, plenty for this), and click **Create**.
2. **Upload the files.** On the new repo page, click **uploading an existing
   file**, then drag in the *contents* of this folder (`scripts/`, `.github/`,
   `README.md`, `.gitignore`) and click **Commit changes**.
   - If the `.github` folder doesn't upload by dragging (hidden folders are
     sometimes skipped on a Mac), click **Add file > Create new file**, type
     `.github/workflows/check-sources.yml` as the name, paste the file's
     contents, and commit.
3. **Optional: add The Odds API key.** Sign up free at the-odds-api.com, then in
   the repo go to **Settings > Secrets and variables > Actions > New repository
   secret**, name it `ODDS_API_KEY`, paste the key, save.
4. **Run it.** Go to the **Actions** tab, pick **Check data sources**, click
   **Run workflow**. It takes under a minute.
5. **Read the result.** Click the finished run: the summary table shows PASS,
   WARN or FAIL for each source. Copy that table (or download the
   `source-check-report` file at the bottom) and paste it back to Claude.

Run it on a **game day, in the afternoon Pacific**, so there is a slate with
spreads and a fresh injury report to test against.

## Run it on your own computer instead

Needs Python 3.10 or newer, nothing else:

```bash
python3 scripts/check_sources.py
```

The report is written to `reports/source_check.md`.
