# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `GOOGLE_SHEET_ID is empty` / `not valid Base64` | Fill `.env`; re-create the Base64 line (see CONFIGURATION.md). Make sure it is one line with no quotes broken |
| `Permission denied. Share the spreadsheet with ...@...iam.gserviceaccount.com` | Open the sheet > Share > add that e-mail as **Editor** |
| `Google Sheets API is not enabled` | Cloud console > APIs & Services > Library > enable *Google Sheets API* for the project that owns the service account |
| HTTP 429 / quota | The program retries with back-off; if it still fails, run again later |
| `Rows in 'All Jobs' were re-ordered ... nothing was written` | You sorted/filtered-and-edited the sheet during the run. Re-run. Tip: use a filter view, not a sort, on `All Jobs` |
| Gmail "rejected the login" | Use an App Password (needs 2-Step Verification), not your normal password |
| A source shows `failed` in Source Statistics | Blocking, changed endpoint or no internet. Other sources continue. Check *System Logs* |
| LinkedIn/Indeed return nothing on GitHub | Cloud IPs are often blocked; try `JOBSPY_SITES=naukri` or add proxies |
| Too many "Company Verification Required" | Fill the *Company Registry* tab (Classification, and Founded Year + Employee Count for startups) |
| Sheet is growing too big | Keep `STORE_OUT_OF_SCOPE_JOBS=false`; Google Sheets allows 10 million cells total |
| Apify did not run | All four gates must be open - see CONFIGURATION.md. `python main.py check` prints the allowed actors |
| Edited a threshold, nothing changed | Changes apply on the next run to all rows |
| Workflow: `Secret APP_ENV_FILE is not set` | Add the secret (DEPLOYMENT.md) |
| Many jobs in "Location Verification Required" | Remote/vague locations do not say they are open to India. Open the posting; if it is open to India, apply manually. Nothing is assumed |
| A job shows "UNVERIFIED bundled seed" | Its company classification comes from the unverified seed. Add the company to the *Company Registry* tab with an evidence URL, (strict is now the default; `COMPANY_SEED_MODE=flag` in an old `.env` overrides it) |
| Salary shows "per month ... CTC is not confirmed" | A monthly figure is not CTC; confirm the annual CTC on the employer page |


## AI problems

Start with `python main.py show-config` then `python main.py check-ai`.

| What you see | Meaning | Fix |
|---|---|---|
| `HTTP 404 ... model ... not found` | The model name does not exist for your key (old name such as `gemini-2.0-flash`, a typo, or a `models/` prefix) | Set `GEMINI_MODEL=gemini-3.8-flash` in `.env` **and** in the GitHub `APP_ENV_FILE` secret. Check the name in Google AI Studio |
| `GEMINI_MODEL` in the Actions log differs from your `.env` | The secret still holds the old file | Update `APP_ENV_FILE` (README section 17), re-run |
| `HTTP 503 ... high demand` | Google is busy. Not your fault | Nothing: it retries, then the next provider is used |
| `HTTP 429` | Free limit reached | Waits / switches provider automatically; lower `*_MAX_CALLS_PER_RUN` or add another provider |
| `auth: HTTP 400 API key not valid` / `401` / `403` | Wrong or missing key | Re-create the key; paste without quotes or spaces |
| `.env is not saved as UTF-8` | PowerShell `>` saved it as UTF-16 | Re-save in Notepad as UTF-8 (the loader also accepts UTF-16 and BOM) |
| `CLOUDFLARE_ACCOUNT_ID must be the 32-character account ID` | Wrong ID | Copy it from the Cloudflare dashboard |
| `quota` for Cloudflare | 10,000 free neurons/day used (resets 00:00 UTC) | Wait, or let the next provider take over |
| Nothing from AI at all | No key set, `AI_ENABLED=false`, or every provider failed | The rules-only result is still complete and correct |


## Alerts and monitoring

| What you see | Meaning | Fix |
|---|---|---|
| E-mail "Job system alert: ... no jobs in 3 runs in a row" | A source returned nothing 3 times (blocked, changed layout, or a bad board id) | `python main.py validate-targets`; remove/fix the board. Not mailed again for 3 days |
| "only N jobs today, usually about M (sudden drop)" | Volume fell below 30% of the recent median | Check the source in `Source Statistics`; often a temporary block |
| "every AI provider failed today" | All providers errored; rules still ran | `python main.py check-ai` |
| E-mail "Job system: daily run FAILED" | The workflow stopped before finishing | Open the link in the mail, read the red step, see this file |
| Change history | `System Logs`, component `change`: salary / experience / location / category / status changes of existing jobs | Informational |
| Which companies should I verify first? | `python main.py registry-queue` lists them by number of relevant jobs | Fill the `Company Registry` tab |

## Final safety patch
| Symptom | Fix |
|---|---|
| The 9:00 run does not start | By design: set repository variable `ENABLE_SCHEDULED_PRODUCTION` to `true` (docs/DEPLOYMENT.md). The run shows as skipped. |
| Dry run says "stopped: spreadsheet structure problem" (exit code 3) | A tab or header is missing. A dry run never repairs it. Run `python main.py check` (write-enabled) once, then repeat the dry run. |
| Scheduled run skipped even though I set the variable | The value must be exactly lowercase `true` with no spaces or quotes. `TRUE`, `True`, `true ` all keep it off by design. |
| Dry run made no AI calls | Intended. Set `DRY_RUN_ALLOW_AI=true` to allow them. |
| Many jobs now say Company Verification Required | Strict mode is the default. Verify the companies in the Company Registry tab, or check that your `.env`/`APP_ENV_FILE` has no old `COMPANY_SEED_MODE=flag`. |
| Function shows "(unverified)" | The AI suggested a function but the posting has only one supporting term; check the role yourself. |
| Apify did not run | Dry run, `APIFY_ENABLED` off, no token/budget, or the actor not enabled. See docs/CONFIGURATION.md. |

## Apify tokens
| Symptom | Fix |
|---|---|
| "no Apify token was accepted" in Source Statistics | Every token was refused (401 invalid, 403 no permission, 429 rate limit). Nothing was started or charged. Check the tokens in the Apify console and `APIFY_TOKENS` in `APP_ENV_FILE`. |
| Apify says "stopped after an error (no retry of paid runs)" | A timeout/server error made it unclear whether a run started; the maximum possible cost was counted and nothing was retried. Look at your Apify console for a run. |
| Several tokens but credit runs out as if there were one | Tokens of the same Apify account share its credit. |
| `show-config` prints `Apify tokens configured = 0` | `APIFY_TOKENS` and `APIFY_TOKEN` are both empty in the `.env` that was loaded. |
