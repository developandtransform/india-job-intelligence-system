# Deployment (GitHub Actions)

1. Create a **private** GitHub repository and push this folder (the `.env` file is ignored by `.gitignore`).
2. Fill in a local `.env`, run `python main.py check` until it prints `Google Sheets OK`, then `python main.py run --dry-run`.
3. In GitHub: *Settings > Secrets and variables > Actions > New repository secret*. Name `APP_ENV_FILE`, value = the **entire contents** of your `.env`.
4. *Actions > Daily job run > Run workflow*, leave `mode` = `dry-run` (the default).
5. The schedule `30 3 * * *` (03:30 UTC = 09:00 IST) does **not** run production until you add the repository variable `ENABLE_SCHEDULED_PRODUCTION` = `true` (see below).

Facts worth knowing
- GitHub scheduled runs can start several minutes late and are paused after 60 days of repository inactivity on public repos.
- The workflow has `permissions: contents: read`, never commits, never uploads artifacts, and removes `.env` at the end (`if: always()`).
- Secrets from forks are not exposed. Keep the repository private; the daily digest links to your sheet.
- JobSpy sources (LinkedIn, Indeed) may block GitHub's cloud IP addresses. The run continues with the other sources and records the failure in *Source Statistics*. This has **not** been observed here (untested); proxies via `JOBSPY_PROXIES` are the usual remedy.
- `tests.yml` runs `pytest` on every push. No workflow has been executed on GitHub by the author of this code; **no passing badge is claimed**.


## Reliability features of the daily run

- **Schedule**: `30 3 * * *` UTC = 09:00 IST (GitHub cron is always UTC). Dates inside the app use `TIMEZONE` (default Asia/Kolkata).
  GitHub can start scheduled runs several minutes late at busy times. This setting is unchanged from before; nothing new was switched on.
- **Concurrency**: one run at a time (`group: daily-jobs`, a second run waits, never cancels the first).
- **Timeout**: 45 minutes.
- **If the run fails**: GitHub e-mails the repository owner, and a step also runs `python main.py notify-failure`, which sends one short
  e-mail through your Gmail settings with a link to the log (if Gmail is configured).
- **Alerts for a quiet problem**: after each successful run, `Source Statistics` history is checked. You get **one** alert e-mail
  (separate from the digest) when a source returns no jobs 3 runs in a row, fails, errors, drops suddenly in volume, or all AI providers
  fail. The same issue is not mailed again for 3 days; every alert is recorded in `System Logs` (component `alert`).
- **Dry run** (default for manual runs): Actions tab > Daily job run > Run workflow > `mode` = `dry-run`. Reads the Sheet and fetches free sources; creates/changes **nothing** in the Sheet
  (no tabs, headers, jobs, AI cache or logs), sends **no** e-mail, runs **no** Apify actor and makes **no** AI request (unless `DRY_RUN_ALLOW_AI=true` is in your env file). Missing tabs/headers are reported and the run stops.
- **Secrets**: only `APP_ENV_FILE`; written to `.env` in the runner, secret-looking values masked, `.env` removed in an `always()` step.

## Scheduled production gate
A small `gate` job runs first. GitHub expression `==` ignores letter case, so the variable is not compared in an expression: it is passed raw into the gate's shell, which tests `[ "$ENABLE_SCHEDULED_PRODUCTION" = "true" ]` and outputs `true` or `false`. The production job `needs: gate` and runs when the event is a manual dispatch, or when the gate output is `true`. Missing, empty, `false`, `TRUE`, `True`, ` true`, `true ` or any other value = the scheduled run is skipped (the gate job itself still shows as a quick green step). The gate is a repository **variable** (not a secret) so it is easy to flip.
- **Keep it disabled:** do nothing, or leave the variable absent/`false`. Never set it until a manual production run has worked.
- **Enable later:** repository > *Settings > Secrets and variables > Actions > Variables > New repository variable*, name `ENABLE_SCHEDULED_PRODUCTION`, value `true`. To stop again, set `false` or delete it.
- **Manual production run:** Run workflow > `mode` = `production` (explicit choice).
- **Test workflow:** `tests.yml` is unchanged and runs on push.
