# Acceptance audit

Audit of the first delivered version (`job-intelligence-system.zip`) against the original brief and the quality-audit brief.
Method: the code was read and **probed with real inputs** (not judged from the README). Environment: Claude.ai sandbox, no access to job sites,
Google, Gmail, Gemini or Apify, so every "live" integration is at best *implemented but not tested*.

Baseline reproduced before any change: `python -m compileall -q .` OK, `pytest -q` = 52 passed, `python main.py demo` OK.

Status key: PASS · PARTIAL (partially implemented) · NOT IMPL · UNTESTED (implemented but not tested live) · MANUAL (needs manual configuration) · FAILED.
The **Fix status** column is filled at the end of the work (section 3).

## 1. The 19 points you asked me to verify

| # | Point | Verdict | Evidence | Severity |
|---|---|---|---|---|
| 1 | Source coverage narrower than requested | **Confirmed.** Implemented: JobSpy (Naukri, Indeed, LinkedIn; Glassdoor/Google optional), 11 ATS types, JSON-LD career pages, 6 optional Apify actors. Not implemented: Hirist, Foundit, Shine, Instahyre, TimesJobs; IIMJobs only via paid Apify | `connectors/`, `SUPPORTED_ATS` | Medium |
| 2 | ATS config has only a few example employers | **Confirmed.** 5 boards, all foreign employers (Stripe, Spotify, Ramp, Visa, NVIDIA), none checked | `config/targets.yaml` | Medium |
| 3 | Career-page config empty | **Confirmed.** `career_pages: []` | `config/targets.yaml` | Medium |
| 4 | JobSpy needs extra configuration | **Confirmed.** Works without keys, but LinkedIn/Indeed commonly block cloud IPs; `JOBSPY_PROXIES` exists; never run live | `jobspy_connector.py` | Medium (UNTESTED) |
| 5 | Hirist etc. not implemented | **Confirmed.** Not in JobSpy and no public API; only listed as an aggregator domain | grep | Medium (documentation) |
| 6 | ATS documented but unsupported | **Not confirmed.** All 11 documented ATS types have a branch in `fetch_board`; unsupported ones (SuccessFactors, iCIMS, Jobvite, Zoho, Rippling, Eightfold, Pinpoint) are listed as unsupported | `ats_connector.py:378-420` | PASS |
| 7 | IIMJobs must stay optional | **PASS.** Only reachable through Apify actor `iimjobs`, off by default, failure isolated | `apify_connector.py`, `settings.py` | - |
| 8 | Company seed unverified | **Confirmed.** 234 entries have only name/aliases/classification. No evidence URL, date or status | `config/company_seed.yaml` | High |
| 9 | Unverified company data must not silently decide eligibility | **Confirmed FAILURE.** A seed hit returns *Eligible / "Not a startup"* with evidence text "Bundled seed registry (verify)", indistinguishable in the status columns from a verified registry row | `company_verification.py` `evaluate_company` | **High** |
| 10 | README incomplete | **Confirmed.** 25 sections but not the requested structure (no Technology stack, Limitations, Cost breakdown, Roadmap, Disclaimer sections) | `README.md` | Medium |
| 11 | README clickable TOC | **Confirmed missing** | `README.md` | Medium |
| 12 | Folder tree matches files | **PARTIAL.** README tree was hand-written and abbreviated | `README.md` | Low |
| 13 | Test results independently reproduced | **Done:** 52 passed reproduced in a fresh copy. But 1 test (`test_experience_limit`, "Freshers welcome") asserted nothing and several required cases were missing | `tests/` | Medium |
| 14 | History survives scheduled updates | **PASS (mock-tested).** Master is only appended/diff-updated; jobs never deleted | `google_sheets.save_master`, `lifecycle.py`, tests | UNTESTED live |
| 15 | Manual status/notes not overwritten | **PASS (mock-tested)**, plus abort when rows shifted | same | UNTESTED live |
| 16 | One root `.env` | **PASS.** Only `config/settings.py` reads configuration; no other `.env*` file but `.env.example` | `settings.py`, `find` | - |
| 17 | One `APP_ENV_FILE` secret | **PASS** (workflow reads only that secret) | `daily_jobs.yml` | UNTESTED on GitHub |
| 18 | Apify disabled by default | **PASS** (4 gates, tested) | `settings.enabled_apify_actors` | - |
| 19 | 9:00 AM IST schedule | **PASS** (`30 3 * * *` = 09:00 IST, `workflow_dispatch` present). GitHub may start scheduled runs late | `daily_jobs.yml` | UNTESTED on GitHub |

## 2. Defects found by probing the code (not in your list)

