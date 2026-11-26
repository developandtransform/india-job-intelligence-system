# Improvement report (AI hotfix, multi-provider, usage reduction, reliability)

Test status when written: **419 passed** (293 before the final safety patch), `compileall` clean, `python main.py demo` runs. Every AI, Sheets, Gmail and discovery
test uses fakes or mocks. **No live request was made to Gemini, Groq, Cloudflare, OpenRouter, Google Sheets, Gmail, GitHub or any job site.**

## Tested and working (offline, real code paths)
Rules, eligibility precedence, dedup, history, manual-status preservation, dry run writes nothing, digest caps/priority, alert
de-duplication logic, change-log logic, registry queue, `.env` loading (UTF-8 / BOM / UTF-16 / CRLF), secret redaction, workflow YAML
structure, `show-config`, `check-ai` command logic.

## Tested with mocked responses only (behaviour proven against our fakes, not the real services)
Gemini / Groq / Cloudflare / OpenRouter / custom request formats and error handling (success, 404, 429, 503, timeout, bad key, malformed JSON,
quota, `Retry-After`, JSON-mode failure); router fail-over, cool-downs and call limits; the 310-job synthetic run (70 AI questions, 0 on a
repeat run, 3 after 3 text edits, pipeline survives all providers failing); AI Cache tab read/write against a fake worksheet; board discovery
against a fake connector; alert e-mails against a fake mailer.

## Implemented but not live-tested
Real HTTP to the four providers (URLs and headers follow their documentation) · the "AI Cache" tab in the real Google Sheet ·
alert and failure e-mails through real Gmail · `notify-failure` step in GitHub Actions · `discover-boards` against real ATS endpoints ·
`registry-queue` against your Sheet · the new workflow steps.

## Not implemented (and why)
- Batching several jobs per AI request: small free models are not reliable enough with multi-item JSON; one question per job keeps each answer verifiable.
- Detecting a salary change when only a structured API field changes and the posting text stays identical.
- A dedicated "Change Log" tab: changes go to System Logs (component `change`) to avoid another tab.
- Pinning GitHub Actions to commit SHAs.
- A verified list of Indian ATS identifiers (needs live access; the helper produces candidates for you to verify).

## What changed
| Area | Change |
|---|---|
| Gemini 404 | Default `gemini-3.8-flash`; model name normalised (`models/` prefix, quotes); detailed log line per attempt (provider, model, HTTP status, Google message, keys redacted); retries only 429/5xx/timeouts with exponential backoff + jitter; no retry for bad key/model/request; `check-ai`; `show-config`; workflow no longer masks harmless values such as the model name |
| `.env` | BOM / UTF-16 safe reader with a friendly error |
| Providers | Groq, Gemini, Cloudflare Workers AI, OpenRouter (off), custom OpenAI-compatible (off); order configurable; each independently switchable; `AI_ENABLED=false` disables all |
| Router | Priority order, fail-over, permanent errors/quota switch a provider off for the run, long `Retry-After` = cool-down, 2 failures in a row = off for the run, validator-driven fail-over, per-provider and global limits, usage statistics |
| Usage reduction | Rules first; no AI for jobs already excluded or with thin text; persistent hash-keyed cache in a small "AI Cache" tab; failures never cached; measured statistics rows in Source Statistics |
| Monitoring | Alerts for failed/erroring sources, 3 zero-job runs, sudden volume drop, AI outage; one alert mail per issue per 3 days; failed-run e-mail |
| History | Edited postings (same source) are re-evaluated; field-level change log in System Logs |
| Registry | `registry-queue` ranks companies to verify by number of relevant jobs |
| Digest | Overall cap (`EMAIL_MAX_JOBS_TOTAL`), priority order kept, "Needs attention" block, waiting-counts overview |
| Eligibility rules | **Unchanged** |

## Known limitations
See README section 21 and `docs/AI_PROVIDERS.md`. Free AI limits are provider-controlled and can change without notice.

## Final safety patch
1. **Workflow gate**: scheduled production needs `ENABLE_SCHEDULED_PRODUCTION == 'true'`; manual `mode` defaults to `dry-run`. Because GitHub `==` is case-insensitive, a preliminary `gate` job compares the raw variable in shell (`[ "$ENABLE_SCHEDULED_PRODUCTION" = "true" ]`) and the production job `needs` its output. Verified offline by executing the real gate script and the Run step script with bash for: missing, empty, `false`, `FALSE`, `TRUE`, `True`, `true `, ` true`, and `true` (only the last permits it). Real GitHub evaluation: not tested.
2. **Read-only dry run**: `run_pipeline` no longer calls `store.prepare()` in a dry run; a new read-only `validate_structure()` reports missing tabs/headers and stops. The Sheets store has a read-only guard that blocks every write. Skipped operations are listed in the report. `DRY_RUN_ALLOW_AI` (default false) is the AI opt-in.
3. **Strict default** for `COMPANY_SEED_MODE`; tests cover startup age/employee rules, unknown data and that a seed is never verified evidence.
4. **Apify**: tests for dry run, disabled actors, missing approval, caps, no retry/fallback paid runs, invalid config. Found and fixed a gap: after an error the connector could try the next query (a possible second paid run); it now stops and records the maximum cost.
5. **AI function validation**: `function_evidence()` requires 2 distinct keywords of the suggested function; 1 = "(unverified)", lowest score, flag; 0 = rejected. Counters appear in the AI statistics row.

### Test classification
- Offline, real code paths: rules, eligibility, strict mode, dry-run guard, function validation, workflow YAML.
- Mocked services: Sheets (recording fake), Apify, all AI providers, Gmail.
- Implemented, not live-tested: everything that talks to Google Sheets, Gmail, AI providers, Apify, job sites and GitHub Actions.
- Not implemented: automatic applications (deliberately), scraping circumvention.
- Existing tests adapted (not removed): the test helper pins `company_seed_mode="flag"` for rule tests that use seed companies; the config-default test expects `strict`; one dry-run AI test opts in; one AI function fixture gained two supporting terms; the workflow-input test checks `mode`.

## Portability and safety corrections (after independent inspection)
- **Hardcoded path removed** from `test_dry_run_safety.py` (now relative to the test file). The whole suite was searched for other absolute paths (none left); file reads in tests are explicitly UTF-8 so Windows code pages cannot break them.
- **Case-sensitive gate** (see item 1 above). The five tests that execute the workflow shell scripts need `bash` (present on GitHub Ubuntu runners, Git for Windows and WSL) and are skipped, not failed, when it is missing.
- **Complete Dry Run structure validation**: required headers are now checked for All Jobs, Company Registry, Source Statistics, System Logs and every generated view. Extra user columns are allowed; missing tabs, empty or blank header rows and missing columns are reported per tab; nothing is modified. The optional AI Cache tab is checked only when it exists and AI is in use, and a missing cache tab never blocks a run.

## Multiple Apify tokens
`APIFY_TOKENS` (comma/newline separated, cleaned, de-duplicated) with `APIFY_TOKEN` as the fallback; all are in `secret_values()`. `TokenPool` rotates round-robin per run and is thread-safe. Failover only on 401/403/429 (proof that no run exists), at most one attempt per token; timeouts, connection errors, 5xx and unreadable answers never start another run and record the maximum cost (unchanged fail-closed rule). A run keeps its token for polling, abort and dataset. The spending tracker, caps, result limits and dry-run guards are untouched. Tested with mocks only (`tests/test_apify_multi_token.py`, 36 tests); **no real Apify call was made**, so real Apify behaviour for these status codes is implemented but not live-tested.
