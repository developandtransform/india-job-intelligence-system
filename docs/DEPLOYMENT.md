# Deployment (GitHub Actions)

1. Create a **private** GitHub repository and push this folder (the `.env` file is ignored by `.gitignore`).
2. Fill in a local `.env`, run `python main.py check` until it prints `Google Sheets OK`, then `python main.py run --dry-run`.
3. In GitHub: *Settings > Secrets and variables > Actions > New repository secret*. Name `APP_ENV_FILE`, value = the **entire contents** of your `.env`.
4. *Actions > Daily job run > Run workflow* (tick "Dry run" the first time).
5. The schedule `30 3 * * *` (03:30 UTC = 09:00 IST) then runs every day.

Facts worth knowing
- GitHub scheduled runs can start several minutes late and are paused after 60 days of repository inactivity on public repos.
- The workflow has `permissions: contents: read`, never commits, never uploads artifacts, and removes `.env` at the end (`if: always()`).
- Secrets from forks are not exposed. Keep the repository private; the daily digest links to your sheet.
- JobSpy sources (LinkedIn, Indeed) may block GitHub's cloud IP addresses. The run continues with the other sources and records the failure in *Source Statistics*. This has **not** been observed here (untested); proxies via `JOBSPY_PROXIES` are the usual remedy.
- `tests.yml` runs `pytest` on every push. No workflow has been executed on GitHub by the author of this code; **no passing badge is claimed**.
