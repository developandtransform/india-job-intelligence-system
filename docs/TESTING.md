# Testing

```powershell
pip install -r requirements-dev.txt
pytest -q
```
Result when this was written: **292 passed** (Python 3.13, Linux). `python -m compileall -q .` clean. `python main.py demo` runs.

## What the tests prove
- `test_experience.py`, `test_salary.py`, `test_rules.py`: every row of the experience, CTC, stipend and company tables (0-3 eligible; 2-4, 3-5, 3+, 4+, 5+, 0-5 excluded; Rs 8,000 / 9,999 / 10,000 / 15,000; unpaid; undisclosed; 8,000-12,000; Rs 7 / 8 / 6-10 LPA; startup younger than 5 years, 89 employees, unknown headcount; non-startup with 20 employees), "up to" maximums, fixed/variable/monthly not treated as CTC, titles never inferring eligibility, universal experience limit.
- `test_location.py`: Indian cities, hybrid, relocation, remote with/without India eligibility, foreign locations.
- `test_company_registry.py`: unverified seed is flagged (and ignored in strict mode), verified registry rows, unknown companies queued, source-supplied data.
- `test_dedup_history.py`, `test_pipeline.py`: dedup across sources and runs, different locations, source-URL preservation, reposts, closure rules, manual status/notes preservation, history never deleted, dry run, threshold changes.
- `test_scoring.py`: bounds, explanation, ordering, score never overrides a rule.
- `test_sheets_store_fake.py`, `test_sheets_retries.py`: diff-only writes, manual-column protection, row-shift abort, retry of 429/5xx, no retry of 400/401/403/404, bounded attempts (fake worksheet / fake API errors).
- `test_http_retries.py`: retries, bounded back-off, no retry of 4xx, POST never retried, Retry-After honoured and capped, jitter configured (local loopback server only).
- `test_email_and_urls.py`: HTML escaping, URL validation, SMTP failure handling with a fake SMTP.
- `test_config.py`: defaults, `.env` loading and override, missing credentials, invalid Base64, only one env file.
- `test_connectors.py`, `test_apify_gemini_mocked.py`: parsers on fixtures; Apify gating, result limits, budget, isolation; Gemini cache/retry/limits.
- `test_ai_gemini_provider.py`, `test_ai_router.py`, `test_ai_usage_reduction.py`, `test_stage5_reliability.py`: AI providers (success, 404, 429, 503, timeout, bad key, malformed JSON, quota, fail-over, cool-down, call limits, redaction), 310-job synthetic run (rules-only jobs trigger no AI, ambiguous jobs do, cache hits, content-change invalidation, all-providers-down), board discovery, monitoring/alert de-duplication, change log, registry queue, digest caps, workflow safety. **All with fakes/mocks - none is a live test.**
- `test_security_and_workflow.py`: workflow cron, manual dispatch, exactly one secret, no artifacts/commits, `.gitignore`, no key-like strings.

## What is NOT tested (be aware)
- **Any live network call**: JobSpy sites, ATS endpoints, career pages, Apify, Gemini. The authoring environment could not reach them.
- **Real Google Sheets API behaviour** (formatting requests, quota handling, dropdowns) and **real Gmail delivery**. The fakes mirror the logic, not Google.
- GitHub Actions execution.
Expect to fix small issues on your first real run. Always start with `python main.py check` then `python main.py run --dry-run`.

## Manual smoke test
`python main.py demo` runs the whole pipeline offline on 9 invented jobs and writes CSVs plus `digest_preview.html` to `demo_output/`.

## Running the whole suite (Windows PowerShell, local Python, GitHub Ubuntu)
```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```
All paths in the tests are calculated from the test file, so the project can live in any folder. Five tests in `tests/test_workflow_gate.py` run the real workflow shell scripts and need `bash` (Git for Windows, WSL, or any Linux runner); without it they are reported as *skipped*, never as passed.