| ID | Sev | Problem (reproduced) | Where |
|---|---|---|---|
| E1 | **High** | A description containing only `0-5 years` (or `0 to 5 years`, `0 - 3 yrs`) with no word "experience" nearby was classified **Eligible, 0-0 years** because `FRESHER_RE` contains the pattern `0 ?-? ?\d`. `0-5` must be Excluded/Verification | `experience_filter.py` `FRESHER_RE` |
| E2 | **High** | Job *titles* such as "Management Trainee"/"Trainee" and every Internship without a stated experience requirement were auto-marked eligible (0 years). The brief forbids inferring eligibility from Junior/Associate/Executive/Trainee titles | `experience_filter.py` `TRAINEE_TITLE_RE` |
| S1 | **High** | `Up to 12 LPA` (a *maximum*) was treated as a **minimum** of 12 and marked Eligible; same for stipends ("up to 15,000/month") and for sources that supply only a maximum | `salary_filter.py` `_range_verdict` |
| S2 | Medium | An unlabelled *monthly* figure (`Rs 70,000 per month`) was converted x12 and accepted as CTC. Monthly pay is a different thing from CTC | `salary_filter.py` |
| L1 | **High** | A remote job with no stated India eligibility was marked India-eligible ("Yes") merely because the search was India-scoped (JobSpy sets `hint_country=India` for LinkedIn/Indeed/Google too). Brief: never assume worldwide remote is open to India | `geo_india.py:190` |
| L2 | Medium | Ambiguous locations were silently dropped as out of scope (not stored). Brief: ambiguous locations require verification | `eligibility.py` |
| H1 | **High** | `HttpClient` auto-retried **POST** on 429/5xx. A retried Apify "start run" POST after a 5xx could start a second **paid** run | `core/http.py` |
| H2 | Low | No retry jitter | `core/http.py` |
| G1 | Medium | Gemini: no cache, no bounded retry for transient errors | `ai_assist.py` |
| U1 | Medium | Apply/job URLs were not validated at ingestion (only the e-mail HTML skipped non-http links). `javascript:`/`file:` URLs could reach the sheet | `normalizer.py` |
| C2 | Low | Source company-type label matched `"mnc" in ctype` (would also match e.g. "non-MNC") | `company_verification.py` |
| R1 | Medium | `Company Registry` tab has no verification status column; seed has no evidence/date/status fields | schema |
| T1 | Medium | Missing tests for items listed in section 7 of the brief (stipend edge cases, 89 employees, config loading, missing credentials, HTTP/Sheets retries, scoring, URL validation, Apify result limits, ...) | `tests/` |
| D1 | Low | MIT attribution for adapted GetAnything patterns was only in the README | docs |

## 3. Resolution (after the work)

Final results, all executed in the Claude.ai sandbox: `python -m compileall -q .` clean; `pytest -q` **191 passed**; `python main.py demo` OK. Nothing was run against live services.

| ID | Fix status | What changed |
|---|---|---|
| #8, #9, R1 | **FIXED** | Seed entries are explicitly *Unverified* (file header, `verification_status`, evidence/date fields). A seed match is reported as "Not a startup (unverified)" with a visible per-job warning; `COMPANY_SEED_MODE=strict` ignores the seed for eligibility. Registry tab gained *Verification Status* (existing sheets get the column added automatically). **No seed entry could actually be verified** (no live access; see limitation below) |
| E1 | **FIXED** | Removed numeric patterns from `FRESHER_RE`; bare `0-5 years` is no longer eligible |
| E2 | **FIXED** | Title/internship inference removed; internships with no stated experience go to Experience Verification |
| (new) | **FIXED** | Found while testing: an internship duration ("Internship 3 months") next to the word "experience" was read as an experience requirement; durations of internships/contracts/probation are now ignored |
| S1 | **FIXED** | "Up to X" is a maximum (verification, or Excluded if below the threshold); applies to stipends and to source-supplied max-only figures |
| S2 | **FIXED** | Monthly/weekly full-time figures are never treated as CTC |
| (new) | **FIXED** | Found while testing: a salary window cut in the middle of "remote" produced the word "ote" and mis-labelled CTC as OTE; windows no longer cut words. "Variable pay up to 4 LPA" was labelled CTC because of "LPA"; explicit CTC wording now outranks "variable", which outranks the weak "LPA" signal |
| L1, L2 | **FIXED** | India-scoped search no longer proves a remote role is open to India; ambiguous locations are stored under the new *Location Verification Required* category (tab + e-mail section) instead of being dropped. This adds a 15th tab to the original 13 + Company Registry |
| H1 | **FIXED** | POST is never auto-retried (no duplicate paid Apify runs) |
| H2 | **FIXED** | Jittered, capped back-off; `Retry-After` honoured up to 60 s; `urllib3>=2` pinned |
| G1 | **FIXED** | Gemini: per-prompt cache, one bounded retry on transient errors, none on permanent ones, self-disables after 3 failures |
| U1 | **FIXED** | `safe_url()` at ingestion and in the digest; non-http(s) links dropped |
| C2 | **FIXED** | `MNC` label matching is word-based and excludes "non-MNC" |
| T1 | **FIXED** | 52 -> 191 tests; every row of your test-case table is covered |
| D1 | **FIXED** | `THIRD_PARTY_NOTICES.md` |
| #10, #11, #12 | **FIXED** | README rebuilt in the requested 25-section structure with a clickable table of contents and a generated (accurate) folder tree |
| #1, #5 | **NOT FIXED (documented)** | Hirist, Foundit, Shine, Instahyre, TimesJobs remain unsupported; documented as such |
| #2, #3 | **NOT FIXED (documented)** | Still 5 example boards and no career pages. Verifying real Indian employer identifiers requires live requests to job sites, which this audit was forbidden to make, and identifiers must not be invented. `python main.py validate-targets` lets you check your own additions |
| #4 | **NEEDS MANUAL CONFIGURATION** | JobSpy may need proxies on GitHub's IPs; unknowable without a live run |
| #8 (evidence) | **NOT DONE** | Recording founding year / headcount / evidence URL for each of the 234 seed companies was not possible without live research. Fill the Company Registry tab, or add `evidence_url` + `verified_date` to the seed file |
| #13-#19 | **PASS** | Re-verified by tests (history, manual preservation, one `.env`, one secret, Apify off, cron `30 3 * * *`, manual dispatch) |

## 4. Known remaining weaknesses

- All live integrations are untested (Medium/High until you run `check` and `--dry-run`).
- ATS parsers other than Greenhouse/Lever have no fixture test.
- Apify actor output adapters were written from actor pages, not from real output.
- Experience/salary extraction is regex-based; odd phrasings go to verification rather than being guessed.
- `Excluded` rows for in-scope jobs are stored (history) and appear in *Rejected and Closed*.
