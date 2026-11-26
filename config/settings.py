"""The ONE central configuration loader.

Every setting in the whole project is read here, from the single root ``.env`` file
(or from real environment variables, which win over the file - this is what GitHub
Actions uses after it rebuilds ``.env`` from the ``APP_ENV_FILE`` secret).

Nothing else in the code base calls ``os.environ`` or ``load_dotenv``.
"""
from __future__ import annotations

import io

import base64
import json
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from integrations.ai_providers import normalize_gemini_model

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

APIFY_ACTORS = ("linkedin", "naukri", "iimjobs", "glassdoor", "google_jobs", "workday")


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _num(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip()) if value not in (None, "") else default
    except ValueError:
        return default


def _int(value: str | None, default: int) -> int:
    return int(_num(value, default))


def parse_token_list(value) -> list[str]:
    """APIFY_TOKENS -> clean list: split on commas and newlines (a list/tuple is accepted too), strip whitespace and
    surrounding quotes, ignore empty entries, drop duplicates (first occurrence wins, so the order is stable)."""
    if not value:
        return []
    parts = re.split(r"[,\r\n]+", value) if isinstance(value, str) else [str(v) for v in value if v]
    out: list[str] = []
    for part in parts:
        tok = part.strip().strip("\"'").strip()
        if tok and tok not in out:
            out.append(tok)
    return out


