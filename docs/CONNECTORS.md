# Connectors

> **Testing status.** All connector *parsing* is unit-tested on hand-written fixture payloads. **No connector has been run against its live service** (the build environment could not reach job sites). Run `python main.py validate-targets` and a `--dry-run` on your own machine before trusting any source. Live endpoints change; expect to fix some.

## Status of every source

| Source | Status | Notes |
|---|---|---|
| JobSpy: Naukri, Indeed, LinkedIn | Implemented, **not tested live** | default sites; `JOBSPY_PROXIES` supported; cloud IPs are often blocked; a site failing 3 times is skipped for the rest of the run |
| JobSpy: Glassdoor, Google | Implemented, **not tested live**; optional via `JOBSPY_SITES` | |
| ATS: Greenhouse, Lever | Implemented; **parser tested on fixtures**; endpoint not tested live | |
| ATS: Ashby, SmartRecruiters, Workable, Recruitee, Personio, Teamtailor, Breezy, Workday, Oracle Recruiting | Implemented, **not tested** (Ashby only for garbage tolerance) | public endpoints, no login |
| Career pages (JSON-LD `JobPosting`) | Implemented, **not tested**; **no pages configured by default** | checks robots.txt; never marks jobs closed |
| Apify (6 actors) | **Experimental**, off by default, never run | gating, result limits and budget tested with a fake HTTP layer; actor output adapters are unverified against real output |
| Apify IIMJobs | Experimental and **optional**; never a dependency | |
| AI providers (Groq, Gemini, Cloudflare, OpenRouter, custom) | Implemented; retry/fail-over/cache tested with fakes only, **never run live** | locator only - see [AI_PROVIDERS.md](AI_PROVIDERS.md) |
| Hirist, Foundit, Shine, Instahyre, TimesJobs | **Unsupported** | no public API; not in JobSpy; not planned for scraping |
| SuccessFactors, iCIMS, Jobvite, Zoho Recruit, Rippling, Eightfold, Pinpoint | **Unsupported** | no uniform public API |

**Coverage warning.** `config/targets.yaml` contains five *example* boards (Stripe, Spotify, Ramp, Visa, NVIDIA), all foreign employers, and no career pages. No Indian employer identifiers were added because none could be verified without live access and identifiers must not be invented. Coverage of Indian employers is therefore whatever you add plus what JobSpy returns; it is **not exhaustive**.

Retry policy for all HTTP: GET/HEAD only, capped and jittered exponential back-off, `Retry-After` honoured up to 60 s, no retry of 4xx other than 429. POST is never retried automatically.

## Adding boards
Edit `config/targets.yaml` or set `ATS_BOARDS_EXTRA` (comma separated), e.g. `greenhouse:acme,lever:foo,workday:https://x.wd5.myworkdayjobs.com/Site`. The starter list contains five **example** boards (Stripe, Spotify, Ramp, Visa, NVIDIA) from the upstream projects' documentation; replace them with employers relevant to you.

## Apify actors (optional, paid)
| Key | Actor | Price used for budgeting (estimate) |
|---|---|---|
| `linkedin` | blackfalcondata/linkedin-job-scraper | ~$0.00027/result + $0.0005/run |
| `naukri` | valig/naukri-jobs-scraper | ~$0.0004/result |
| `iimjobs` | data_daemon/iimjobs-jobs-scraper | ~$0.002/result + $0.001/run |
| `glassdoor` | valig/glassdoor-jobs-scraper | ~$0.0004/result |
| `google_jobs` | belcaidsaad/job-scraper-with-hiring-urgency-company-size-saad-belcaid | usage-priced; $0.10/run assumed |
| `workday` | fantastic-jobs/workday-jobs-api | ~$0.0012/result; actor minimum 200 jobs |

Prices were read from the actor pages on 2026-11-24 and **may be wrong or change**; the real `usageTotalUsd` returned by Apify is recorded in *Source Statistics*. An actor runs only if `APIFY_ENABLED=true`, a token is set (`APIFY_TOKENS`, or the legacy `APIFY_TOKEN`), `APIFY_APPROVED_MAX_USD_PER_RUN>0` and its own `APIFY_ENABLE_<NAME>=true`. The result limit is lowered so the estimate fits the remaining budget, and `maxItems` and `maxTotalChargeUsd` are sent with every run. Remember Apify also bills compute/proxy usage separately; set a monthly cap in the Apify console as well.


## Finding more Indian employer boards (verified, never guessed into the config)

`config/board_candidates.csv` lists employer **names** (tech, startups, financial services, consulting, FMCG, e-commerce, GCCs) with
no identifiers. Run:

```powershell
python main.py discover-boards            # or: --limit 5 to try a few first
```

For each name it derives a few mechanical slug guesses and makes ordinary single GET requests to the public Greenhouse, Lever,
Ashby and SmartRecruiters JSON endpoints (same endpoints the employers' own careers pages use; no login, no HTML scraping,
the normal request delay, at most 8 requests per company). It writes `discovery_output/discovered_boards.csv` with the number of
postings, how many are in India, and a ready `targets.yaml` line **only for boards that really answered with postings**. It never
edits `config/targets.yaml`; you review the CSV and copy the lines you want, then run `python main.py validate-targets`.
Expect most names to be "not found": many large Indian employers use Workday, Oracle or their own sites. For those, find the
careers URL yourself and add `workday:<url>` (see `config/targets.yaml`). Status: tested with fakes only, never run live.
