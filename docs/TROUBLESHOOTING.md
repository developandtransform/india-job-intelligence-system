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
| A job shows "UNVERIFIED bundled seed" | Its company classification comes from the unverified seed. Add the company to the *Company Registry* tab with an evidence URL, or set `COMPANY_SEED_MODE=strict` |
| Salary shows "per month ... CTC is not confirmed" | A monthly figure is not CTC; confirm the annual CTC on the employer page |
