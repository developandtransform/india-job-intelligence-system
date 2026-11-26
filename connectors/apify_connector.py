"""Optional paid sources through the Apify platform (REST API). EVERYTHING IS OFF BY DEFAULT.

An actor runs only when ALL of these are true (see ``Settings.enabled_apify_actors``):
  1. APIFY_ENABLED=true                         (master switch)
  2. at least one token is set (APIFY_TOKENS, or the legacy APIFY_TOKEN)
  3. APIFY_ENABLE_<ACTOR>=true                  (per-actor switch)
  4. APIFY_APPROVED_MAX_USD_PER_RUN > 0         (your explicit spending approval for one daily run)

Spend controls
--------------
* a per-run budget (the approval above) shared by all actors, tracked by ``SpendTracker``
* a per-actor ceiling (APIFY_MAX_USD_PER_ACTOR) sent to Apify as ``maxTotalChargeUsd`` (Apify stops the run at that cost)
* a per-actor result limit (APIFY_MAX_RESULTS_<ACTOR>) sent as ``maxItems`` and in the actor input
* the result limit is lowered up front so that (limit x price) fits inside the remaining budget
* the real cost reported by Apify (``usageTotalUsd``) is recorded in the Source Statistics tab

Prices below are the ones displayed on each Actor's Apify page on 2026-11-24 and are ONLY used for the pre-run estimate.
Apify's current price always applies - check the Actor's Pricing tab before enabling it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from connectors.base import Connector, ConnectorResult
from connectors.jobspy_connector import select_queries
from core.models import Job
from core.textutil import html_to_text, to_iso_date

log = logging.getLogger("apify")
API = "https://api.apify.com/v2"


def g(d: Any, path: str, default: Any = None) -> Any:
    """Tolerant nested getter: g(item, 'company.name')."""
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur and cur[part] is not None:
            cur = cur[part]
        else:
            return default
    return cur


# ----------------------------------------------------------------- adapters
def adapt_naukri(it: dict) -> Job | None:
    title, company = it.get("title"), g(it, "company.name")
    if not title or not company:
        return None
    locs = ", ".join(str(l.get("label")) for l in it.get("locations", []) if isinstance(l, dict) and l.get("label"))
    j = Job(original_id=str(it.get("id", "")), source="apify:naukri", title=str(title), company=str(company), location=locs,
            description=html_to_text(g(it, "description.full") or g(it, "description.short")), job_url=it.get("url", ""),
            apply_url=it.get("url", ""), posted_date=to_iso_date(it.get("createdDate") or it.get("createdDateText")),
            employment_type=str(it.get("employmentType") or it.get("jobType") or ""), hint_country="India",
            company_website=str(g(it, "company.websiteUrl", "")))
    j.full_description = j.description
    j.hint_remote = True if str(it.get("wfhType")) == "2" else None
    sal = it.get("salary") or {}
    if sal.get("minimum") or sal.get("maximum"):
        j.hint_salary = {"min": sal.get("minimum"), "max": sal.get("maximum"), "currency": sal.get("currency") or "INR",
                         "period": "year", "basis": "Platform-CTC", "text": sal.get("label") or "", "source": "apify:naukri"}
    exp = it.get("experience") or {}
    if exp.get("text") or exp.get("minimum") is not None:
        j.hint_exp_text = str(exp.get("text") or "")
        try:
            j.hint_exp_min = float(exp["minimum"]) if exp.get("minimum") not in (None, "") else None
            j.hint_exp_max = float(exp["maximum"]) if exp.get("maximum") not in (None, "") else None
        except (TypeError, ValueError):
            pass
    return j


def adapt_linkedin(it: dict) -> Job | None:
    title, company = it.get("title"), it.get("company")
    if not title or not company:
        return None
    j = Job(original_id=str(it.get("linkedinJobId") or it.get("jobId", "")), source="apify:linkedin", title=str(title), company=str(company),
            location=str(it.get("location") or ""), description=str(it.get("description") or html_to_text(it.get("descriptionHtml")) or ""),
            job_url=it.get("jobUrl", ""), apply_url=it.get("applyUrl") or it.get("jobUrl", ""), posted_date=to_iso_date(it.get("postedAt")),
            employment_type=str(it.get("employmentType") or ""), hint_country="India", company_website=str(it.get("companyUrl") or ""))
    j.full_description = j.description
    yrs = g(it, "aiEnrichment.yearsExperienceMin")
    if yrs is not None:
        j.raw["ai_years_min"] = yrs      # kept for audit only: a minimum, so it cannot prove eligibility by itself
    return j


def adapt_iimjobs(it: dict) -> Job | None:
    title, company = it.get("title"), g(it, "company.name")
    if not title or not company:
        return None
    locs = it.get("locations") or []
    j = Job(original_id=str(it.get("id", "")), source="apify:iimjobs", title=str(title), company=str(company),
            location=", ".join(str(l) for l in locs) if isinstance(locs, list) else str(locs), description=str(it.get("description") or html_to_text(it.get("descriptionHtml")) or ""),
            job_url=it.get("url", ""), apply_url=it.get("url", ""), posted_date=to_iso_date(it.get("postedAt")), hint_country="India",
            company_website=str(g(it, "company.domain", "")))
    j.full_description = j.description
    j.hint_remote = True if it.get("remote") else None
    lo, hi = it.get("experienceMinYears"), it.get("experienceMaxYears")
    if lo is not None or hi is not None:
        j.hint_exp_min = float(lo) if lo is not None else None
        j.hint_exp_max = float(hi) if hi is not None else None
        j.hint_exp_text = f"{lo}-{hi} years"
    slo, shi = it.get("salaryMinLpa"), it.get("salaryMaxLpa")
    if not it.get("salaryHidden") and (slo or shi):
        j.hint_salary = {"min": float(slo) * 100000 if slo else None, "max": float(shi) * 100000 if shi else None, "currency": "INR",
                         "period": "year", "basis": "Platform-CTC", "text": f"{slo}-{shi} LPA (IIMJobs)", "source": "apify:iimjobs"}
    hints = {}
    if g(it, "company.foundedYear"):
        hints["founded_year"] = str(g(it, "company.foundedYear"))
    if g(it, "company.employeesIndia"):
        hints["employees"] = str(g(it, "company.employeesIndia"))
    if g(it, "company.companyType"):
        hints["company_type"] = str(g(it, "company.companyType"))
    if g(it, "company.industry"):
        hints["industry"] = str(g(it, "company.industry"))
    j.hint_company = hints
    return j


def adapt_glassdoor(it: dict) -> Job | None:
    title, company = it.get("title"), g(it, "employer.name")
    if not title or not company:
        return None
    j = Job(original_id=str(it.get("id", "")), source="apify:glassdoor", title=str(title), company=str(company), location=str(g(it, "location.name", "")),
            description=html_to_text(it.get("description")), job_url=it.get("seoUrl") or it.get("url", ""), apply_url=it.get("url", ""),
            hint_country="India")
    j.full_description = j.description
    if isinstance(it.get("ageInDays"), (int, float)):
        j.posted_date = datetime.fromtimestamp(time.time() - it["ageInDays"] * 86400, tz=timezone.utc).date().isoformat()
    pay = it.get("pay") or {}
    # Glassdoor also shows its OWN estimates; only employer-provided pay may be used as a stated salary
    if pay.get("source") == "EMPLOYER_PROVIDED" and (pay.get("min") or pay.get("max")):
        period = {"ANNUAL": "year", "MONTHLY": "month", "WEEKLY": "week", "DAILY": "day", "HOURLY": "hour"}.get(str(pay.get("period", "")).upper(), "")
        j.hint_salary = {"min": pay.get("min"), "max": pay.get("max"), "currency": pay.get("currency"), "period": period,
                         "basis": "Unspecified", "text": "Glassdoor employer-provided pay", "source": "apify:glassdoor"}
    return j


def adapt_google_jobs(it: dict) -> Job | None:
    title, company = it.get("job_title"), it.get("company_name")
    if not title or not company:
        return None
    desc = str(it.get("description") or "")
    if it.get("salary_range"):
        desc = f"Salary: {it['salary_range']}\n{desc}"
    url = it.get("apply_url") or ""
    j = Job(original_id=url or f"{company}|{title}|{it.get('location','')}", source="apify:google_jobs", title=str(title), company=str(company),
            location=str(it.get("location") or ""), description=desc, job_url=url, apply_url=url, employment_type=str(it.get("employment_type") or ""),
            hint_country="India", company_website=str(it.get("company_domain") or ""))
    j.full_description = j.description
    j.hint_remote = True if it.get("remote") else None
    if isinstance(it.get("days_listed"), (int, float)):
        j.posted_date = datetime.fromtimestamp(time.time() - it["days_listed"] * 86400, tz=timezone.utc).date().isoformat()
    if it.get("company_size"):
        j.hint_company = {"employees": str(it["company_size"])}
    return j


def adapt_workday(it: dict) -> Job | None:
    title, org = it.get("title"), it.get("organization")
    if not title or not org:
        return None
    locs = it.get("locations_derived") or it.get("locations_alt") or []
    desc = str(it.get("description_text") or html_to_text(it.get("description_html")) or "")
    extra = it.get("ai_requirements_summary")
    if extra:
        desc += "\n" + str(extra)
    j = Job(original_id=str(it.get("id", "")), source="apify:workday", title=str(title), company=str(org),
            location="; ".join(str(l) for l in locs) if isinstance(locs, list) else str(locs), description=desc, job_url=it.get("url", ""),
            apply_url=it.get("url", ""), posted_date=to_iso_date(it.get("date_posted")),
            employment_type=", ".join(it.get("employment_type") or []) if isinstance(it.get("employment_type"), list) else str(it.get("employment_type") or ""))
    j.full_description = j.description
    j.hint_remote = True if "remote" in str(it.get("ai_work_arrangement") or "").lower() else None
    hints = {}
    if it.get("org_linkedin_headcount"):
        hints["employees"] = str(it["org_linkedin_headcount"])
    elif it.get("org_linkedin_size"):
        hints["employees"] = str(it["org_linkedin_size"])
    fd = str(it.get("org_linkedin_founded_date") or "")
    if fd[:4].isdigit():
        hints["founded_year"] = fd[:4]
    j.hint_company = hints
    return j


# ------------------------------------------------------------------- specs
@dataclass(frozen=True)
class ActorSpec:
    key: str
    actor_id: str
    label: str
    price_per_result: float        # USD, from the Actor page (estimate only)
    price_per_run: float           # USD flat start fee / estimate
    build_input: Callable[[str, int], dict]
    adapter: Callable[[dict], Job | None]
    notes: str = ""


ACTORS: dict[str, ActorSpec] = {
    "linkedin": ActorSpec("linkedin", "blackfalcondata/linkedin-job-scraper", "LinkedIn", 0.00027, 0.0005,
                          lambda q, n: {"keywords": q, "location": "India", "maxResults": n, "enrichDetails": True}, adapt_linkedin,
                          "pay-per-event; compute/proxy billed separately"),
    "naukri": ActorSpec("naukri", "valig/naukri-jobs-scraper", "Naukri", 0.0004, 0.0,
                        lambda q, n: {"keywords": q, "location": "India", "sort": "f", "jobAge": "7", "limit": n}, adapt_naukri,
                        "page shows both $0.28/1k and $0.40/1k; the higher is used for the estimate"),
    "iimjobs": ActorSpec("iimjobs", "data_daemon/iimjobs-jobs-scraper", "IIMJobs", 0.002, 0.001,
                         lambda q, n: {"queries": [q], "maxExperience": 3, "postedWithinDays": 7, "includeDescription": True,
                                       "maxJobsPerQuery": n, "maxJobs": n, "site": "iimjobs"}, adapt_iimjobs,
                         "native maxExperience filter; returns founded year / headcount / company type"),
    "glassdoor": ActorSpec("glassdoor", "valig/glassdoor-jobs-scraper", "Glassdoor", 0.0004, 0.0,
                           lambda q, n: {"keywords": q, "location": "India", "daysOld": 7, "limit": n, "sortBy": "date_desc"}, adapt_glassdoor,
                           "only employer-provided pay is used"),
    "google_jobs": ActorSpec("google_jobs", "belcaidsaad/job-scraper-with-hiring-urgency-company-size-saad-belcaid", "Google Jobs", 0.0, 0.10,
                             lambda q, n: {"searchQuery": q, "location": "India", "datePosted": "week", "maxListings": n}, adapt_google_jobs,
                             "usage-priced (compute units); a flat $0.10/run estimate is used - verify"),
    "workday": ActorSpec("workday", "fantastic-jobs/workday-jobs-api", "Workday (all tenants)", 0.0012, 0.0,
                         lambda q, n: {"maxJobs": max(200, n), "titleSearch": q, "locationSearch": "India", "includeCompanyDetails": True}, adapt_workday,
                         "the actor enforces a minimum of 200 jobs per run"),
}


# Responses that prove NO run was created because the request itself was refused. Only these allow trying another token.
FAILOVER_STATUSES = frozenset({401, 403, 429})


class ApifyStartRejected(RuntimeError):
    """Every configured token was refused (401/403/429) before any run existed: nothing was started, nothing can be billed."""


class TokenPool:
    """Round-robin Apify tokens. Thread-safe. Tokens are only ever handled by index in logs and messages.

    * ``start_order()`` gives the tokens to try for ONE new run: starting at the rotating cursor, each token at most once.
    * after a successful start ``started_with(i)`` moves the cursor to the NEXT token, so run 1 uses A, run 2 B, run 3 C, run 4 A.
    * a token refused with 401/403 is skipped for the rest of this execution; one refused with 429 is tried last next time.
    * the token that started a run must be used for that run's polling / abort / dataset: callers keep the index.
    """

    def __init__(self, tokens) -> None:
        self._tokens = tuple(tokens)
        self._cursor = 0
        self._rejected: set[int] = set()      # 401 / 403
        self._limited: set[int] = set()       # 429
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._tokens)

    def token(self, index: int) -> str:
        return self._tokens[index]

    def start_order(self) -> list[int]:
        with self._lock:
            n = len(self._tokens)
            order = [(self._cursor + k) % n for k in range(n)] if n else []
            usable = [i for i in order if i not in self._rejected]
            return [i for i in usable if i not in self._limited] + [i for i in usable if i in self._limited]

    def started_with(self, index: int) -> None:
        with self._lock:
            self._limited.discard(index)
            self._cursor = (index + 1) % len(self._tokens)

    def mark_refused(self, index: int, status: int) -> None:
        with self._lock:
            (self._limited if status == 429 else self._rejected).add(index)

    def usable(self) -> int:
        with self._lock:
            return len(self._tokens) - len(self._rejected)


@dataclass
class SpendTracker:
    approved_usd: float
    per_actor_cap_usd: float
    spent_usd: float = 0.0
    per_actor: dict[str, float] = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return max(0.0, self.approved_usd - self.spent_usd)

    def plan(self, spec: ActorSpec, wanted: int, actor_spent: float) -> tuple[int, float]:
        """Return (allowed result limit, max charge to send to Apify). limit=0 means: do not run."""
        ceiling = min(self.per_actor_cap_usd - actor_spent, self.remaining)
        if ceiling <= spec.price_per_run:
            return 0, 0.0
        if spec.price_per_result <= 0:
            return (wanted, round(ceiling, 4)) if spec.price_per_run <= ceiling else (0, 0.0)
        affordable = int((ceiling - spec.price_per_run) / spec.price_per_result)
        return max(0, min(wanted, affordable)), round(ceiling, 4)

    def record(self, key: str, cost: float) -> None:
        self.spent_usd += cost
        self.per_actor[key] = self.per_actor.get(key, 0.0) + cost


class ApifyConnector(Connector):
    name = "apify"
    free = False
    TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

    def __init__(self, settings, http) -> None:
        super().__init__(settings, http)
        self.tracker = SpendTracker(settings.apify_approved_max_usd_per_run, settings.apify_max_usd_per_actor)
        self.per_actor_stats: dict[str, dict] = {}
        self.pool = TokenPool(getattr(settings, "apify_token_list", None) or ((settings.apify_token,) if settings.apify_token else ()))

    def _redact(self, text: str) -> str:
        """Safety net: no configured token may ever appear in an error message."""
        for i in range(len(self.pool)):
            text = text.replace(self.pool.token(i), f"<apify token #{i + 1}>")
        return text

    def enabled(self) -> bool:
        return bool(self.settings.enabled_apify_actors())

    def _headers(self, index: int = 0) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.pool.token(index)}", "Content-Type": "application/json"}

    def _start_run(self, actor: str, params: dict, payload: dict) -> tuple[dict, int]:
        """Start ONE run. Returns (run data, index of the token that started it). Failover to the next token happens only when a
        token was explicitly refused (401/403/429), which proves no run was created. A timeout, a connection error, a 5xx or an
        unreadable answer is ambiguous (a paid run may exist), so it propagates: the caller records the maximum cost and stops."""
        refused: list[str] = []
        for idx in self.pool.start_order():                  # each configured token at most once
            resp = self.http.post(f"{API}/acts/{actor}/runs", params=params, json=payload, headers=self._headers(idx))
            status = getattr(resp, "status_code", 0)
            if status in FAILOVER_STATUSES:
                self.pool.mark_refused(idx, status)
                refused.append(f"token #{idx + 1}: HTTP {status}")
                log.warning("Apify token #%d refused with HTTP %d before any run was created; trying the next token", idx + 1, status)
                continue
            resp.raise_for_status()
            run = resp.json()["data"]
            self.pool.started_with(idx)
            return run, idx
        raise ApifyStartRejected("no Apify token was accepted (" + ("; ".join(refused) or "none usable") + ")")

    def _run_actor(self, spec: ActorSpec, payload: dict, limit: int, max_usd: float, deadline: float) -> tuple[list[dict], float, str]:
        if getattr(self.settings, "dry_run", False):
            raise RuntimeError("dry run: Apify actors are never started")      # last line of defence, base.run() already skips
        actor = spec.actor_id.replace("/", "~")
        timeout = int(min(self.settings.apify_run_timeout_seconds, max(30, deadline - time.monotonic())))
        params = {"maxItems": limit, "maxTotalChargeUsd": max_usd, "timeout": timeout}
        run, idx = self._start_run(actor, params, payload)
        run_id, status = run["id"], run.get("status", "READY")
        hdr = self._headers(idx)            # the SAME token for the whole life of this run: poll, abort, dataset
        try:
            while status not in self.TERMINAL:
                if time.monotonic() > deadline:
                    self.http.post(f"{API}/actor-runs/{run_id}/abort", headers=hdr)
                    status = "ABORTED"
                    break
                time.sleep(5)
                run = self.http.get(f"{API}/actor-runs/{run_id}", headers=hdr).json()["data"]
                status = run.get("status", status)
        except Exception:
            try:       # best effort: stop a run we can no longer watch (same token). The actor is NEVER started again.
                self.http.post(f"{API}/actor-runs/{run_id}/abort", headers=hdr)
            except Exception:  # noqa: BLE001
                pass
            raise
        cost = float(run.get("usageTotalUsd") or 0.0)
        items: list[dict] = []
        if run.get("defaultDatasetId"):
            r = self.http.get(f"{API}/datasets/{run['defaultDatasetId']}/items", params={"format": "json", "clean": "true", "limit": limit},
                              headers=hdr)
            r.raise_for_status()
            data = r.json()
            items = data[:limit] if isinstance(data, list) else []      # never accept more rows than the approved limit
        return items, cost, status

    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        s = self.settings
        queries = select_queries(max(s.apify_max_queries_per_actor * 4, 4))
        for key in s.enabled_apify_actors():
            spec = ACTORS[key]
            stats = {"items": 0, "jobs": 0, "cost_usd": 0.0, "runs": 0, "skipped": ""}
            self.per_actor_stats[key] = stats
            term_list = [q for q, _kind in queries][: s.apify_max_queries_per_actor]
            for term in term_list:
                limit_wanted = s.apify_max_results.get(key) or 50
                limit, max_usd = self.tracker.plan(spec, limit_wanted, self.tracker.per_actor.get(key, 0.0))
                if limit < 1:
                    stats["skipped"] = "budget exhausted"
                    result.errors.append(f"apify:{key}: skipped, budget exhausted (spent ${self.tracker.spent_usd:.4f} of ${self.tracker.approved_usd:.2f})")
                    break
                payload = {**spec.build_input(term, limit), **s.apify_input_json.get(key, {})}
                try:
                    items, cost, status = self._run_actor(spec, payload, limit, max_usd, deadline)
                except ApifyStartRejected as exc:
                    # Every token was refused before a run existed: nothing was started, so nothing is charged. Stop this actor.
                    result.errors.append(f"apify:{key} '{term}': {self._redact(str(exc))}")
                    stats["skipped"] = "all Apify tokens refused (no run started, no cost)"
                    break
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"apify:{key} '{term}': {type(exc).__name__}: {self._redact(str(exc))[:200]}")
                    # FAIL SAFE: after an error we cannot know whether a paid run was started (e.g. the answer was lost).
                    # Count the most it could have cost and do NOT start another run for this actor - no automatic retry.
                    self.tracker.record(key, max_usd)
                    stats["cost_usd"] = round(stats["cost_usd"] + max_usd, 5)
                    stats["skipped"] = "stopped after an error (no retry of paid runs)"
                    break
                result.requests += 1
                cost = cost if cost > 0 else min(max_usd, spec.price_per_run + spec.price_per_result * len(items))   # conservative fallback
                self.tracker.record(key, cost)
                stats["runs"] += 1
                stats["items"] += len(items)
                stats["cost_usd"] = round(stats["cost_usd"] + cost, 5)
                if status != "SUCCEEDED":
                    result.errors.append(f"apify:{key} '{term}': run ended as {status}")
                for it in items:
                    result.raw_count += 1
                    job = spec.adapter(it) if isinstance(it, dict) else None
                    if job:
                        job.raw["apify_actor"] = spec.actor_id
                        result.jobs.append(job)
                        stats["jobs"] += 1
        result.cost_usd = round(self.tracker.spent_usd, 5)
        result.note = "; ".join(f"{ACTORS[k].label}: {v['items']} items, ${v['cost_usd']:.4f}" for k, v in self.per_actor_stats.items())
