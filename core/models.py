"""The common job schema.

Every connector returns ``Job`` objects. The same object is what the pipeline enriches and what is
serialised to / from one row of the Google Sheets "All Jobs" tab (``to_record`` / ``from_record``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any

# (attribute, sheet column header, kind)   kind: str | int | float | list | json
# ORDER MATTERS: this is the column order of the "All Jobs" tab. The first two are the user-owned columns.
FIELD_MAP: list[tuple[str, str, str]] = [
    ("application_status", "Application Status", "str"),      # USER OWNED - never overwritten
    ("notes", "Notes", "str"),                                # USER OWNED - never overwritten
    ("title", "Job Title", "str"),
    ("company", "Company", "str"),
    ("category", "Category", "str"),
    ("match_score", "Match Score", "int"),
    ("location", "Location", "str"),
    ("ctc_display", "CTC LPA", "str"),
    ("stipend_display", "Monthly Stipend INR", "str"),
    ("exp_display", "Experience Text", "str"),
    ("job_kind", "Full-time/Internship", "str"),
    ("posted_date", "Posted Date", "str"),
    ("apply_url", "Direct Apply URL", "str"),
    ("job_url", "Job URL", "str"),
    ("match_explanation", "Match Explanation", "str"),
    ("exclusion_reason", "Exclusion Reason", "str"),
    ("verification_flags", "Verification Flags", "str"),
    ("job_status", "Job Status", "str"),
    ("source", "Source", "str"),
    ("source_urls", "All Source URLs", "list"),
    ("function", "Function", "str"),
    ("company_classification", "Company Classification", "str"),
    ("startup_status", "Startup Verification Status", "str"),
    ("company_evidence", "Company Evidence", "str"),
    ("company_website", "Company Website", "str"),
    ("company_founded", "Company Founding Year", "str"),
    ("company_age", "Company Age", "str"),
    ("employee_count", "Employee Count", "str"),
    ("country", "Country", "str"),
    ("city", "City", "str"),
    ("state", "State", "str"),
    ("work_arrangement", "Work Arrangement", "str"),
    ("india_remote", "India Remote Eligibility", "str"),
    ("exp_min", "Experience Minimum", "str"),
    ("exp_max", "Experience Maximum", "str"),
    ("exp_status", "Experience Verification Status", "str"),
    ("exp_source", "Experience Source", "str"),
    ("employment_type", "Employment Type", "str"),
    ("sal_min", "Salary Minimum", "str"),
    ("sal_max", "Salary Maximum", "str"),
    ("sal_currency", "Salary Currency", "str"),
    ("sal_period", "Salary Period", "str"),
    ("sal_basis", "Salary Basis", "str"),
    ("sal_disclosure", "Salary Disclosure Status", "str"),
    ("sal_status", "Salary Verification Status", "str"),
    ("stipend_status", "Stipend Verification Status", "str"),
    ("ctc_min_lpa", "CTC LPA Min", "str"),
    ("ctc_max_lpa", "CTC LPA Max", "str"),
    ("stipend_min", "Stipend INR Min", "str"),
    ("stipend_max", "Stipend INR Max", "str"),
    ("deadline", "Application Deadline", "str"),
    ("date_discovered", "Date Discovered", "str"),
    ("first_seen", "First Seen Date", "str"),
    ("last_seen", "Last Seen Date", "str"),
    ("last_updated", "Last Updated Date", "str"),
    ("responsibilities", "Responsibilities", "str"),
    ("required_skills", "Required Skills", "str"),
    ("preferred_skills", "Preferred Skills", "str"),
    ("description", "Job Description", "str"),
    ("internal_id", "Internal Job ID", "str"),
    ("original_id", "Original Job ID", "str"),
    ("dup_group_id", "Duplicate Group ID", "str"),
    ("source_keys", "Source Keys", "list"),
    ("source_scopes", "Source Scopes", "list"),
    ("repost_count", "Repost Count", "int"),
    ("missed_runs", "Missed Runs", "int"),
    ("content_hash", "Content Hash", "str"),
    ("last_reported_hash", "Last Reported Hash", "str"),
    ("last_reported_date", "Last Reported Date", "str"),
    ("raw", "Raw Data", "json"),
]

COL = {attr: header for attr, header, _ in FIELD_MAP}
MASTER_COLUMNS = [header for _, header, _ in FIELD_MAP]
MANUAL_COLUMNS = ["Application Status", "Notes"]
MANUAL_ATTRS = ["application_status", "notes"]

# Truncation so the sheet stays below Google's per-cell (50,000 chars) and payload limits.
MAX_DESC_CHARS = 3000
MAX_RAW_CHARS = 2000
MAX_CELL_CHARS = 45000

APPLICATION_STATUSES = ["New", "Saved", "Applied", "Interview", "Rejected", "Offer", "Closed"]

CATEGORIES = [
    "Strong Matches", "Possible Matches", "Paid Internships", "Salary Undisclosed",
    "Salary Verification Required", "Company Verification Required",
    "Experience Verification Required", "Location Verification Required", "Excluded",
]


@dataclass
class Job:
    # ---- persisted (see FIELD_MAP) ----
    application_status: str = "New"
    notes: str = ""
    title: str = ""
    company: str = ""
    category: str = ""
    match_score: int = 0
    match_explanation: str = ""
    exclusion_reason: str = ""
    verification_flags: str = ""
    location: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    work_arrangement: str = ""
    india_remote: str = ""
    job_kind: str = ""
    employment_type: str = ""
    function: str = ""
    exp_min: str = ""
    exp_max: str = ""
    exp_display: str = ""
    exp_status: str = ""
    exp_source: str = ""
    sal_min: str = ""
    sal_max: str = ""
    sal_currency: str = ""
    sal_period: str = ""
    sal_basis: str = ""
    sal_disclosure: str = ""
    sal_status: str = ""
    stipend_status: str = ""
    ctc_min_lpa: str = ""
    ctc_max_lpa: str = ""
    ctc_display: str = ""
    stipend_min: str = ""
    stipend_max: str = ""
    stipend_display: str = ""
    company_classification: str = ""
    startup_status: str = ""
    company_evidence: str = ""
    company_website: str = ""
    company_founded: str = ""
    company_age: str = ""
    employee_count: str = ""
    description: str = ""
    responsibilities: str = ""
    required_skills: str = ""
    preferred_skills: str = ""
    posted_date: str = ""
    date_discovered: str = ""
    deadline: str = ""
    source: str = ""
    source_urls: list[str] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)
    source_scopes: list[str] = field(default_factory=list)
    job_url: str = ""
    apply_url: str = ""
    job_status: str = "Active"
    first_seen: str = ""
    last_seen: str = ""
    last_updated: str = ""
    dup_group_id: str = ""
    internal_id: str = ""
    original_id: str = ""
    repost_count: int = 0
    missed_runs: int = 0
    content_hash: str = ""
    last_reported_hash: str = ""
    last_reported_date: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # ---- ephemeral hints supplied by connectors (not persisted) ----
    hint_exp_text: str = ""
    hint_exp_min: float | None = None
    hint_exp_max: float | None = None
    hint_salary: dict[str, Any] | None = None   # {min,max,currency,period,basis,text,source}
    hint_company: dict[str, Any] = field(default_factory=dict)  # {founded_year, employees, industry, website, ...}
    hint_country: str = ""                       # "India" when the search itself was scoped to India
    hint_remote: bool | None = None
    full_description: str = ""                   # untruncated text used for extraction
    is_new_sighting: bool = True                 # False for records loaded from the sheet
    dirty: bool = True                           # needs re-extraction of experience/salary

    # ------------------------------------------------------------------ io
    def to_record(self) -> dict[str, str]:
        rec: dict[str, str] = {}
        for attr, header, kind in FIELD_MAP:
            val = getattr(self, attr)
            if kind == "list":
                text = "\n".join(dict.fromkeys(v for v in val if v))
            elif kind == "json":
                text = json.dumps(val, ensure_ascii=False, default=str)[:MAX_RAW_CHARS] if val else ""
            elif val is None:
                text = ""
            else:
                text = str(val)
            if attr == "description":
                text = text[:MAX_DESC_CHARS]
            rec[header] = text[:MAX_CELL_CHARS]
        return rec

    @classmethod
    def from_record(cls, rec: dict[str, str]) -> "Job":
        job = cls()
        for attr, header, kind in FIELD_MAP:
            raw = rec.get(header, "")
            raw = "" if raw is None else str(raw)
            if kind == "int":
                try:
                    val: Any = int(float(raw)) if raw.strip() else 0
                except ValueError:
                    val = 0
            elif kind == "list":
                val = [p.strip() for p in raw.split("\n") if p.strip()]
            elif kind == "json":
                try:
                    val = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    val = {}
            else:
                val = raw
            setattr(job, attr, val)
        job.full_description = job.description
        job.is_new_sighting = False
        job.dirty = False
        if not job.application_status:
            job.application_status = "New"
        return job


EPHEMERAL = {"hint_exp_text", "hint_exp_min", "hint_exp_max", "hint_salary", "hint_company",
             "hint_country", "hint_remote", "full_description", "is_new_sighting", "dirty"}
PERSISTED_ATTRS = [f.name for f in fields(Job) if f.name not in EPHEMERAL]
