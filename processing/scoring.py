"""Function detection and the transparent 0-100 relevance score.

Scoring only *ranks* jobs that already passed (or are pending) the hard eligibility rules. A high score can never
turn an excluded job into an eligible one - see ``processing/eligibility.py``.

Weights (sum = 100)
-------------------
Function relevance 30 | Experience suitability 15 | Salary 15 | Location 10 | Employment type 5 |
Company quality 7 | Job-description quality 5 | Skills match 5 | Freshness 5 | Direct apply link 3
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from urllib.parse import urlparse

from config.profile import AGGREGATOR_DOMAINS, FUNCTIONS, IRRELEVANT_TITLE_PATTERNS, SKILL_KEYWORDS

WEIGHTS = {"function": 30, "experience": 15, "salary": 15, "location": 10, "employment": 5,
           "company": 7, "description": 5, "skills": 5, "freshness": 5, "apply_link": 3}
assert sum(WEIGHTS.values()) == 100

TIER1_CITIES = {"bengaluru", "bangalore", "mumbai", "navi mumbai", "thane", "delhi", "new delhi", "gurugram", "gurgaon",
                "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad"}


@dataclass
class FunctionMatch:
    name: str = ""
    strength: str = "none"        # title | description | generic | none
    core: bool = False
    irrelevant: bool = False


@lru_cache(maxsize=None)
def _kw_regex(kw: str) -> re.Pattern:
    k = kw.strip().lower()
    return re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", re.I)


_IRRELEVANT = [re.compile(p, re.I) for p in IRRELEVANT_TITLE_PATTERNS]


def function_evidence(label: str, title: str, description: str) -> list[str]:
    """Deterministic support for a function label that an AI suggested: the distinct keywords of THAT function found in the
    posting. Overlapping matches count once (the longest wins), so "digital marketing" is one piece of evidence, not two."""
    if label not in FUNCTIONS or label == "Other Management":
        return []
    text = f"{title or ''}\n{(description or '')[:6000]}".lower()
    spans: list[tuple[int, int, str]] = []
    for kw in sorted(FUNCTIONS[label][1], key=len, reverse=True):
        for m in _kw_regex(kw).finditer(text):
            if not any(m.start() < e and s_ < m.end() for s_, e, _ in spans):
                spans.append((m.start(), m.end(), kw.lower()))
    return sorted({kw for _, _, kw in spans})


def detect_function(title: str, description: str = "") -> FunctionMatch:
    t = (title or "").lower()
    irrelevant = any(rx.search(t) for rx in _IRRELEVANT)
    best: tuple[int, str, bool] | None = None
    for fname, (core, kws) in FUNCTIONS.items():
        if fname == "Other Management":
            continue
        for kw in kws:
            if _kw_regex(kw).search(t):
                score = len(kw) + (3 if core else 0)
                if best is None or score > best[0]:
                    best = (score, fname, core)
    if best:
        return FunctionMatch(best[1], "title", best[2], False)
    # description evidence
    d = (description or "")[:3500].lower()
    hits: dict[str, int] = {}
    for fname, (core, kws) in FUNCTIONS.items():
        if fname == "Other Management":
            continue
        n = sum(1 for kw in kws if _kw_regex(kw).search(d))
        if n:
            hits[fname] = n
    if hits and not irrelevant:
        fname = max(hits, key=lambda f: (hits[f], FUNCTIONS[f][0]))
        if hits[fname] >= 3:
            return FunctionMatch(fname, "description", FUNCTIONS[fname][0], False)
    if irrelevant:
        return FunctionMatch("", "none", False, True)
    if any(_kw_regex(kw).search(t) for kw in FUNCTIONS["Other Management"][1]):
        return FunctionMatch("Other Management", "generic", False, False)
    return FunctionMatch("", "none", False, False)


def _is_aggregator(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in AGGREGATOR_DOMAINS)


@dataclass
class ScoreResult:
    score: int = 0
    parts: dict[str, int] = field(default_factory=dict)
    explanation: str = ""


def score_job(job, fm: FunctionMatch, today: date) -> ScoreResult:
    """``job`` is a core.models.Job whose extraction fields are already populated."""
    parts: dict[str, int] = {}
    why: list[str] = []

    # function relevance (30)
    if fm.strength == "title":
        parts["function"] = 30 if fm.core else 24
        why.append(f"{fm.name} role (title)")
    elif fm.strength == "description":
        parts["function"] = 16 if fm.core else 12
        why.append(f"{fm.name} duties (description)")
    elif fm.strength == "generic":
        parts["function"] = 8
        why.append("generic management title")
    else:
        parts["function"] = 0

    # experience (15)
    emax = None if job.exp_max == "" else float(job.exp_max)
    emin = None if job.exp_min == "" else float(job.exp_min)
    if job.exp_status == "Eligible":
        top = emax if emax is not None else (emin or 0)
        parts["experience"] = 15 if top <= 1 else 13 if top <= 2 else 10
        why.append(f"experience {job.exp_display or 'fresher-friendly'}")
    elif job.exp_status == "Verification Required":
        parts["experience"] = 4
        why.append("experience unverified")
    else:
        parts["experience"] = 0

    # salary (15)
    if job.job_kind == "Internship":
        s = None if job.stipend_min == "" else float(job.stipend_min)
        if job.stipend_status == "Eligible" and s is not None:
            parts["salary"] = 15 if s >= 25000 else 12 if s >= 15000 else 10
            why.append(f"stipend ₹{int(s):,}/month")
        elif job.stipend_status == "Verification Required":
            parts["salary"] = 6
            why.append("stipend range crosses threshold")
        else:
            parts["salary"] = 3
    else:
        c = None if job.ctc_min_lpa == "" else float(job.ctc_min_lpa)
        if job.sal_status == "Eligible" and c is not None:
            parts["salary"] = 15 if c >= 12 else 13 if c >= 10 else 11
            why.append(f"CTC ≥ {c:g} LPA")
        elif job.sal_status == "Verification Required":
            parts["salary"] = 6
            why.append("salary needs verification")
        else:
            parts["salary"] = 4

    # location (10)
    city = (job.city or "").lower()
    if job.india_remote == "Yes" and job.work_arrangement == "Remote":
        parts["location"] = 9
        why.append("remote (India-eligible)")
    elif city in TIER1_CITIES:
        parts["location"] = 10
        why.append(job.city)
    elif job.india_remote == "Yes":
        parts["location"] = 8
        why.append(job.city or job.state or "India")
    else:
        parts["location"] = 2

    # employment type (5)
    parts["employment"] = 5 if job.job_kind == "Full-time" else 4 if job.job_kind == "Internship" else 2

    # company quality (7)
    cls = job.company_classification or ""
    if job.startup_status.startswith("Not a startup") and cls in ("MNC", "Large Indian company", "Bank/Financial institution", "Consulting firm"):
        parts["company"] = 7
        why.append(cls)
    elif job.startup_status.startswith("Not a startup"):
        parts["company"] = 5
        why.append(cls or "established company")
    elif job.startup_status.startswith("Startup - meets") or job.startup_status.startswith("Meets startup"):
        parts["company"] = 5
        why.append("startup meeting criteria")
    else:
        parts["company"] = 2

    # description quality (5)
    n = len(job.full_description or job.description or "")
    parts["description"] = 5 if n >= 1200 else 3 if n >= 500 else 1 if n >= 150 else 0

    # skills (5)
    d = (job.full_description or job.description or "").lower()
    hits = sum(1 for k in SKILL_KEYWORDS if _kw_regex(k).search(d))
    parts["skills"] = 5 if hits >= 6 else 3 if hits >= 3 else 1 if hits >= 1 else 0
    if hits >= 3:
        why.append(f"{hits} relevant skills")

    # freshness (5)
    age = None
    if job.posted_date:
        try:
            age = (today - date.fromisoformat(job.posted_date[:10])).days
        except ValueError:
            age = None
    parts["freshness"] = 2 if age is None else 5 if age <= 2 else 4 if age <= 7 else 3 if age <= 14 else 2 if age <= 30 else 1
    if age is not None and age <= 3:
        why.append("posted in last 3 days")

    # direct apply link (3)
    if job.apply_url and not _is_aggregator(job.apply_url):
        parts["apply_link"] = 3
        why.append("direct employer link")
    elif job.apply_url:
        parts["apply_link"] = 2
    else:
        parts["apply_link"] = 0

    total = max(0, min(100, sum(parts.values())))
    return ScoreResult(total, parts, "; ".join(why))
