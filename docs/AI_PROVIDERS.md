# AI providers

The AI helper is **optional**. The Python rules decide every eligibility question. AI is only ever used to *locate* things
(a quote about experience, a job-function label) and **Python validates every answer** (the quote must appear word-for-word in
the posting and our own parser reads the numbers). With AI switched off the whole system still works.

## Order of work for every job

1. Python rules (experience, salary, location, company, function keywords).
2. If the rules already **exclude** the job → no AI.
3. If the posting text is **too short** (`AI_MIN_DESCRIPTION_CHARS`, default 200) → no AI.
4. **Cache** (tab "AI Cache" in your Google Sheet). Same text asked before → answer reused, no AI.
5. **Providers in order**: Groq → Gemini → Cloudflare Workers AI → OpenRouter (off) → your optional "custom" one.
6. Every provider failed → the Python rules decide alone. The daily run never stops because of AI.

## Supported providers (checked against the providers' own documentation, Nov 2026)

| Provider | Default model | Free tier (what the docs said) | JSON output | On by default |
|---|---|---|---|---|
| **Groq** | `openai/gpt-oss-20b` | Free plan with per-model limits. **Exact numbers are only on your own Groq console "Limits" page** | `json_object` mode (all models); strict schemas on gpt-oss/qwen3 | yes, if a key is set |
| **Gemini** | `gemini-3.8-flash` | Per-project limits shown in Google AI Studio, "not guaranteed" | `responseMimeType: application/json` | yes, if a key is set |
| **Cloudflare Workers AI** | `@cf/meta/llama-3.1-8b-instruct` | 10,000 "neurons"/day free, resets 00:00 UTC; above that requests fail | JSON mode (may answer "JSON Mode couldn't be met" → we move on) | yes, if token + account ID set |
| **OpenRouter** | `openrouter/free` | `:free` models: 20 requests/min; 50/day (or 1000/day if you ever bought ≥10 credits). Free model list changes often | not guaranteed → we validate | **no** |
| **Custom** (any OpenAI-compatible service) | you choose | depends | `json_object` | **no** |

Model names and limits change. If one stops working run `python main.py check-ai`, then change the model in `APP_ENV_FILE`
(no code change needed). Gemini note: `gemini-2.0-flash` was shut down on 2026-06-01; a model list returning HTTP 200 does not
prove `generateContent` works for that model, which is why `check-ai` makes a real tiny generation.

## Settings (all optional)

See `.env.example` for the full list. Main ones:

| Setting | Meaning |
|---|---|
| `AI_ENABLED=false` | Turns **all** AI off (rules only) |
| `AI_PROVIDER_ORDER=groq,gemini,cloudflare,openrouter` | Priority order |
| `GROQ_ENABLED`, `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_MAX_CALLS_PER_RUN` | Groq |
| `GEMINI_ENABLED`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_MAX_CALLS_PER_RUN` | Gemini |
| `CLOUDFLARE_ENABLED`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODEL` | Cloudflare |
| `OPENROUTER_ENABLED=true`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter (use a `:free` model) |
| `CUSTOM_ENABLED`, `CUSTOM_BASE_URL`, `CUSTOM_API_KEY`, `CUSTOM_MODEL` | Any other OpenAI-compatible service |
| `AI_MAX_CALLS_PER_RUN` (60) | All providers together |
| `AI_MAX_RETRIES` (3), `AI_TIMEOUT_SECONDS` (30), `AI_BACKOFF_*`, `AI_MAX_RETRY_AFTER_SECONDS` (30) | Retry behaviour |
| `AI_CACHE_MAX_ENTRIES` (3000), `AI_CACHE_TTL_DAYS` (45) | Cache size / age |

A provider without a key (or with `*_ENABLED=false`) is simply skipped. **Keys live only in `.env` (local) and the GitHub
`APP_ENV_FILE` secret. They are never written to Google Sheets, logs or the repository.**

## Failure handling

| Situation | What happens |
|---|---|
| 429, 500, 502, 503, 504, timeout | Retried with exponential back-off + random jitter (`Retry-After` respected up to 30 s) |
| Wrong key, wrong model name (404), bad request | **Not retried.** Clear message in the log; provider switched off for the run |
| Daily quota used up | Provider switched off for the run, next provider takes over |
| Long `Retry-After` (> 30 s) | Provider rests for that long, others are used meanwhile |
| Two failures in a row | Provider switched off for the rest of the run (not hammered) |
| Answer is not valid JSON / wrong shape | Counted as a failure, next provider is tried |
| All providers fail | Python rules only; failures are **not** cached, so tomorrow it tries again |

Log lines look like `[ai:gemini] model=gemini-3.8-flash HTTP 503 transient: ... - retrying in 4.1s (retry 1 of 3)`.
Keys, bearer tokens and `key=` URLs are redacted automatically.

## Commands

```powershell
python main.py show-config   # prints which models/providers are really in effect, then the number of Apify tokens (no secrets)
python main.py check-ai      # one tiny real request to EACH enabled provider, one by one
```

`check-ai` prints provider, model, connection result (with HTTP status), whether the reply was valid JSON, retries, time and
the failure reason. It uses a few tokens of free quota. Exit code 1 if any provider fails.

## Adding another provider

* **OpenAI-compatible service** (most are): no code. Set `CUSTOM_ENABLED=true`, `CUSTOM_BASE_URL=https://.../v1`,
  `CUSTOM_API_KEY`, `CUSTOM_MODEL`.
* **A named, separate one**: subclass `OpenAICompatProvider` in `integrations/ai_providers.py`
  (`name`, `url`), add it to `PROVIDER_CLASSES`, add its settings in `config/settings.py`. About 10 lines; see the
  `Cerebras` example in `tests/test_ai_router.py`.

## What the statistics tab shows

Every run adds rows with Connector = `AI` to **Source Statistics**: a `summary` row (jobs evaluated, jobs handled by rules
only, jobs requiring AI, total AI calls, cache hits, fallback attempts, skipped counts, estimated prompt tokens = characters/4)
and one row per provider (model, calls, successes, failures, retries, whether it was switched off and why).
Counts are measured during the run; only the token figure is an estimate.

## Cache design (why a tab, not "Raw Data")

The Raw Data column exists only for jobs that are *stored*. Most AI questions concern jobs the rules then drop, and GitHub Actions
starts with an empty disk each morning, so those would be asked again every day. The small **AI Cache** tab (same spreadsheet, no
new database) stores `hash of the text sent → validated answer`. A changed title or description changes the hash, so it is
re-asked automatically. Entries expire after 45 days; the tab is capped at 3000 rows. Dry runs read the cache but never write it.
