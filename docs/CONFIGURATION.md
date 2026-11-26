# Configuration reference

There is **one** configuration file: `.env` in the project root (copy `.env.example`). Real environment variables override it. On GitHub the same content lives in the single secret `APP_ENV_FILE`; the workflow rebuilds `.env` from it, masks the values, and deletes it after the run. Only `config/settings.py` reads configuration.

`.env` is git-ignored. Never paste secrets into issues, commits or logs.

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_SHEET_ID` | – | The long id in the sheet URL (`/d/<ID>/edit`). **Required** |
| `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` | – | The service-account key file as one line of Base64. **Required** |
| `EMAIL_ENABLED` | true | Send the digest |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `EMAIL_RECIPIENT` | – | Gmail SMTP login (16-char App Password; spaces are stripped) and the recipient |
| `EMAIL_MAX_JOBS_PER_CATEGORY` | 15 | Cap per section; jobs cut by the cap appear the next day |
| `EMAIL_MAX_JOBS_TOTAL` | 40 | Cap for the whole digest. Higher-priority sections fill it first; the rest wait until tomorrow |
| `APIFY_ENABLED` | false | Master switch |
| `APIFY_TOKEN` | – | Apify API token |
| `APIFY_ENABLE_LINKEDIN/NAUKRI/IIMJOBS/GLASSDOOR/GOOGLE_JOBS/WORKDAY` | false | Per-actor switch |
| `APIFY_APPROVED_MAX_USD_PER_RUN` | 0 | Your explicit budget for one daily run, all actors together. 0 = nothing runs |
| `APIFY_MAX_USD_PER_ACTOR` | 0.50 | Ceiling for one actor (sent as `maxTotalChargeUsd`) |
| `APIFY_MAX_RESULTS_<NAME>` | 50-200 | Result limit per actor run (sent as `maxItems`) |
| `APIFY_RUN_TIMEOUT_SECONDS` | 600 | |
| `APIFY_MAX_QUERIES_PER_ACTOR` | 2 | Each query is a separate paid run |
| `APIFY_INPUT_JSON_<NAME>` | – | Single-line JSON merged over an actor's default input |
| `GEMINI_*`, `GROQ_*`, `CLOUDFLARE_*`, `OPENROUTER_*`, `CUSTOM_*`, `AI_*` | see [AI_PROVIDERS.md](AI_PROVIDERS.md) | Optional AI helpers. Default Gemini model is `gemini-3.8-flash`. AI only *locates* text; Python decides. `AI_ENABLED=false` switches all AI off |
| `ENABLE_JOBSPY`, `JOBSPY_SITES`, `JOBSPY_RESULTS_PER_QUERY`, `JOBSPY_HOURS_OLD`, `JOBSPY_MAX_QUERIES_PER_RUN`, `JOBSPY_LINKEDIN_FETCH_DESCRIPTION`, `JOBSPY_PROXIES` | see `.env.example` | JobSpy controls. 44 search queries rotate daily, 12 run per day |
| `ENABLE_ATS`, `ATS_BOARDS_EXTRA`, `ENABLE_CAREER_PAGES`, `CAREER_PAGE_URLS_EXTRA` | true | Direct sources |
| `MINIMUM_CTC_LPA` | 8 | Full-time floor |
| `MINIMUM_INTERNSHIP_STIPEND_INR_MONTHLY` | 10000 | Internship floor |
| `MAXIMUM_EXPERIENCE_YEARS` | 3 | Applies to every job |
| `STARTUP_MIN_AGE_YEARS` / `STARTUP_MIN_EMPLOYEES` | 5 / 90 | Startups only |
| `COMPANY_SEED_MODE` | **strict** | `strict` (default) = the UNVERIFIED bundled seed is ignored for eligibility, so unverified companies go to Company Verification Required; `flag` = use the seed but warn on each job (not recommended) |
| `STRONG_MATCH_MIN_SCORE` / `POSSIBLE_MATCH_MIN_SCORE` | 70 / 45 | Score cut-offs |
| `TIMEZONE`, `DAILY_RUN_TIME` | Asia/Kolkata, 09:00 | `DAILY_RUN_TIME` is informational; the schedule is the cron line in the workflow |
| `LOG_LEVEL`, `HTTP_TIMEOUT_SECONDS`, `HTTP_MAX_RETRIES`, `REQUEST_DELAY_SECONDS`, `SOURCE_TIME_BUDGET_SECONDS` | | Politeness and limits |
| `STALE_AFTER_DAYS` / `CLOSE_AFTER_MISSED_RUNS` | 30 / 2 | Lifecycle |
| `DRY_RUN` | false | Read-only run: reads the Sheet, fetches free sources, evaluates; no writes, no e-mail, no paid connectors, no AI |
| `DRY_RUN_ALLOW_AI` | false | Opt-in: allow AI requests during a dry run (they may use free-tier quota; results are still not saved) |
| `STORE_OUT_OF_SCOPE_JOBS` | false | true = also store non-India / unrelated-function jobs (the sheet grows fast) |

**Note:** `DRY_RUN` in `.env` is read by `config/settings.py`; `python main.py run --dry-run` also forces it.

## Getting the Base64 key (Windows PowerShell)
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\key.json"))
```
Paste the single output line after `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=`, then delete the downloaded JSON file (or keep it outside the project folder).

## Migrating to strict company mode
Older `.env` files may contain `COMPANY_SEED_MODE=flag`; an explicit value overrides the new default. Change that line to `COMPANY_SEED_MODE=strict` (or delete it) in your **local `.env`**, then paste the whole file again into the GitHub secret `APP_ENV_FILE`. Your credentials do not need to change. Why strict: the bundled seed is unverified general knowledge, so letting it decide startup age/size would let unverified data pass eligibility. Verify companies in the *Company Registry* tab (classification, founded year, employees, evidence URL) and they are used immediately.

## Apify spending protection
Apify runs only when ALL are true: not a dry run, `APIFY_ENABLED=true`, a token is set, an approved per-run budget > 0, and the specific actor is enabled. Per-run and per-actor spending caps are checked before each start and sent to Apify as limits. POST requests are never auto-retried, and after any error in an actor the connector stops (it records the maximum possible cost instead of trying again). Invalid values leave it disabled.