def _json_obj(value: str | None) -> dict:
    if not value or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _csv(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    # --- Google Sheets ---
    google_sheet_id: str = ""
    google_service_account_json_base64: str = ""
    # --- Email ---
    email_enabled: bool = True
    gmail_address: str = ""
    gmail_app_password: str = ""
    email_recipient: str = ""
    email_max_jobs_total: int = 40            # whole digest; higher-priority sections fill it first
    email_max_jobs_per_category: int = 15
    # --- Apify ---
    apify_token: str = ""                       # legacy single token (APIFY_TOKEN); still honoured
    apify_tokens: tuple[str, ...] = ()           # APIFY_TOKENS: any number of tokens, tried in rotation (see apify_token_list)
    apify_enabled: bool = False
    apify_enable: dict[str, bool] = field(default_factory=dict)
    apify_max_results: dict[str, int] = field(default_factory=dict)
    apify_approved_max_usd_per_run: float = 0.0
    apify_max_usd_per_actor: float = 0.5
    apify_run_timeout_seconds: int = 600
    apify_max_queries_per_actor: int = 2
    apify_input_json: dict[str, dict] = field(default_factory=dict)
    # --- Gemini ---
    gemini_api_key: str = ""
    gemini_enabled: bool = True
    gemini_model: str = "gemini-3.8-flash"
    gemini_max_calls_per_run: int = 20
    # --- other AI providers (all optional) ---
    ai_provider_order: list[str] = field(default_factory=lambda: ["groq", "gemini", "cloudflare", "openrouter"])
    groq_enabled: bool = True
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_max_calls_per_run: int = 40
    cloudflare_enabled: bool = True
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_model: str = "@cf/meta/llama-3.1-8b-instruct"
    cloudflare_max_calls_per_run: int = 20
    openrouter_enabled: bool = False          # off by default: free models change often and have small daily limits
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_max_calls_per_run: int = 10
    custom_enabled: bool = False              # any other OpenAI-compatible service; off by default
    custom_base_url: str = ""
    custom_api_key: str = ""
    custom_model: str = ""
    custom_max_calls_per_run: int = 10
    # --- AI-wide (shared by every provider) ---
    ai_enabled: bool = True
    dry_run_allow_ai: bool = False             # opt-in: let a DRY RUN send AI requests (uses free quota; writes nothing)
    ai_max_calls_per_run: int = 60            # all providers together (each provider also has its own limit)
    ai_min_description_chars: int = 200       # shorter text is not worth an AI question
    ai_cache_max_entries: int = 3000
    ai_cache_ttl_days: int = 45
    ai_timeout_seconds: float = 30.0
    ai_max_retries: int = 3
    ai_backoff_base_seconds: float = 2.0
    ai_backoff_max_seconds: float = 30.0
    ai_max_retry_after_seconds: float = 30.0
    # --- Free sources ---
    enable_jobspy: bool = True
    jobspy_sites: list[str] = field(default_factory=lambda: ["naukri", "indeed", "linkedin"])
    jobspy_results_per_query: int = 25
    jobspy_hours_old: int = 72
    jobspy_max_queries_per_run: int = 12
    jobspy_linkedin_fetch_description: bool = True
    jobspy_proxies: list[str] = field(default_factory=list)
    enable_ats: bool = True
    enable_career_pages: bool = True
    ats_boards_extra: list[str] = field(default_factory=list)
    career_page_urls_extra: list[str] = field(default_factory=list)
    # --- Eligibility ---
    minimum_ctc_lpa: float = 8.0
    minimum_internship_stipend_inr_monthly: float = 10000.0
    maximum_experience_years: float = 3.0
    startup_min_age_years: float = 5.0
    startup_min_employees: int = 90
    company_seed_mode: str = "strict"    # strict (default) = the unverified bundled seed never decides eligibility; flag = use it but flag every job
    strong_match_min_score: int = 70
    possible_match_min_score: int = 45
    # --- System ---
    timezone: str = "Asia/Kolkata"
    daily_run_time: str = "09:00"
    log_level: str = "INFO"
    http_timeout_seconds: float = 25.0
    http_max_retries: int = 3
    request_delay_seconds: float = 1.0
    source_time_budget_seconds: int = 900
    stale_after_days: int = 30
    close_after_missed_runs: int = 2
    dry_run: bool = False
    store_out_of_scope_jobs: bool = False

    # ---------------------------------------------------------------- helpers
    def service_account_info(self) -> dict[str, Any]:
        """Decode the Base64 service-account JSON. Raises ValueError with a friendly message."""
        raw = self.google_service_account_json_base64.strip()
        if not raw:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 is empty")
        try:
            info = json.loads(base64.b64decode(raw, validate=False).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 is not valid Base64 of a JSON key file "
                f"({type(exc).__name__}). Re-create it as described in README."
            ) from None
        if info.get("type") != "service_account" or "client_email" not in info:
            raise ValueError("The decoded JSON is not a Google service-account key (missing client_email).")
        return info

    @property
    def apify_token_list(self) -> tuple[str, ...]:
        """The tokens in use: APIFY_TOKENS if it holds any valid token, otherwise the legacy APIFY_TOKEN. Cleaned and
        de-duplicated even when Settings is built by hand."""
        tokens = parse_token_list(self.apify_tokens) or parse_token_list(self.apify_token)
        return tuple(tokens)

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs."""
        vals = [self.google_service_account_json_base64, self.gmail_app_password,
                *self.apify_token_list, self.apify_token, *self.apify_tokens, self.gemini_api_key, self.groq_api_key,
                self.cloudflare_api_token, self.openrouter_api_key, self.custom_api_key]
        try:
            info = self.service_account_info()
            vals += [info.get("private_key", ""), info.get("private_key_id", "")]
        except ValueError:
            pass
        return [v for v in vals if v and len(v) >= 6]

    def enabled_apify_actors(self) -> list[str]:
        """Actors that are allowed to run. All gates must be open (see docs/CONFIGURATION.md)."""
        if not (self.apify_enabled and self.apify_token_list and self.apify_approved_max_usd_per_run > 0):
            return []
        return [a for a in APIFY_ACTORS if self.apify_enable.get(a)]

    def provider_config(self, name: str) -> dict[str, Any]:
        """Uniform view of one provider's settings (never logged: contains the key)."""
        if name == "cloudflare":
            return {"enabled": self.cloudflare_enabled, "api_key": self.cloudflare_api_token, "account_id": self.cloudflare_account_id,
                    "model": self.cloudflare_model, "max_calls": self.cloudflare_max_calls_per_run}
        if name == "custom":
            return {"enabled": self.custom_enabled, "api_key": self.custom_api_key, "base_url": self.custom_base_url,
                    "model": self.custom_model, "max_calls": self.custom_max_calls_per_run}
        return {"enabled": getattr(self, f"{name}_enabled"), "api_key": getattr(self, f"{name}_api_key"),
                "model": getattr(self, f"{name}_model"), "max_calls": getattr(self, f"{name}_max_calls_per_run")}

    def ai_problems(self) -> list[str]:
        """Problems with the AI settings (never contains a key)."""
        import re as _re
        out: list[str] = []
        if not self.ai_enabled:
            return out
        known = {"groq", "gemini", "cloudflare", "openrouter", "custom"}
        for n in self.ai_provider_order:
            if n not in known:
                out.append(f"AI_PROVIDER_ORDER contains unknown provider '{n}' (known: {', '.join(sorted(known))}).")
        if self.gemini_enabled and self.gemini_api_key:
            if not _re.fullmatch(r"[A-Za-z0-9._-]+", self.gemini_model):
                out.append("GEMINI_MODEL contains unexpected characters; use a plain name such as gemini-3.8-flash.")
        for n in known:
            cfg = self.provider_config(n)
            if cfg["api_key"][:1] in ("'", '"'):
                out.append(f"The {n} key starts with a quote character - remove the quotes in .env / APP_ENV_FILE.")
        if self.cloudflare_enabled and self.cloudflare_api_token and not _re.fullmatch(r"[0-9a-fA-F]{32}", self.cloudflare_account_id or ""):
            out.append("CLOUDFLARE_ACCOUNT_ID must be the 32-character account ID (Cloudflare dashboard > Workers AI).")
        if self.custom_enabled and not (self.custom_base_url.startswith("https://") and self.custom_api_key and self.custom_model):
            out.append("CUSTOM_ENABLED=true needs CUSTOM_BASE_URL (https://...), CUSTOM_API_KEY and CUSTOM_MODEL.")
        if self.openrouter_enabled and not self.openrouter_api_key:
            out.append("OPENROUTER_ENABLED=true but OPENROUTER_API_KEY is empty.")
        return out

    def validate(self, need_sheets: bool = True, need_email: bool = False) -> list[str]:
        """Return a list of human-readable problems (empty list = OK)."""
        problems: list[str] = []
        if need_sheets:
            if not self.google_sheet_id:
                problems.append("GOOGLE_SHEET_ID is empty.")
            try:
                self.service_account_info()
            except ValueError as exc:
                problems.append(str(exc))
        if need_email:
            for name, val in (("GMAIL_ADDRESS", self.gmail_address),
                              ("GMAIL_APP_PASSWORD", self.gmail_app_password),
                              ("EMAIL_RECIPIENT", self.email_recipient)):
                if not val:
                    problems.append(f"{name} is empty (needed to send the email digest).")
        problems += self.ai_problems()
        if self.apify_enabled and not self.apify_token_list:
            problems.append("APIFY_ENABLED=true but neither APIFY_TOKENS nor APIFY_TOKEN is set - Apify will be skipped.")
        if self.apify_enabled and self.apify_approved_max_usd_per_run <= 0:
            problems.append("APIFY_ENABLED=true but APIFY_APPROVED_MAX_USD_PER_RUN is 0 - Apify will be skipped.")
        return problems


def read_env_file(path: Path) -> dict[str, str | None]:
    """Read a .env file even if Windows saved it as UTF-8-with-BOM or UTF-16 (PowerShell ``>`` does that)."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError(f"{path.name} is not saved as UTF-8 text. Re-save it as UTF-8 in Notepad "
                             "(File > Save As > Encoding: UTF-8).") from None
    return dict(dotenv_values(stream=io.StringIO(text)))


def load_settings(env_path: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    """Build Settings from the root .env file, overridden by real environment variables.

    ``environ`` lets tests inject values without touching the real environment.
    """
    path = env_path or ENV_PATH
    values: dict[str, str | None] = {}
    if path.exists():
        values.update(read_env_file(path))
    source_env = os.environ if environ is None else environ
    for key, val in source_env.items():
        # real env vars win, but an empty one must not erase a value from the file
        if val != "" or key not in values:
            values[key] = val

    g = values.get

    return Settings(
        google_sheet_id=(g("GOOGLE_SHEET_ID") or "").strip(),
        google_service_account_json_base64=(g("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64") or "").strip(),
        email_enabled=_truthy(g("EMAIL_ENABLED"), True),
        gmail_address=(g("GMAIL_ADDRESS") or "").strip(),
        gmail_app_password=(g("GMAIL_APP_PASSWORD") or "").replace(" ", ""),
        email_recipient=(g("EMAIL_RECIPIENT") or "").strip(),
        email_max_jobs_per_category=_int(g("EMAIL_MAX_JOBS_PER_CATEGORY"), 15),
        email_max_jobs_total=max(1, _int(g("EMAIL_MAX_JOBS_TOTAL"), 40)),
        apify_token=(g("APIFY_TOKEN") or "").strip(),
        apify_tokens=tuple(parse_token_list(g("APIFY_TOKENS"))),
        apify_enabled=_truthy(g("APIFY_ENABLED"), False),
        apify_enable={a: _truthy(g(f"APIFY_ENABLE_{a.upper()}"), False) for a in APIFY_ACTORS},
        apify_max_results={a: max(1, _int(g(f"APIFY_MAX_RESULTS_{a.upper()}"), 50)) for a in APIFY_ACTORS},
        apify_approved_max_usd_per_run=max(0.0, _num(g("APIFY_APPROVED_MAX_USD_PER_RUN"), 0.0)),
        apify_max_usd_per_actor=max(0.0, _num(g("APIFY_MAX_USD_PER_ACTOR"), 0.5)),
        apify_run_timeout_seconds=_int(g("APIFY_RUN_TIMEOUT_SECONDS"), 600),
        apify_max_queries_per_actor=max(1, _int(g("APIFY_MAX_QUERIES_PER_ACTOR"), 2)),
        apify_input_json={a: _json_obj(g(f"APIFY_INPUT_JSON_{a.upper()}")) for a in APIFY_ACTORS},
        gemini_api_key=(g("GEMINI_API_KEY") or "").strip(),
        gemini_enabled=_truthy(g("GEMINI_ENABLED"), True),
        gemini_model=normalize_gemini_model(g("GEMINI_MODEL") or "") or "gemini-3.8-flash",
        gemini_max_calls_per_run=max(0, _int(g("GEMINI_MAX_CALLS_PER_RUN"), 20)),
        ai_provider_order=[x.lower() for x in _csv(g("AI_PROVIDER_ORDER"))] or ["groq", "gemini", "cloudflare", "openrouter"],
        groq_enabled=_truthy(g("GROQ_ENABLED"), True),
        groq_api_key=(g("GROQ_API_KEY") or "").strip(),
        groq_model=(g("GROQ_MODEL") or "openai/gpt-oss-20b").strip(),
        groq_max_calls_per_run=max(0, _int(g("GROQ_MAX_CALLS_PER_RUN"), 40)),
        cloudflare_enabled=_truthy(g("CLOUDFLARE_ENABLED"), True),
        cloudflare_account_id=(g("CLOUDFLARE_ACCOUNT_ID") or "").strip(),
        cloudflare_api_token=(g("CLOUDFLARE_API_TOKEN") or "").strip(),
        cloudflare_model=(g("CLOUDFLARE_MODEL") or "@cf/meta/llama-3.1-8b-instruct").strip(),
        cloudflare_max_calls_per_run=max(0, _int(g("CLOUDFLARE_MAX_CALLS_PER_RUN"), 20)),
        openrouter_enabled=_truthy(g("OPENROUTER_ENABLED"), False),
        openrouter_api_key=(g("OPENROUTER_API_KEY") or "").strip(),
        openrouter_model=(g("OPENROUTER_MODEL") or "openrouter/free").strip(),
        openrouter_max_calls_per_run=max(0, _int(g("OPENROUTER_MAX_CALLS_PER_RUN"), 10)),
        custom_enabled=_truthy(g("CUSTOM_ENABLED"), False),
        custom_base_url=(g("CUSTOM_BASE_URL") or "").strip(),
        custom_api_key=(g("CUSTOM_API_KEY") or "").strip(),
        custom_model=(g("CUSTOM_MODEL") or "").strip(),
        custom_max_calls_per_run=max(0, _int(g("CUSTOM_MAX_CALLS_PER_RUN"), 10)),
        ai_enabled=_truthy(g("AI_ENABLED"), True),
        dry_run_allow_ai=_truthy(g("DRY_RUN_ALLOW_AI"), False),
        ai_max_calls_per_run=max(0, _int(g("AI_MAX_CALLS_PER_RUN"), 60)),
        ai_min_description_chars=max(0, _int(g("AI_MIN_DESCRIPTION_CHARS"), 200)),
        ai_cache_max_entries=max(0, _int(g("AI_CACHE_MAX_ENTRIES"), 3000)),
        ai_cache_ttl_days=max(1, _int(g("AI_CACHE_TTL_DAYS"), 45)),
        ai_timeout_seconds=max(1.0, _num(g("AI_TIMEOUT_SECONDS"), 30.0)),
        ai_max_retries=min(6, max(0, _int(g("AI_MAX_RETRIES"), 3))),
        ai_backoff_base_seconds=max(0.0, _num(g("AI_BACKOFF_BASE_SECONDS"), 2.0)),
        ai_backoff_max_seconds=max(0.0, _num(g("AI_BACKOFF_MAX_SECONDS"), 30.0)),
        ai_max_retry_after_seconds=max(0.0, _num(g("AI_MAX_RETRY_AFTER_SECONDS"), 30.0)),
        enable_jobspy=_truthy(g("ENABLE_JOBSPY"), True),
        jobspy_sites=[s.lower() for s in _csv(g("JOBSPY_SITES"))] or ["naukri", "indeed", "linkedin"],
        jobspy_results_per_query=max(1, _int(g("JOBSPY_RESULTS_PER_QUERY"), 25)),
        jobspy_hours_old=max(1, _int(g("JOBSPY_HOURS_OLD"), 72)),
        jobspy_max_queries_per_run=max(1, _int(g("JOBSPY_MAX_QUERIES_PER_RUN"), 12)),
        jobspy_linkedin_fetch_description=_truthy(g("JOBSPY_LINKEDIN_FETCH_DESCRIPTION"), True),
        jobspy_proxies=_csv(g("JOBSPY_PROXIES")),
        enable_ats=_truthy(g("ENABLE_ATS"), True),
        enable_career_pages=_truthy(g("ENABLE_CAREER_PAGES"), True),
        ats_boards_extra=_csv(g("ATS_BOARDS_EXTRA")),
        career_page_urls_extra=_csv(g("CAREER_PAGE_URLS_EXTRA")),
        minimum_ctc_lpa=_num(g("MINIMUM_CTC_LPA"), 8.0),
        minimum_internship_stipend_inr_monthly=_num(g("MINIMUM_INTERNSHIP_STIPEND_INR_MONTHLY"), 10000.0),
        maximum_experience_years=_num(g("MAXIMUM_EXPERIENCE_YEARS"), 3.0),
        startup_min_age_years=_num(g("STARTUP_MIN_AGE_YEARS"), 5.0),
        startup_min_employees=_int(g("STARTUP_MIN_EMPLOYEES"), 90),
        strong_match_min_score=_int(g("STRONG_MATCH_MIN_SCORE"), 70),
        possible_match_min_score=_int(g("POSSIBLE_MATCH_MIN_SCORE"), 45),
        timezone=(g("TIMEZONE") or "Asia/Kolkata").strip(),
        daily_run_time=(g("DAILY_RUN_TIME") or "09:00").strip(),
        log_level=(g("LOG_LEVEL") or "INFO").strip().upper(),
        http_timeout_seconds=_num(g("HTTP_TIMEOUT_SECONDS"), 25.0),
        http_max_retries=_int(g("HTTP_MAX_RETRIES"), 3),
        request_delay_seconds=_num(g("REQUEST_DELAY_SECONDS"), 1.0),
        source_time_budget_seconds=_int(g("SOURCE_TIME_BUDGET_SECONDS"), 900),
        stale_after_days=_int(g("STALE_AFTER_DAYS"), 30),
        close_after_missed_runs=max(1, _int(g("CLOSE_AFTER_MISSED_RUNS"), 2)),
        company_seed_mode=("flag" if (g("COMPANY_SEED_MODE") or "strict").strip().lower() == "flag" else "strict"),   # anything unclear = strict
        dry_run=_truthy(g("DRY_RUN"), False),
        store_out_of_scope_jobs=_truthy(g("STORE_OUT_OF_SCOPE_JOBS"), False),
    )
