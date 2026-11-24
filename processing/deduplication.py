"""Duplicate detection across sources.

Two postings are the *same opening* when ANY of these holds:
1. they share a source key (``source:original_id``) or a canonical URL;
2. same company AND same city AND very similar title AND (similar description OR identical title).

Different locations => different openings, even with an identical title and company.
Different description (low similarity on long texts) => different openings (e.g. two teams hiring the same title).
Reposts (a new posting that re-uses a closed one) are handled in ``lifecycle.py``.
"""
from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from config.profile import AGGREGATOR_DOMAINS
from core.models import Job
from core.textutil import canonical_url, norm_company, norm_text
from urllib.parse import urlparse

TITLE_SIM = 90
TITLE_SIM_NO_LOCATION = 95
DESC_SAME = 80
DESC_DIFFERENT = 50


def _is_aggregator_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in AGGREGATOR_DOMAINS)


def job_urls(j: Job) -> set[str]:
    return {canonical_url(u) for u in [j.job_url, j.apply_url, *j.source_urls] if u}


def same_posting(a: Job, b: Job) -> bool:
    if set(a.source_keys) & set(b.source_keys):
        return True
    if job_urls(a) & job_urls(b):
        return True
    ca, cb = norm_company(a.company), norm_company(b.company)
    if not ca or not cb or (ca != cb and fuzz.ratio(ca, cb) < 92):
        return False
    ta, tb = norm_text(a.title), norm_text(b.title)
    if not ta or not tb:
        return False
    city_a, city_b = (a.city or "").lower(), (b.city or "").lower()
    if city_a and city_b and city_a != city_b:
        return False
    if a.state and b.state and not (city_a and city_b) and a.state.lower() != b.state.lower():
        return False
    if a.job_kind and b.job_kind and a.job_kind != b.job_kind:
        return False
    loc_known = bool(city_a and city_b) or (norm_text(a.location) == norm_text(b.location) and norm_text(a.location) != "")
    title_sim = fuzz.token_sort_ratio(ta, tb)
    if title_sim < (TITLE_SIM if loc_known else TITLE_SIM_NO_LOCATION):
        return False
    da, db = (a.full_description or a.description or "")[:1500], (b.full_description or b.description or "")[:1500]
    if len(da) >= 200 and len(db) >= 200:
        sim = fuzz.ratio(norm_text(da), norm_text(db))
        if sim >= DESC_SAME:
            return True
        if sim < DESC_DIFFERENT:
            return False
        return title_sim == 100 and loc_known
    return title_sim == 100 and loc_known


class JobIndex:
    """Index of jobs for fast candidate lookup."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.by_key: dict[str, Job] = {}
        self.by_url: dict[str, Job] = {}
        self.by_company: dict[str, list[Job]] = defaultdict(list)
        for j in jobs or []:
            self.add(j)

    def add(self, j: Job) -> None:
        for k in j.source_keys:
            self.by_key[k] = j
        for u in job_urls(j):
            self.by_url[u] = j
        self.by_company[norm_company(j.company)].append(j)

    def find(self, j: Job) -> Job | None:
        for k in j.source_keys:
            if k in self.by_key:
                return self.by_key[k]
        for u in job_urls(j):
            if u in self.by_url:
                return self.by_url[u]
        for cand in self.by_company.get(norm_company(j.company), []):
            if cand is not j and same_posting(j, cand):
                return cand
        # tolerate small company spelling differences
        key = norm_company(j.company)
        for other_key, lst in self.by_company.items():
            if other_key != key and other_key and key and fuzz.ratio(key, other_key) >= 92:
                for cand in lst:
                    if same_posting(j, cand):
                        return cand
        return None


def completeness(j: Job) -> int:
    score = 0
    if j.apply_url and not _is_aggregator_url(j.apply_url):
        score += 4
    score += min(3, len(j.full_description or j.description or "") // 700)
    if j.hint_salary:
        score += 1
    if j.posted_date:
        score += 1
    if j.source and j.source.split(",")[0].split(":")[0] in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable",
                                                "recruitee", "personio", "teamtailor", "breezy", "oracle", "career_page"):
        score += 2
    return score


def sources_label(j: Job) -> str:
    seen: list[str] = []
    for k in j.source_keys:
        s = k.split(":", 1)[0]
        if s not in seen:
            seen.append(s)
    if j.source and j.source not in seen:
        seen.insert(0, j.source)
    if j.source in seen:
        seen.remove(j.source)
        seen.insert(0, j.source)
    return ", ".join(seen)


def merge_into(primary: Job, other: Job) -> Job:
    """Fold ``other`` into ``primary`` (primary keeps identity). Returns primary."""
    for u in other.source_urls:
        if u not in primary.source_urls:
            primary.source_urls.append(u)
    for k in other.source_keys:
        if k not in primary.source_keys:
            primary.source_keys.append(k)
    for sc in other.source_scopes:
        if sc not in primary.source_scopes:
            primary.source_scopes.append(sc)
    # best direct apply URL: prefer a non-aggregator link
    for cand in (other.apply_url, other.job_url):
        if cand and (not primary.apply_url or (_is_aggregator_url(primary.apply_url) and not _is_aggregator_url(cand))):
            primary.apply_url = cand
    if not primary.job_url:
        primary.job_url = other.job_url
    # fill gaps; take the longer description (and mark for re-extraction when it changes)
    od = other.full_description or other.description
    pd = primary.full_description or primary.description
    if len(od) > len(pd) * 1.15 + 50:
        primary.full_description = primary.description = od
        primary.dirty = True
    for attr in ("location", "city", "state", "country", "employment_type", "company_website", "responsibilities",
                 "required_skills", "preferred_skills", "deadline"):
        if not getattr(primary, attr) and getattr(other, attr):
            setattr(primary, attr, getattr(other, attr))
    if other.posted_date and (not primary.posted_date or other.posted_date < primary.posted_date):
        primary.posted_date = other.posted_date
    if other.hint_salary and not primary.hint_salary:
        primary.hint_salary = other.hint_salary
        primary.dirty = True
    for k, v in (other.hint_company or {}).items():
        primary.hint_company.setdefault(k, v)
    if (other.hint_exp_text or other.hint_exp_min is not None or other.hint_exp_max is not None) and not (
            primary.hint_exp_text or primary.hint_exp_min is not None or primary.hint_exp_max is not None):
        primary.hint_exp_text, primary.hint_exp_min, primary.hint_exp_max = other.hint_exp_text, other.hint_exp_min, other.hint_exp_max
        primary.dirty = True
    if other.hint_country and not primary.hint_country:
        primary.hint_country = other.hint_country
    if other.hint_remote is not None and primary.hint_remote is None:
        primary.hint_remote = other.hint_remote
    for k, v in other.raw.items():
        primary.raw.setdefault(k, v)
    return primary


def dedupe_batch(jobs: list[Job]) -> list[Job]:
    """Collapse duplicates inside one run's batch. The most complete record becomes the primary."""
    ordered = sorted(jobs, key=completeness, reverse=True)
    out: list[Job] = []
    idx = JobIndex()
    for j in ordered:
        match = idx.find(j)
        if match:
            merge_into(match, j)
            idx.add(match)
        else:
            out.append(j)
            idx.add(j)
    return out
