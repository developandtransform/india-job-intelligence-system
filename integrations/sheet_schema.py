"""Tab names, headers and the rules that decide which jobs appear in each generated view tab."""
from __future__ import annotations

from typing import Callable

from core.models import MASTER_COLUMNS, Job
from processing.ai_cache import CACHE_HEADERS

TAB_ALL = "All Jobs"
TAB_NEW = "New Today"
TAB_STRONG = "Strong Matches"
TAB_POSSIBLE = "Possible Matches"
TAB_INTERN = "Paid Internships"
TAB_SAL_UNDISC = "Salary Undisclosed"
TAB_SAL_VERIFY = "Salary Verification Required"
TAB_COMPANY_VERIFY = "Company Verification Required"
TAB_EXP_VERIFY = "Experience Verification Required"
TAB_LOC_VERIFY = "Location Verification Required"   # extra tab (not in the original 13)
TAB_APPLIED = "Applied"
TAB_REJECTED = "Rejected and Closed"
TAB_STATS = "Source Statistics"
TAB_LOGS = "System Logs"
TAB_AI_CACHE = "AI Cache"           # extra tab: remembered AI answers (see processing/ai_cache.py)
TAB_REGISTRY = "Company Registry"      # extra tab (not in the original 13): where you record company facts

TAB_ORDER = [TAB_ALL, TAB_NEW, TAB_STRONG, TAB_POSSIBLE, TAB_INTERN, TAB_SAL_UNDISC, TAB_SAL_VERIFY,
             TAB_COMPANY_VERIFY, TAB_EXP_VERIFY, TAB_LOC_VERIFY, TAB_APPLIED, TAB_REJECTED, TAB_STATS, TAB_LOGS, TAB_REGISTRY, TAB_AI_CACHE]

VIEW_COLUMNS = ["Application Status", "Job Title", "Company", "Category", "Match Score", "Location", "CTC LPA",
                "Monthly Stipend INR", "Experience Text", "Full-time/Internship", "Posted Date", "Direct Apply URL",
                "Match Explanation", "Verification Flags", "Exclusion Reason", "Company Classification",
                "Startup Verification Status", "Source", "First Seen Date", "Job Status", "Internal Job ID"]

STATS_HEADERS = ["Run Date", "Run Time (UTC)", "Connector", "Source", "Status", "Jobs Fetched", "New Jobs",
                 "Out-of-scope Dropped", "Pre-filtered (non-India)", "Requests", "Cost USD", "Seconds", "Errors", "Notes"]
LOG_HEADERS = ["Timestamp (UTC)", "Level", "Component", "Message"]
REGISTRY_HEADERS = ["Company", "Classification", "Founded Year", "Employee Count", "Evidence / Source URL",
                    "Verified By", "Notes", "Last Updated", "Verification Status"]

CLASSIFICATION_CHOICES = ["Startup", "MNC", "Large Indian company", "Mid-sized company", "Established company", "SME",
                          "Bank/Financial institution", "Consulting firm", "Other"]
APPLICATION_STATUS_CHOICES = ["New", "Saved", "Applied", "Interview", "Rejected", "Offer", "Closed"]

MAX_VIEW_ROWS = 2000
_OPEN_STATUSES_EXCLUDED = {"Rejected", "Closed"}


def _active(j: Job) -> bool:
    return j.job_status == "Active" and j.application_status not in _OPEN_STATUSES_EXCLUDED


VIEW_RULES: dict[str, Callable[[Job, str], bool]] = {
    TAB_NEW: lambda j, today: j.first_seen == today and j.category != "Excluded",
    TAB_STRONG: lambda j, t: j.category == "Strong Matches" and _active(j),
    TAB_POSSIBLE: lambda j, t: j.category == "Possible Matches" and _active(j),
    TAB_INTERN: lambda j, t: j.category == "Paid Internships" and _active(j),
    TAB_SAL_UNDISC: lambda j, t: j.category == "Salary Undisclosed" and _active(j),
    TAB_SAL_VERIFY: lambda j, t: j.category == "Salary Verification Required" and _active(j),
    TAB_COMPANY_VERIFY: lambda j, t: j.category == "Company Verification Required" and _active(j),
    TAB_EXP_VERIFY: lambda j, t: j.category == "Experience Verification Required" and _active(j),
    TAB_LOC_VERIFY: lambda j, t: j.category == "Location Verification Required" and _active(j),
    TAB_APPLIED: lambda j, t: j.application_status in ("Applied", "Interview", "Offer"),
    TAB_REJECTED: lambda j, t: j.category == "Excluded" or j.job_status == "Closed" or j.application_status in _OPEN_STATUSES_EXCLUDED,
}


def build_views(jobs: list[Job], today: str) -> dict[str, list[list[str]]]:
    """Return {tab: [header_row, *rows]} for every generated view."""
    out: dict[str, list[list[str]]] = {}
    for tab, rule in VIEW_RULES.items():
        sel = [j for j in jobs if rule(j, today)]
        sel.sort(key=lambda j: (-j.match_score, j.first_seen or ""), reverse=False)
        rows = []
        for j in sel[:MAX_VIEW_ROWS]:
            rec = j.to_record()
            rows.append([rec[c] for c in VIEW_COLUMNS])
        out[tab] = [VIEW_COLUMNS, *rows]
    return out


assert set(VIEW_COLUMNS) <= set(MASTER_COLUMNS)
