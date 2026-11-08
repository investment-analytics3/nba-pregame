# Scheduler setup

GitHub's built-in cron is best-effort (runs can be late or skipped), so an
outside clock starts the Scheduler workflow every 15 minutes and a watchdog
reports when ticks stop. Both are free. GitHub's own cron stays on as a backup.

## 1. GitHub token (lets cron-job.org start the Scheduler)

GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token

- Token name: `scheduler-tick`
- Expiration: 1 year (set a reminder to renew; cron-job.org also emails if it starts failing)
- Repository access: Only select repositories > `nba-pregame`
- Permissions > Repository permissions > **Actions: Read and write** (nothing else)
- Generate, then copy the token (shown once)

## 2. cron-job.org (the clock)

Create a cronjob:

- URL: `https://api.github.com/repos/investment-analytics3/nba-pregame/actions/workflows/scheduler.yml/dispatches`
- Schedule: every 15 minutes (custom: minutes 0,15,30,45; every hour, every day)
- Time zone: any (the Scheduler works in Pacific time itself)
- Advanced:
  - Request method: `POST`
  - Headers:
    - `Authorization: Bearer <token from step 1>`
    - `Accept: application/vnd.github+json`
    - `X-GitHub-Api-Version: 2022-11-28`
    - `Content-Type: application/json`
  - Request body: `{"ref":"main","inputs":{"source":"cron-job.org"}}`
- Notifications: notify on failure (after 2 failures) and when it recovers

Success is HTTP **204**. Use "Test run" to check.

## 3. healthchecks.io (the watchdog)

- Add a check named `NBA scheduler`
- Schedule: Simple, period **15 minutes**, grace **30 minutes**
- Integrations: email is on by default; ntfy can be added for phone alerts
- Copy the check's ping URL into the repo secret `HEALTHCHECK_URL`
  (Settings > Secrets and variables > Actions > New repository secret)

It alerts once when ticks stop for about 45 minutes and once when they resume.

## Checking it from a Claude session

```
gh api "repos/investment-analytics3/nba-pregame/actions/workflows/scheduler.yml/runs?per_page=5" \
  --jq '.workflow_runs[] | "\(.created_at) \(.event) \(.display_title) \(.conclusion)"'
```

A tick's log starts with `Started by: cron-job.org` (or `schedule` for the GitHub backup).
