# Repository Audit

This document records the audit of the five open-source repositories that informed this project. The audit was
done by reading the **actual code**, dependency manifests and licence files in the supplied archives, not only the READMEs.

> **Environment note.** The audit environment could not reach job sites (egress policy returned HTTP 403), so claims about
> *live* behaviour of third-party endpoints come from reading the code and each repo's own comments, not from running them.

## 1. Summary table

| # | Repository | Language | Licence (file in archive) | What it is | Verdict for this project |
|---|---|---|---|---|---|
| 1 | [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) v1.5.0 | Python 3.10+ | **MIT** (Cullen Watson) | Multi-board scraper library (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google, Bayt, **Naukri**, BDJobs, HelloWork, SEEK) | **Reuse as a pip dependency** (not vendored) |
| 2 | [udaninn/career-page-job-monitor](https://github.com/udaninn/career-page-job-monitor) | Python (Apify Actors) | **No licence file** | Six ATS adapters + Workday actor + delta tracker, one Apify Actor per ATS | **Reference only**: no code copied |
| 3 | [GetAnything-1/ats-jobs-scraper](https://github.com/GetAnything-1/ats-jobs-scraper) | Python (Apify Actor) | **MIT** (Get Anything) | Greenhouse, Lever, Ashby, SmartRecruiters, Workday in ~420 lines | **Adapted** (endpoint patterns, Workday paging traps), credited |
| 4 | [sukhhdipsingh/ats-job-scraper](https://github.com/sukhhdipsingh/ats-job-scraper) v1.4.0 | Node.js (Apify Actor) | **No licence file** (`package.json` has no `license` field) | 14 ATS adapters, monitor mode, ghost-job signals | **Reference only**: no code copied; ideas re-implemented in Python |
| 5 | [ShivamKumarPTU/JobBot](https://github.com/ShivamKumarPTU/JobBot) | Python/FastAPI + React | **No licence file** | Scraper + AI scorer + **auto-apply** + SQLite | **Reference only** for ideas; **auto-apply deliberately excluded** |

**Licence consequence.** A repository without a licence is "all rights reserved" by default. Repos 2, 4 and 5 therefore
contributed *ideas and publicly documented endpoint facts only*. Every line of this project's code was written fresh.
Public API endpoints themselves (for example `boards-api.greenhouse.io/v1/boards/{slug}/jobs`) are facts documented by the
ATS vendors, not the intellectual property of any of these repos.

## 2. Per-repository evaluation

### 2.1 JobSpy

| Criterion | Finding |
|---|---|
| Sources | LinkedIn, Indeed, Glassdoor, ZipRecruiter (US/CA), Google Jobs, Bayt, **Naukri (India)**, BDJobs, HelloWork, SEEK. Indian-relevant: Naukri, LinkedIn, Indeed (`country_indeed="india"`), Glassdoor (`www.glassdoor.co.in`), Google |
| Methodology | Per-site HTTP scrapers using `requests` and `curl_cffi` (browser TLS impersonation). Naukri uses the `/jobapi/v3/search` JSON API; Indeed uses its mobile GraphQL API; LinkedIn parses guest HTML; Google parses the search page |
| API availability | Python function `scrape_jobs()`, CLI and MCP server. Returns a pandas DataFrame |
| Extracted fields | id, site, job_url, job_url_direct, title, company, location(city/state/country), date_posted, job_type, salary (interval/min/max/currency), is_remote, job_level, description, company_* (industry, size, revenue), skills, **experience_range (Naukri)**, work_from_home_type |
| Salary | Structured on Naukri/Indeed/Glassdoor. LinkedIn parses the page salary tag; a regex-guess fallback exists but is gated to USA. Not reliable for India beyond Naukri |
| Experience | **Naukri only** (`experience_range`). Not extracted elsewhere, so we parse descriptions ourselves |
| Location | Parsed to city/state/country |
| Description | Markdown/HTML/plain. LinkedIn needs `linkedin_fetch_description=True` (one extra request per job) |
| Direct apply URL | `job_url_direct` where available (Naukri `applyRedirectUrl`, Indeed, LinkedIn external apply) |
| Pagination | Offset/page loops until `results_wanted`; Naukri stops at the last page; most boards cap near 1,000 per search |
| Duplicate detection | None across sites. Only within-run |
| Historical monitoring | None (stateless) |
| Dependencies | requests, beautifulsoup4, pandas, pydantic, curl_cffi, markdownify |
| Maintenance | Active (v1.5.0, CI and PyPI publish workflows present) |
| Reliability | Indeed: best, little rate limiting (per README). LinkedIn: 429 after bursts, proxies recommended. Glassdoor: limits above ~2 req/s. Datacenter IPs (including GitHub runners) are more likely to be blocked |
| Reusability | **High.** `scrape_jobs()` is clean; `jobs.attrs["errors"]` reports per-site failures, which makes per-connector error isolation easy |
| Limitations | No history, no experience parsing, scraping breaks when sites change, ToS grey areas, Google Jobs only accepts one search string |

### 2.2 udaninn/career-page-job-monitor

| Criterion | Finding |
|---|---|
| Sources | Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters (+ a separate Workday actor) |
| Methodology | Public ATS JSON APIs via `httpx`; ATS auto-detection from a name or domain by probing token variants |
| API availability | Apify Actor input JSON only (no pip package) |
| Fields | Normalised title, company, location, remote, department, employment type, posted date, URL, description |
| Salary / experience | Not extracted |
| Direct apply URL | Board job URL |
| Pagination | Per-ATS as required |
| Duplicate detection | `global_id` hashing |
| Historical monitoring | **Yes**: `DeltaTracker` snapshots per input scope in an Apify key-value store and emits new and closed jobs; "snapshot scoped to exact input" is a good design note |
| Dependencies | apify, httpx |
| Maintenance | Looks recently active (code generator for per-ATS actors) |
| Reliability | Public APIs, so stable; tolerant parsing helpers (`collect`, `blank`, `as_dict`) |
| Reusability | **Reference only** (no licence). Ideas adopted: token variants from a domain, tolerant parsing, "failed board must never mark jobs closed" |
| Limitations | Locked to Apify storage; no India specifics; no salary/experience |

### 2.3 GetAnything-1/ats-jobs-scraper (MIT)

| Criterion | Finding |
|---|---|
| Sources | Greenhouse, Lever, Ashby (GraphQL), SmartRecruiters, Workday |
| Methodology | Public endpoints through `curl_cffi` |
| Fields | company, ats, title, location, remote, department, employmentType, postedDate, seniority/function guesses, description (≤5,000 chars), url, jobId |
| Salary / experience | None |
| Pagination | SmartRecruiters offset 100/page; **Workday limit capped at 20, silent empty page above 20, throttling on fast paging** (documented "three Workday traps") |
| Dedup / history | None |
| Dependencies | apify, curl_cffi, beautifulsoup4 |
| Reusability | **High as knowledge.** The Workday traps and endpoint list are directly useful; MIT permits adaptation with attribution |
| Limitations | Workday has no descriptions or dates in list view; Ashby via GraphQL gives no description; failed pages return `[]` silently (we improve this) |

### 2.4 sukhhdipsingh/ats-job-scraper

| Criterion | Finding |
|---|---|
| Sources | **14 ATS**: Workday, Oracle HCM, Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio (XML), Breezy, Rippling, Eightfold, Pinpoint, Teamtailor (RSS) |
| Methodology | Public endpoints; per-ATS `matchUrl/fetchAll/transform` module contract; URL-only sources for Workday/Oracle |
| Fields | Rich unified schema, compensation object, seniority, workplace type, technology tags, `daysOpen`, `isStale` |
| Salary | Structured where the ATS publishes it (Lever, Ashby, Recruitee, Breezy, Pinpoint) and converted to approximate USD. **Not India-relevant** |
| Experience | None (Personio exposes a `yearsOfExperience` field) |
| Pagination | Workday 20/page, Oracle 200/page advancing by returned count (a good defensive pattern) |
| Duplicate detection | `company|title|location` key |
| Historical monitoring | **Yes**: pure `diffRun()` state diff with repost counts and removed records |
| Dependencies | apify SDK (Node ≥20) |
| Tests | Node test suite with fixtures |
| Reusability | **Reference only**: no licence, and it is Node.js. The module contract, Oracle/Personio/Teamtailor endpoint shapes and the "failed board never marks removed" rule were re-implemented independently |
| Limitations | Node/Apify-coupled; no Indian-market features |

### 2.5 JobBot

| Criterion | Finding |
|---|---|
| Sources | Greenhouse (20 companies), Lever (22), RemoteOK, RSS (WWR, HN), Playwright scrapers (Naukri, LinkedIn, Glassdoor), BS4 scrapers (iimjobs, hirist), custom career pages |
| Methodology | httpx/BS4/Playwright; APScheduler every 8 h; FastAPI + React UI; SQLite |
| Scoring | LLM-based 0–100 (`ai/scorer.py`) with a 50 fallback. Non-deterministic, paid, no hard filters |
| Tracking | `Application` table with status; follow-up drafts |
| Auto-apply | Greenhouse/Lever/Workday form fillers with human "pause then submit" |
| Salary / experience | Not parsed as structured filters |
| Dependencies | fastapi, sqlmodel, playwright, anthropic/openai/gemini/groq SDKs, PyMuPDF |
| Reusability | **Reference only**: no licence, heavy stack, and the auto-apply code is **out of scope by requirement**. Ideas kept: ATS fingerprint detection, application-status lifecycle, score-with-explanation |
| Limitations | LLM-only scoring (no deterministic eligibility), Playwright fragility, local SQLite (not suitable for Actions), pinned old library versions |

## 3. Overlap matrix

| Capability | JobSpy | #2 | #3 | #4 | JobBot | Chosen for this project |
|---|:-:|:-:|:-:|:-:|:-:|---|
| Naukri | ✅ | | | | Playwright | JobSpy |
| LinkedIn / Indeed / Glassdoor / Google | ✅ | | | | partial | JobSpy |
| Greenhouse | | ✅ | ✅ | ✅ | ✅ | Own Python connector (pattern from #3, MIT) |
| Lever | | ✅ | ✅ | ✅ | ✅ | Own |
| Ashby | | ✅ | ✅ (GraphQL) | ✅ (REST) | | Own, uses the REST posting API |
| SmartRecruiters | | ✅ | ✅ | ✅ | | Own |
| Workday | | (actor) | ✅ | ✅ | apply only | Own, with #3's paging traps |
| Workable / Recruitee | | ✅ | | ✅ | | Own |
| Personio / Teamtailor / Breezy | | | | ✅ | | Own |
| Oracle Recruiting | | | | ✅ | | Own |
| SuccessFactors / iCIMS / Jobvite / Zoho Recruit | | | | | fingerprint only | **Not implemented** (no uniform public API); generic JSON-LD career-page connector instead |
| Change tracking | | ✅ | | ✅ | | Own, backed by Google Sheets |
| Dedup | | hash | key | key | hash | Own, multi-signal fuzzy |
| Experience / salary / company eligibility | partial | | | | | **Built from scratch** |
| Auto-apply | | | | | ✅ | **Excluded** |

## 4. Reuse ledger

| Component | Status | Detail |
|---|---|---|
| Naukri / LinkedIn / Indeed / Glassdoor / Google scraping | **Reused** | `python-jobspy` (MIT) as a pip dependency, called from `connectors/jobspy_connector.py` |
| ATS endpoint list, Workday limit/throttle handling | **Modified / adapted** | From GetAnything ats-jobs-scraper (MIT), rewritten in synchronous `requests` with explicit retries |
| Oracle, Personio, Teamtailor, Breezy, Workable, Recruitee endpoint shapes | **Independently re-implemented** | Endpoint facts only; no source copied from the unlicensed repos |
| "Failed board never marks jobs closed" | **Idea adopted** | Implemented in `processing/lifecycle.py` |
| Application status lifecycle | **Idea adopted** | Sheet dropdown New/Saved/Applied/Interview/Rejected/Offer/Closed |
| Everything else (normalisation, experience/salary/company rules, dedup, scoring, Sheets, email, Apify adapters/budget) | **Developed from scratch** | |
