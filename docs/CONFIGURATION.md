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
| `APIFY_TOKENS` | – | One or more Apify API tokens, comma- or newline-separated (`TOKEN_1,TOKEN_2,TOKEN_3`). Whitespace and empty entries are ignored, duplicates removed |
| `APIFY_TOKEN` | – | Legacy single token. Used only when `APIFY_TOKENS` is empty, so old installations keep working unchanged |
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

## Multiple Apify tokens (rotation and failover)
**Configure.** In `.env` (and in the GitHub secret `APP_ENV_FILE`): `APIFY_TOKENS=TOKEN_1,TOKEN_2,TOKEN_3`. Commas or one token per line; spaces, quotes and empty entries are ignored and duplicates are removed. Leave `APIFY_TOKEN` blank, or keep it: it is a **fallback used only when `APIFY_TOKENS` is empty**, so an installation that only has `APIFY_TOKEN` works exactly as before. Every token is treated as a secret and masked in logs; messages and reports refer to "token #2", never the value. `python main.py show-config` prints the number of tokens (after cleaning and de-duplication, e.g. `Apify tokens configured = 3`) - never a token value, prefix or suffix. It makes no Apify call.

**Rotation.** Each new Actor run starts with the next token: run 1 uses A, run 2 B, run 3 C, run 4 A again. A token rejected with 401/403 is skipped for the rest of that execution; one that returned 429 is tried last. A single token simply is used every time.

**One token per run.** The token that started a run is used for everything about that run - polling, abort and dataset download - because tokens may belong to different Apify accounts and cannot see each other's runs. A run is never moved to another token.

**Failover.** If starting an Actor is refused with **401, 403 or 429**, no run was created, so the next token is tried (at most once per token per start). Anything ambiguous never triggers another start: a timeout, a connection error, a 5xx answer or an unreadable answer might mean the run exists. In that case the connector records the **maximum possible cost** for that Actor (existing fail-closed rule), stops that Actor and does not retry. If polling or the dataset download fails after a successful start, the Actor is **not** started again (the connector tries to abort that run with the same token).

**When all tokens fail.** If every token is refused (401/403/429) before any run exists, nothing was started and nothing is charged: the Actor is skipped, a message "no Apify token was accepted" appears in Source Statistics, and the rest of the pipeline continues. The monitoring alert for a failing source applies as usual.

**Spending is not multiplied.** `APIFY_APPROVED_MAX_USD_PER_RUN`, `APIFY_MAX_USD_PER_ACTOR`, `APIFY_MAX_RESULTS_<ACTOR>` and the timeout are shared by all tokens; switching token never resets the spending tracker. Dry Run never starts an Actor, whatever the number of tokens.

**Credits.** Tokens from the **same** Apify account share that account's monthly credits and usage allowance - extra tokens do **not** add credit. Rotation helps only when tokens belong to different accounts (each with its own allowance) or to survive a revoked token. Check the terms of each account you use.

**Updating `APP_ENV_FILE`.** Edit your local `.env` (add the `APIFY_TOKENS=` line), copy the whole file, then GitHub > repository > *Settings > Secrets and variables > Actions > Secrets > APP_ENV_FILE > Update* and paste it. Nothing else changes; the workflow already rebuilds `.env` from that secret and masks the values.
