"""Turns a connector's raw ``Job`` into a clean, enriched record (text cleaning, location, kind, sections)."""
from __future__ import annotations

import re

from core.models import Job
from core.textutil import canonical_url, html_to_text, safe_url, short_hash, to_iso_date
from processing.geo_india import india_eligibility, parse_location, work_arrangement

INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|summer analyst|summer associate|trainee intern)\b", re.I)
OTHER_TYPE_RE = re.compile(r"\b(part[- ]?time|contract(?:or)?|freelance|temporary|temp\b|consultant contract|per diem|"
                           r"c2h|contract[- ]to[- ]hire|volunteer)\b", re.I)
FULLTIME_RE = re.compile(r"\b(full[- ]?time|permanent|regular)\b", re.I)

_SECTION_HEADS = {
    "resp": r"(?:key\s+)?responsibilit(?:y|ies)|what you(?:'|’)ll do|what you will do|role overview|job duties|duties|your role|day[- ]to[- ]day",
    "req": r"(?:required\s+)?(?:skills|qualifications?|requirements?)|who you are|what we(?:'|’)re looking for|you should have|must[- ]have|"
           r"eligibility|desired candidate profile",
    "pref": r"preferred(?: skills| qualifications)?|good to have|nice to have|bonus points|plus points|added advantage",
}
_HEAD_RE = re.compile(rf"^\s*(?:[-*•#]+\s*)?(?P<h>{'|'.join('(?:' + v + ')' for v in _SECTION_HEADS.values())})\s*[:\-]?\s*$", re.I | re.M)


def detect_kind(title: str, employment_type: str, text_head: str, hinted: str = "") -> str:
    if hinted in ("Internship", "Full-time"):
        base = hinted
    else:
        base = ""
    et = employment_type or ""
    if INTERN_RE.search(title or "") or re.search(r"intern", et, re.I):
        return "Internship"
    if base == "Internship":
        return "Internship"
    if OTHER_TYPE_RE.search(et) or OTHER_TYPE_RE.search(title or ""):
        if not FULLTIME_RE.search(et):
            return "Other"
    if base:
        return base
    if FULLTIME_RE.search(et) or not et:
        # description saying "internship" counts only when the title does not say otherwise and text is explicit
        if re.search(r"\bthis is (?:a|an) (?:paid |unpaid )?internship\b|\binternship (?:duration|program)\b", text_head or "", re.I):
            return "Internship"
        return "Full-time"
    return "Full-time"


def split_sections(description: str) -> dict[str, str]:
    """Heuristically cut responsibilities / required / preferred sections out of a description."""
    out = {"resp": "", "req": "", "pref": ""}
    if not description:
        return out
    marks = []
    for m in _HEAD_RE.finditer(description):
        h = m.group("h").lower()
        key = "pref" if re.search(_SECTION_HEADS["pref"], h, re.I) else "resp" if re.search(_SECTION_HEADS["resp"], h, re.I) else "req"
        marks.append((m.start(), m.end(), key))
    for i, (st, en, key) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else min(len(description), en + 1500)
        chunk = description[en:stop].strip()[:1500]
        if chunk and not out[key]:
            out[key] = chunk
    return out


def normalize_job(job: Job, today: str) -> Job:
    """Clean text, parse location/kind/dates and compute keys. Mutates and returns ``job``."""
    job.title = re.sub(r"\s+", " ", html_to_text(job.title)).strip()
    job.company = re.sub(r"\s+", " ", html_to_text(job.company)).strip()
    job.location = re.sub(r"\s+", " ", html_to_text(job.location)).strip(" ,;-")
    job.full_description = html_to_text(job.full_description or job.description)
    job.description = job.full_description
    invalid = any(u and str(u).strip() and not safe_url(u) for u in (job.job_url, job.apply_url))
    job.job_url = safe_url(job.job_url)
    job.apply_url = safe_url(job.apply_url) or job.job_url
    if invalid:
        job.raw["invalid_url_dropped"] = True
    job.source_urls = [u for u in (safe_url(x) for x in job.source_urls) if u]
    job.posted_date = to_iso_date(job.posted_date)
    job.deadline = to_iso_date(job.deadline)
    job.date_discovered = job.date_discovered or today
    job.employment_type = (job.employment_type or "").strip()

    head = job.full_description[:2500]
    job.job_kind = detect_kind(job.title, job.employment_type, head, job.job_kind)

    geo = parse_location(job.location, job.title)
    job.city, job.state, job.country = geo.city, geo.state, geo.country
    elig, _why = india_eligibility(geo, job.full_description, job.hint_country, job.hint_remote)
    job.india_remote = elig
    job.work_arrangement = work_arrangement(geo, head if job.hint_remote is None else ("remote " + head if job.hint_remote else head))
    if job.hint_remote and job.work_arrangement == "On-site":
        job.work_arrangement = "Remote"
    if elig == "Yes" and not job.country:
        job.country = "India"

    sec = split_sections(job.full_description)
    job.responsibilities = job.responsibilities or sec["resp"]
    job.required_skills = job.required_skills or sec["req"]
    job.preferred_skills = job.preferred_skills or sec["pref"]

    # keys used by de-duplication / history
    if job.original_id and job.source:
        key = f"{job.source}:{job.original_id}"
        if key not in job.source_keys:
            job.source_keys.append(key)
    for u in (job.job_url, job.apply_url):
        if u and u not in job.source_urls:
            job.source_urls.append(u)
    if not job.internal_id:
        job.internal_id = short_hash(job.source, job.original_id or canonical_url(job.job_url), job.title, job.company)
    return job
