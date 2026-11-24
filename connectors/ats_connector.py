"""Applicant-tracking-system connectors that use each vendor's PUBLIC job-board endpoint (no login, no key).

Implemented: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Personio, Teamtailor, Breezy HR,
Workday (CXS public search) and Oracle Recruiting Cloud.
NOT implemented (no uniform public listing API): SAP SuccessFactors, iCIMS, Jobvite, Zoho Recruit - for those, add the
company's career-page URL to ``career_pages`` and the generic JSON-LD connector will try it.

Endpoint shapes follow the vendors' public documentation and the MIT-licensed GetAnything-1/ats-jobs-scraper; the code is
original. Each board is fetched independently; one failing board never affects another.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml

from config.settings import PROJECT_ROOT
from connectors.base import Connector, ConnectorResult, run_with_deadline
from core.http import HttpClient
from core.models import Job
from core.textutil import html_to_text, to_iso_date
from processing.geo_india import parse_location

log = logging.getLogger("ats")

TARGETS_PATH = PROJECT_ROOT / "config" / "targets.yaml"
SUPPORTED_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "personio",
                 "teamtailor", "breezy", "workday", "oracle")
WORKDAY_PAGE = 20          # Workday silently returns nothing above 20 per page
WORKDAY_MAX_JOBS = 400
WORKDAY_DETAIL_CAP = 40
SMARTRECRUITERS_DETAIL_CAP = 40
ORACLE_PAGE = 200
ORACLE_MAX_JOBS = 2000


# ------------------------------------------------------------------ helpers
def parse_target(spec: str) -> tuple[str, str, str] | None:
    """'greenhouse:stripe|Stripe Inc' -> ('greenhouse', 'stripe', 'Stripe Inc')."""
    if ":" not in spec:
        return None
    ats, rest = spec.split(":", 1)
    ats = ats.strip().lower()
    ident, _, name = rest.partition("|")
    if ats not in SUPPORTED_ATS or not ident.strip():
        return None
    return ats, ident.strip(), name.strip()


def load_targets(extra: list[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    boards: list[tuple[str, str, str]] = []
    pages: list[str] = []
    try:
        data = yaml.safe_load(TARGETS_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Could not read config/targets.yaml: %s", exc)
        data = {}
    for spec in [*(data.get("ats_boards") or []), *extra]:
        t = parse_target(str(spec))
        if t and t not in boards:
            boards.append(t)
        elif not t:
            log.warning("Ignoring unrecognised ATS target %r", spec)
    pages = [str(u) for u in (data.get("career_pages") or [])]
    return boards, pages


def _pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _safe_xml(text: str) -> ET.Element:
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise ValueError("XML with DOCTYPE/ENTITY declarations is refused")
    return ET.fromstring(text)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, name: str) -> str:
    for c in el.iter():
        if _local(c.tag) == name.lower() and c is not el:
            return (c.text or "").strip()
    return ""


def _prefilter(job: Job, result: ConnectorResult) -> bool:
    """Drop jobs whose location is clearly outside India (they can never be eligible)."""
    geo = parse_location(job.location, job.title)
    if geo.is_india is False and not re.search(r"\bindia\b", job.full_description[:1500], re.I):
        result.dropped_prefilter += 1
        return False
    return True


def _job(ats: str, ident: str, company: str, **kw) -> Job:
    j = Job(source=ats, company=company, source_scopes=[f"{ats}:{ident}"], **kw)
    j.full_description = j.description
    return j


def workday_posted(text: str | None, today: date | None = None) -> str:
    today = today or date.today()
    t = (text or "").lower()
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""


# ------------------------------------------------------------------ parsers (pure functions, unit-tested)
def parse_greenhouse(payload: dict, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload.get("jobs", []) or []:
        loc = (r.get("location") or {}).get("name") or ""
        j = _job("greenhouse", ident, company, original_id=str(r.get("id", "")), title=r.get("title", ""),
                 location=loc, job_url=r.get("absolute_url", ""), apply_url=r.get("absolute_url", ""),
                 description=html_to_text(r.get("content")), posted_date=to_iso_date(r.get("first_published") or r.get("updated_at")))
        deps = ", ".join(d.get("name", "") for d in r.get("departments", []) if d.get("name"))
        if deps:
            j.raw["department"] = deps
        out.append(j)
    return out


def parse_lever(payload: list, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload if isinstance(payload, list) else []:
        cats = r.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")] if (cats.get("allLocations") or cats.get("location")) else []
        j = _job("lever", ident, company, original_id=str(r.get("id", "")), title=r.get("text", ""),
                 location="; ".join(x for x in locs if x), job_url=r.get("hostedUrl", ""), apply_url=r.get("applyUrl") or r.get("hostedUrl", ""),
                 description=r.get("descriptionPlain") or html_to_text(r.get("description")),
                 employment_type=cats.get("commitment") or "", posted_date=to_iso_date(r.get("createdAt")))
        wt = (r.get("workplaceType") or "").lower()
        j.hint_remote = True if wt == "remote" else None
        sr = r.get("salaryRange") or {}
        if sr.get("min") or sr.get("max"):
            j.hint_salary = {"min": sr.get("min"), "max": sr.get("max"), "currency": sr.get("currency"),
                             "period": {"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour"}.get(sr.get("interval"), (sr.get("interval") or "").replace("per-", "")),
                             "basis": "Unspecified", "text": "lever salaryRange", "source": "lever"}
        out.append(j)
    return out


def parse_ashby(payload: dict, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload.get("jobs", []) or []:
        if r.get("isListed") is False:
            continue
        locs = [r.get("location"), *[(l.get("location") if isinstance(l, dict) else l) for l in r.get("secondaryLocations", []) or []]]
        j = _job("ashby", ident, company, original_id=str(r.get("id", "")), title=r.get("title", ""),
                 location="; ".join(x for x in locs if isinstance(x, str) and x), job_url=r.get("jobUrl", ""),
                 apply_url=r.get("applyUrl") or r.get("jobUrl", ""), description=r.get("descriptionPlain") or html_to_text(r.get("descriptionHtml")),
                 employment_type=r.get("employmentType") or "", posted_date=to_iso_date(r.get("publishedAt")))
        j.hint_remote = True if (r.get("isRemote") or str(r.get("workplaceType", "")).lower() == "remote") else None
        comp = r.get("compensation") or {}
        comps = [c for c in comp.get("summaryComponents", []) or [] if c.get("compensationType") == "Salary"]
        if comps:
            c = comps[0]
            j.hint_salary = {"min": c.get("minValue"), "max": c.get("maxValue"), "currency": c.get("currencyCode"),
                             "period": {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour"}.get(str(c.get("interval", "")).upper(), ""),
                             "basis": "Unspecified", "text": comp.get("compensationTierSummary") or "", "source": "ashby"}
        out.append(j)
    return out


def parse_workable(payload: dict, ident: str, company: str) -> list[Job]:
    out = []
    name = payload.get("name") or company
    for r in payload.get("jobs", []) or []:
        loc = ", ".join(x for x in (r.get("city"), r.get("state"), r.get("country")) if x)
        j = _job("workable", ident, name, original_id=str(r.get("shortcode") or r.get("id", "")), title=r.get("title", ""),
                 location=loc, job_url=r.get("url") or r.get("shortlink", ""), apply_url=r.get("application_url") or r.get("url", ""),
                 description=html_to_text(r.get("description")), employment_type=r.get("employment_type") or "",
                 posted_date=to_iso_date(r.get("published_on") or r.get("created_at")))
        j.hint_remote = True if r.get("telecommuting") else None
        out.append(j)
    return out


def parse_recruitee(payload: dict, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload.get("offers", []) or []:
        loc = r.get("location") or ", ".join(x for x in (r.get("city"), r.get("country")) if x)
        desc = html_to_text(r.get("description")) + "\n" + html_to_text(r.get("requirements"))
        j = _job("recruitee", ident, r.get("company_name") or company, original_id=str(r.get("id", "")), title=r.get("title", ""),
                 location=loc, job_url=r.get("careers_url", ""), apply_url=r.get("careers_apply_url") or r.get("careers_url", ""),
                 description=desc.strip(), employment_type=r.get("employment_type_code") or "", posted_date=to_iso_date(r.get("published_at") or r.get("created_at")))
        j.hint_remote = True if r.get("remote") else None
        sal = r.get("salary") or {}
        if sal.get("min") or sal.get("max"):
            j.hint_salary = {"min": sal.get("min"), "max": sal.get("max"), "currency": sal.get("currency"),
                             "period": sal.get("period") or "", "basis": "Unspecified", "text": "recruitee salary", "source": "recruitee"}
        out.append(j)
    return out


_PERSONIO_EXP = {"lt-1": "0-1 years", "1-2": "1-2 years", "2-5": "2-5 years", "5-7": "5-7 years", "7-10": "7-10 years",
                 "10-15": "10-15 years", "gt-15": "15+ years"}


def parse_personio(xml_text: str, ident: str, company: str) -> list[Job]:
    root = _safe_xml(xml_text)
    out = []
    for pos in root.iter():
        if _local(pos.tag) != "position":
            continue
        pid = _child_text(pos, "id")
        desc_parts = []
        for jd in pos.iter():
            if _local(jd.tag) == "jobdescription":
                desc_parts.append(_child_text(jd, "name") + "\n" + html_to_text(_child_text(jd, "value")))
        host = f"https://{ident}.jobs.personio.de" if not ident.startswith("http") else ident.rsplit("/xml", 1)[0]
        j = _job("personio", ident, company, original_id=pid, title=_child_text(pos, "name"), location=_child_text(pos, "office"),
                 job_url=f"{host}/job/{pid}", apply_url=f"{host}/job/{pid}", description="\n\n".join(desc_parts),
                 employment_type=_child_text(pos, "employmenttype") or _child_text(pos, "schedule"), posted_date=to_iso_date(_child_text(pos, "createdat")))
        exp = _PERSONIO_EXP.get(_child_text(pos, "yearsofexperience"))
        if exp:
            j.hint_exp_text = exp
        out.append(j)
    return out


def parse_teamtailor(xml_text: str, ident: str, company: str) -> list[Job]:
    root = _safe_xml(xml_text)
    chan_title = ""
    for el in root.iter():
        if _local(el.tag) == "title":
            chan_title = (el.text or "").strip()
            break
    out = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        locs = []
        for loc in item.iter():
            if _local(loc.tag) == "location":
                parts = [_child_text(loc, "city"), _child_text(loc, "country")]
                locs.append(", ".join(p for p in parts if p) or _child_text(loc, "name"))
        link = _child_text(item, "link")
        j = _job("teamtailor", ident, chan_title or company, original_id=_child_text(item, "guid") or link, title=_child_text(item, "title"),
                 location="; ".join(l for l in locs if l), job_url=link, apply_url=link, description=html_to_text(_child_text(item, "description")),
                 posted_date=to_iso_date(_child_text(item, "pubdate")))
        j.hint_remote = True if _child_text(item, "remotestatus").lower() in ("fully", "remote") else None
        out.append(j)
    return out


def parse_breezy(payload: list, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload if isinstance(payload, list) else []:
        loc = r.get("location") or {}
        j = _job("breezy", ident, (r.get("company") or {}).get("name") or company, original_id=str(r.get("id") or r.get("friendly_id", "")),
                 title=r.get("name", ""), location=loc.get("name") or ", ".join(x for x in (loc.get("city"), (loc.get("country") or {}).get("name")) if x),
                 job_url=r.get("url", ""), apply_url=r.get("url", ""), employment_type=(r.get("type") or {}).get("name", ""),
                 posted_date=to_iso_date(r.get("published_date")), description=r.get("salary") and f"Salary: {r['salary']}" or "")
        j.hint_remote = True if loc.get("is_remote") else None
        out.append(j)
    return out


def parse_smartrecruiters_list(payload: dict, ident: str, company: str) -> list[Job]:
    out = []
    for r in payload.get("content", []) or []:
        loc = r.get("location") or {}
        loc_text = ", ".join(x for x in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if x)
        pid = str(r.get("id", ""))
        url = f"https://jobs.smartrecruiters.com/{ident}/{pid}"
        j = _job("smartrecruiters", ident, (r.get("company") or {}).get("name") or company, original_id=pid, title=r.get("name", ""),
                 location=loc_text, job_url=url, apply_url=url, employment_type=(r.get("typeOfEmployment") or {}).get("label", ""),
                 posted_date=to_iso_date(r.get("releasedDate")))
        j.hint_remote = True if loc.get("remote") else None
        j.raw["detail_ref"] = r.get("ref", "")
        out.append(j)
    return out


def parse_smartrecruiters_detail(payload: dict) -> str:
    sections = ((payload.get("jobAd") or {}).get("sections")) or {}
    parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        t = (sections.get(key) or {}).get("text")
        if t:
            parts.append(html_to_text(t))
    return "\n\n".join(parts)


def parse_workday_list(payload: dict, host: str, site: str, ident: str, company: str, today: date | None = None) -> list[Job]:
    out = []
    for r in payload.get("jobPostings", []) or []:
        path = r.get("externalPath", "")
        if not path:
            continue
        req = ""
        for b in r.get("bulletFields") or []:
            req = str(b)
            break
        url = f"https://{host}/en-US/{site}{path}"
        j = _job("workday", ident, company, original_id=req or path.rsplit("_", 1)[-1] or path, title=r.get("title", ""),
                 location=r.get("locationsText", ""), job_url=url, apply_url=url, posted_date=workday_posted(r.get("postedOn"), today))
        j.raw["detail_path"] = path
        out.append(j)
    return out


def parse_workday_detail(payload: dict) -> tuple[str, dict]:
    info = payload.get("jobPostingInfo") or {}
    extra = {"location": info.get("location") or "", "time_type": info.get("timeType") or "", "remote": info.get("remoteType") or "",
             "start": info.get("startDate") or "", "url": info.get("externalUrl") or "", "country": (info.get("country") or {}).get("descriptor") or ""}
    return html_to_text(info.get("jobDescription")), extra


def parse_oracle(payload: dict, origin: str, site: str, ident: str, company: str) -> tuple[list[Job], int]:
    items = (payload.get("items") or [{}])[0]
    total = int(items.get("TotalJobsCount") or 0)
    out = []
    for r in items.get("requisitionList", []) or []:
        rid = str(r.get("Id", ""))
        desc = "\n".join(html_to_text(r.get(k)) for k in ("ShortDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr") if r.get(k))
        url = f"{origin}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}"
        j = _job("oracle", ident, company, original_id=rid, title=r.get("Title", ""), location=r.get("PrimaryLocation") or r.get("PrimaryLocationCountry") or "",
                 job_url=url, apply_url=url, description=desc, employment_type=r.get("JobSchedule") or r.get("JobType") or "",
                 posted_date=to_iso_date(r.get("PostedDate")), deadline=to_iso_date(r.get("PostingEndDate")))
        j.hint_remote = True if re.search("remote", str(r.get("WorkplaceType") or ""), re.I) else None
        out.append(j)
    return out, total


# ------------------------------------------------------------------ network fetchers
def parse_workday_url(href: str) -> tuple[str, str, str]:
    u = urlparse(href)
    host = u.netloc
    tenant = host.split(".")[0]
    segs = [s for s in u.path.split("/") if s]
    segs = [s for s in segs if not re.fullmatch(r"[a-z]{2}[-_][A-Z]{2}", s)]
    if not segs:
        raise ValueError(f"Workday URL has no site name: {href}")
    return host, tenant, segs[0]


def parse_oracle_url(href: str) -> tuple[str, str]:
    u = urlparse(href)
    m = re.search(r"/sites/([^/?#]+)", u.path)
    if not m:
        raise ValueError("Oracle URL needs /sites/<SITE>/ in it, e.g. .../hcmUI/CandidateExperience/en/sites/CX_1/requisitions")
    return f"{u.scheme}://{u.netloc}", m.group(1)


class AtsConnector(Connector):
    name = "ats"

    def __init__(self, settings, http: HttpClient, boards: list[tuple[str, str, str]] | None = None) -> None:
        super().__init__(settings, http)
        self.boards = boards if boards is not None else load_targets(settings.ats_boards_extra)[0]

    def enabled(self) -> bool:
        return self.settings.enable_ats and bool(self.boards)

    # -- one board ---------------------------------------------------------
    def fetch_board(self, ats: str, ident: str, name: str, result: ConnectorResult, deadline: float) -> None:
        company = name or _pretty(ident.split("/")[0] if ats not in ("workday", "oracle") else urlparse(ident).netloc.split(".")[0])
        scope = f"{ats}:{ident}"
        get = self.http.get_json
        jobs: list[Job] = []
        if ats == "greenhouse":
            if not name:
                try:
                    company = get(f"https://boards-api.greenhouse.io/v1/boards/{quote(ident)}").get("name") or company
                    result.requests += 1
                except Exception:  # noqa: BLE001
                    pass
            jobs = parse_greenhouse(get(f"https://boards-api.greenhouse.io/v1/boards/{quote(ident)}/jobs?content=true"), ident, company)
            result.requests += 1
        elif ats == "lever":
            jobs = parse_lever(get(f"https://api.lever.co/v0/postings/{quote(ident)}?mode=json"), ident, company)
            result.requests += 1
        elif ats == "ashby":
            jobs = parse_ashby(get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(ident)}?includeCompensation=true"), ident, company)
            result.requests += 1
        elif ats == "workable":
            jobs = parse_workable(get(f"https://apply.workable.com/api/v1/widget/accounts/{quote(ident)}?details=true"), ident, company)
            result.requests += 1
        elif ats == "recruitee":
            jobs = parse_recruitee(get(f"https://{quote(ident)}.recruitee.com/api/offers/"), ident, company)
            result.requests += 1
        elif ats == "breezy":
            jobs = parse_breezy(get(f"https://{quote(ident)}.breezy.hr/json"), ident, company)
            result.requests += 1
        elif ats == "personio":
            url = ident if ident.startswith("http") else f"https://{quote(ident)}.jobs.personio.de/xml"
            resp = self.http.get(url)
            resp.raise_for_status()
            jobs = parse_personio(resp.text, ident, company)
            result.requests += 1
        elif ats == "teamtailor":
            resp = self.http.get(f"https://{quote(ident)}.teamtailor.com/jobs.rss")
            resp.raise_for_status()
            jobs = parse_teamtailor(resp.text, ident, company)
            result.requests += 1
        elif ats == "smartrecruiters":
            jobs = self._smartrecruiters(ident, company, result, deadline)
        elif ats == "workday":
            jobs = self._workday(ident, company, result, deadline)
        elif ats == "oracle":
            jobs = self._oracle(ident, company, result, deadline)
        result.raw_count += len(jobs)
        kept = [j for j in jobs if j.title and _prefilter(j, result)]
        result.jobs.extend(kept)
        result.completed_scopes.add(scope)          # reached only when the whole board loaded without an exception
        log.info("ATS %s: %d jobs (%d kept after India pre-filter)", scope, len(jobs), len(kept))

    def _smartrecruiters(self, ident: str, company: str, result: ConnectorResult, deadline: float) -> list[Job]:
        jobs: list[Job] = []
        offset, total = 0, None
        while total is None or offset < total:
            data = self.http.get_json(f"https://api.smartrecruiters.com/v1/companies/{quote(ident)}/postings?limit=100&offset={offset}&country=in")
            result.requests += 1
            total = int(data.get("totalFound", 0))
            batch = parse_smartrecruiters_list(data, ident, company)
            if not batch:
                break
            jobs.extend(batch)
            offset += 100
            if time.monotonic() > deadline:
                raise TimeoutError("time budget reached while paging SmartRecruiters")
        detail_budget = SMARTRECRUITERS_DETAIL_CAP
        for j in jobs:
            if detail_budget <= 0 or f"smartrecruiters:{j.original_id}" in self.known_keys:
                continue
            ref = j.raw.pop("detail_ref", "")
            if not ref:
                continue
            try:
                j.description = parse_smartrecruiters_detail(self.http.get_json(ref))
                j.full_description = j.description
                result.requests += 1
                detail_budget -= 1
            except Exception:  # noqa: BLE001 - details are a bonus, not required
                break
        return jobs

    def _workday(self, ident: str, company: str, result: ConnectorResult, deadline: float) -> list[Job]:
        host, tenant, site = parse_workday_url(ident)
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        jobs: list[Job] = []
        offset, total = 0, None
        while total is None or (offset < total and offset < WORKDAY_MAX_JOBS):
            body = {"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset, "searchText": "India"}
            data = self.http.post_json(f"{base}/jobs", json=body, headers={"Content-Type": "application/json"})
            result.requests += 1
            if total is None:
                total = int(data.get("total", 0))      # only the first page carries the total
            batch = parse_workday_list(data, host, site, ident, company)
            if not batch:
                break
            jobs.extend(batch)
            offset += WORKDAY_PAGE
            if time.monotonic() > deadline:
                raise TimeoutError("time budget reached while paging Workday")
        budget = WORKDAY_DETAIL_CAP
        for j in jobs:
            path = j.raw.pop("detail_path", "")
            if budget <= 0 or not path or f"workday:{j.original_id}" in self.known_keys:
                continue
            geo = parse_location(j.location, j.title)
            if geo.is_india is False:
                continue
            try:
                desc, extra = parse_workday_detail(self.http.get_json(f"{base}{path}"))
                j.description = j.full_description = desc
                j.employment_type = extra["time_type"]
                if extra["location"] and not j.location:
                    j.location = extra["location"]
                if re.search("remote", extra["remote"], re.I):
                    j.hint_remote = True
                if extra["url"]:
                    j.apply_url = j.job_url = extra["url"]
                result.requests += 1
                budget -= 1
            except Exception:  # noqa: BLE001
                continue
        return jobs

    def _oracle(self, ident: str, company: str, result: ConnectorResult, deadline: float) -> list[Job]:
        origin, site = parse_oracle_url(ident)
        jobs: list[Job] = []
        total = None
        while (total is None or len(jobs) < min(total, ORACLE_MAX_JOBS)):
            url = (f"{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
                   f"&finder=findReqs;siteNumber={quote(site)},limit={ORACLE_PAGE},offset={len(jobs)},sortBy=POSTING_DATES_DESC")
            batch, t = parse_oracle(self.http.get_json(url), origin, site, ident, company)
            result.requests += 1
            if total is None:
                total = t
            if not batch:
                break
            jobs.extend(batch)                       # advance by what the server returned, not by the page size
            if time.monotonic() > deadline:
                raise TimeoutError("time budget reached while paging Oracle")
        return jobs

    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        def one(board: tuple[str, str, str]) -> None:
            ats, ident, name = board
            self.fetch_board(ats, ident, name, result, deadline)
        run_with_deadline(self.boards, one, deadline, result.errors, "board")
