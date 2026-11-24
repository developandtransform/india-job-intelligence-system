"""Naukri / LinkedIn / Indeed / Glassdoor / Google Jobs through the open-source JobSpy library (MIT).

Each (site, query) is a separate call so one blocked site never affects the others. Searches are India-wide.
JobSpy scrapes public pages; it is not an official API and can be rate-limited or blocked (especially from
datacentre IPs such as GitHub Actions). Failures are recorded, never fatal.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date

from config.profile import SEARCH_QUERIES
from connectors.base import Connector, ConnectorResult
from core.models import Job

log = logging.getLogger("jobspy")

SUPPORTED = {"naukri", "indeed", "linkedin", "glassdoor", "google"}
_INTERVAL = {"yearly": "year", "monthly": "month", "weekly": "week", "daily": "day", "hourly": "hour"}


def select_queries(max_queries: int, today: date | None = None) -> list[tuple[str, str]]:
    """Rotate through the query list so that all queries are covered over a few days."""
    qs = SEARCH_QUERIES
    if max_queries >= len(qs):
        return list(qs)
    ordinal = (today or date.today()).toordinal()
    start = (ordinal * max_queries) % len(qs)
    return [qs[(start + i) % len(qs)] for i in range(max_queries)]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def row_to_job(row: dict, site: str, kind_hint: str) -> Job | None:
    """Map one JobSpy DataFrame row to the common schema."""
    title = _clean(row.get("title"))
    company = _clean(row.get("company"))
    if not title or not company:
        return None
    job_url = _clean(row.get("job_url")) or ""
    direct = _clean(row.get("job_url_direct")) or ""
    loc = _clean(row.get("location"))
    location = ""
    if isinstance(loc, dict):
        location = ", ".join(str(x) for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x)
    elif loc:
        location = str(loc)
    job_types = _clean(row.get("job_type"))
    emp = ", ".join(job_types) if isinstance(job_types, (list, tuple)) else (str(job_types) if job_types else "")
    j = Job(
        original_id=str(_clean(row.get("id")) or job_url),
        source=site,
        title=str(title),
        company=str(company),
        location=location,
        description=str(_clean(row.get("description")) or ""),
        job_url=job_url,
        apply_url=direct or job_url,
        posted_date=_clean(row.get("date_posted")) or "",
        employment_type=emp,
        job_kind="Internship" if kind_hint == "internship" else "",
        hint_country="India",
        hint_remote=_clean(row.get("is_remote")),
        company_website=str(_clean(row.get("company_url_direct")) or _clean(row.get("company_url")) or ""),
        source_scopes=[],
    )
    j.full_description = j.description
    # structured experience (Naukri)
    exp = _clean(row.get("experience_range"))
    if exp:
        j.hint_exp_text = str(exp)
    # structured salary
    lo, hi = _clean(row.get("min_amount")), _clean(row.get("max_amount"))
    if lo or hi:
        interval = _INTERVAL.get(str(_clean(row.get("interval")) or "").lower(), "")
        j.hint_salary = {"min": lo, "max": hi, "currency": str(_clean(row.get("currency")) or "INR"),
                         "period": interval, "basis": "Platform-CTC" if site == "naukri" and interval == "year" else "Unspecified",
                         "text": f"{site} salary field", "source": site}
    # company hints
    hints = {}
    emps = _clean(row.get("company_num_employees"))
    if emps:
        hints["employees"] = str(emps)
    if _clean(row.get("company_industry")):
        hints["industry"] = str(row.get("company_industry"))
    j.hint_company = hints
    j.raw = {k: str(_clean(row.get(k)))[:200] for k in ("site", "job_level", "company_industry", "skills") if _clean(row.get(k)) is not None}
    return j


class JobSpyConnector(Connector):
    name = "jobspy"

    def enabled(self) -> bool:
        return self.settings.enable_jobspy

    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        try:
            from jobspy import scrape_jobs
        except ImportError as exc:  # pragma: no cover
            result.errors.append(f"python-jobspy is not installed: {exc}")
            return
        s = self.settings
        sites = [x for x in s.jobspy_sites if x in SUPPORTED]
        queries = select_queries(s.jobspy_max_queries_per_run)
        log.info("JobSpy: %d sites x %d queries", len(sites), len(queries))
        proxies = s.jobspy_proxies or None
        site_failures: dict[str, int] = {}
        for term, kind in queries:
            for site in sites:
                if time.monotonic() > deadline:
                    result.errors.append("jobspy: time budget reached")
                    return
                if site_failures.get(site, 0) >= 3:      # site is blocking us: stop hammering it today
                    continue
                kwargs = dict(site_name=[site], search_term=term, location="India", results_wanted=s.jobspy_results_per_query,
                              hours_old=s.jobspy_hours_old, country_indeed="india", description_format="markdown",
                              verbose=0, proxies=proxies)
                if site == "naukri":
                    kwargs["location"] = None
                if site == "google":
                    kwargs["google_search_term"] = f"{term} jobs in India since yesterday"
                if site == "linkedin":
                    kwargs["fetch_description"] = s.jobspy_linkedin_fetch_description
                if kind == "internship" and site in ("naukri", "indeed", "linkedin"):
                    kwargs["job_type"] = "internship"
                try:
                    df = scrape_jobs(**{k: v for k, v in kwargs.items() if v is not None})
                except Exception as exc:  # noqa: BLE001
                    site_failures[site] = site_failures.get(site, 0) + 1
                    result.errors.append(f"{site} '{term}': {type(exc).__name__}: {str(exc)[:160]}")
                    continue
                result.requests += 1
                errs = getattr(df, "attrs", {}).get("errors", {})
                if errs:
                    site_failures[site] = site_failures.get(site, 0) + 1
                    result.errors.append(f"{site} '{term}': {str(errs)[:200]}")
                for row in df.to_dict("records"):
                    result.raw_count += 1
                    job = row_to_job(row, str(row.get("site") or site), kind)
                    if job:
                        result.jobs.append(job)
                time.sleep(max(self.settings.request_delay_seconds, 1.0))
