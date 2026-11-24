"""Generic career-page connector based on schema.org ``JobPosting`` JSON-LD (the markup Google Jobs requires).

Given the listing/landing URL of a company career site it
1. checks robots.txt (and refuses disallowed paths),
2. reads JobPosting JSON-LD directly from the page, and
3. follows up to ``MAX_DETAIL_PAGES`` same-site links that look like job pages and reads their JSON-LD.

It only works on sites that publish JSON-LD; JavaScript-only career sites that render everything client-side will
simply yield nothing (reported in Source Statistics). No logins, no CAPTCHA handling, polite rate limiting.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.robotparser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from connectors.ats_connector import load_targets
from connectors.base import Connector, ConnectorResult, run_with_deadline
from core.http import USER_AGENT, HttpClient
from core.models import Job
from core.textutil import html_to_text, to_iso_date

log = logging.getLogger("career")
MAX_DETAIL_PAGES = 40
JOB_LINK_RE = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|requisition|opportunit(?:y|ies))/[^/?#]+", re.I)


def _iter_jsonld(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "@graph" in node:
                    stack.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
                if node.get("itemListElement"):
                    stack.extend(node["itemListElement"])
                if "item" in node and isinstance(node["item"], dict):
                    stack.append(node["item"])
                t = node.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    yield node
            elif isinstance(node, list):
                stack.extend(node)


def _loc_text(node: dict) -> str:
    locs = node.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    parts = []
    for l in locs:
        addr = (l or {}).get("address", {}) if isinstance(l, dict) else {}
        if isinstance(addr, str):
            parts.append(addr)
            continue
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x))
    return "; ".join(p for p in parts if p)


def _salary(node: dict) -> dict | None:
    bs = node.get("baseSalary")
    if not isinstance(bs, dict):
        return None
    val = bs.get("value") if isinstance(bs.get("value"), dict) else {}
    lo, hi = val.get("minValue"), val.get("maxValue")
    if lo is None and hi is None and isinstance(bs.get("value"), (int, float)):
        lo = bs["value"]
    unit = str(val.get("unitText", "")).lower()
    period = {"year": "year", "month": "month", "week": "week", "day": "day", "hour": "hour"}.get(unit, "")
    return {"min": lo, "max": hi, "currency": bs.get("currency"), "period": period, "basis": "Unspecified",
            "text": "JSON-LD baseSalary", "source": "career_page"} if (lo or hi) else None


def posting_to_job(node: dict, page_url: str, company_fallback: str) -> Job | None:
    title = html_to_text(str(node.get("title") or ""))
    if not title:
        return None
    org = node.get("hiringOrganization")
    company = (org.get("name") if isinstance(org, dict) else org) or company_fallback
    url = node.get("url") or page_url
    ident = node.get("identifier")
    oid = (ident.get("value") if isinstance(ident, dict) else ident) or url
    emp = node.get("employmentType")
    emp = ", ".join(emp) if isinstance(emp, list) else (emp or "")
    exp = node.get("experienceRequirements")
    exp_text = exp.get("monthsOfExperience") if isinstance(exp, dict) else exp
    j = Job(source="career_page", original_id=str(oid), title=title, company=str(company), location=_loc_text(node),
            description=html_to_text(node.get("description")), job_url=url, apply_url=url, employment_type=str(emp).replace("_", " ").title(),
            posted_date=to_iso_date(node.get("datePosted")), deadline=to_iso_date(node.get("validThrough")),
            source_scopes=[f"career_page:{urlparse(page_url).netloc}"])
    j.full_description = j.description
    if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        j.hint_remote = True
        reqs = node.get("applicantLocationRequirements")
        reqs = reqs if isinstance(reqs, list) else [reqs] if reqs else []
        names = " ".join(str(r.get("name", "")) for r in reqs if isinstance(r, dict))
        if names and not j.location:
            j.location = "Remote - " + names
    if isinstance(exp, dict) and exp.get("monthsOfExperience") is not None:
        try:
            months = float(exp["monthsOfExperience"])
            j.hint_exp_min = j.hint_exp_max = round(months / 12, 2)
        except (TypeError, ValueError):
            pass
    elif isinstance(exp_text, str) and exp_text:
        j.hint_exp_text = exp_text
    j.hint_salary = _salary(node)
    return j


class CareerPageConnector(Connector):
    name = "career_pages"

    def __init__(self, settings, http: HttpClient, pages: list[str] | None = None) -> None:
        super().__init__(settings, http)
        self.pages = pages if pages is not None else [*load_targets([])[1], *settings.career_page_urls_extra]
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def enabled(self) -> bool:
        return self.settings.enable_career_pages and bool(self.pages)

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self.http.get(f"{origin}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None          # no robots.txt = no restriction
            except Exception:  # noqa: BLE001
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def read_page(self, url: str, result: ConnectorResult) -> tuple[list[Job], str]:
        if not self.allowed(url):
            result.errors.append(f"robots.txt disallows {url}; skipped")
            return [], ""
        resp = self.http.get(url, headers={"Accept": "text/html"})
        result.requests += 1
        resp.raise_for_status()
        company = urlparse(url).netloc.removeprefix("www.").removeprefix("careers.").split(".")[0].title()
        jobs = [j for n in _iter_jsonld(resp.text) if (j := posting_to_job(n, url, company))]
        return jobs, resp.text

    def fetch_page(self, url: str, result: ConnectorResult, deadline: float) -> None:
        jobs, html = self.read_page(url, result)
        if not jobs and html:
            base = urlparse(url).netloc
            soup = BeautifulSoup(html, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0]
                if urlparse(href).netloc == base and JOB_LINK_RE.search(urlparse(href).path) and href != url and href not in links:
                    links.append(href)
            run_with_deadline(links[:MAX_DETAIL_PAGES], lambda l: jobs.extend(self.read_page(l, result)[0]), deadline, result.errors, "detail")
        result.raw_count += len(jobs)
        result.jobs.extend(jobs)
        # completeness cannot be proven (detail pages are capped), so career pages never close jobs; staleness does
        if not jobs:
            result.errors.append(f"{url}: no JobPosting JSON-LD found (the site may render jobs with JavaScript)")

    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        run_with_deadline(self.pages, lambda u: self.fetch_page(u, result, deadline), deadline, result.errors, "page")
