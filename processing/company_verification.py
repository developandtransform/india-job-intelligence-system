"""Company classification and the startup eligibility rule.

Rule (project brief)
--------------------
* A **startup** is eligible only if it is >= STARTUP_MIN_AGE_YEARS (5) years old **and** has >= STARTUP_MIN_EMPLOYEES (90)
  employees. Age is computed from today's date on every run.
* **Every other kind of company** (MNC, large Indian company, bank, consulting firm, SME, ...) has *no* age or
  employee restriction.
* Unknown age/headcount, or an unclear classification -> ``Verification Required``. Nothing is guessed.

Evidence sources (best first): the ``Company Registry`` tab of the Google Sheet (you edit it) > the bundled seed
registry > structured company data returned by a source (founded year / employee count). A company name that merely
*looks* like a bank or consultancy is shown as a hint but never unlocks eligibility on its own.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from config.profile import STARTUP_LANGUAGE
from core.textutil import fmt_num, norm_company

log = logging.getLogger("company")

ELIGIBLE = "Eligible"
EXCLUDED = "Excluded"
VERIFY = "Verification Required"

CLASSIFICATIONS = ["Startup", "MNC", "Large Indian company", "Mid-sized company", "Established company", "SME",
                   "Bank/Financial institution", "Consulting firm", "Other"]
NON_STARTUP = {c for c in CLASSIFICATIONS if c != "Startup"}

SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "company_seed.yaml"

_NAME_HINTS = [
    ("Bank/Financial institution", re.compile(r"\b(bank|insurance|assurance|mutual fund|asset management|securities|housing finance)\b", re.I)),
    ("Consulting firm", re.compile(r"\b(consulting|consultancy|advisors|advisory)\b", re.I)),
]


@dataclass
class CompanyRecord:
    name: str
    classification: str = ""
    founded: str = ""          # "2014" or "2014-06" or "2014-06-12"
    employees: str = ""        # "250", "51-200", "1001-5000", "10000+"
    evidence: str = ""
    verified_by: str = ""      # Seed | User | Source
    verification_status: str = "Unverified"   # Verified (evidence URL + date recorded) | Unverified | User-provided (no evidence)
    evidence_url: str = ""
    verified_date: str = ""


@dataclass
class CompanyResult:
    status: str = VERIFY
    classification: str = ""
    startup_status: str = ""
    evidence: str = ""
    founded: str = ""
    age_text: str = ""
    employees_text: str = ""
    reason: str = ""
    unverified: bool = False      # classification relies on data with no recorded evidence


# ---------------------------------------------------------------- parsing
def parse_employees(text: str | int | float | None) -> tuple[int | None, int | None]:
    """'250' -> (250,250); '51-200' -> (51,200); '10,000+' -> (10000,None); unknown -> (None,None)."""
    if text is None or str(text).strip() == "":
        return None, None
    t = str(text).lower().replace(",", "").replace("employees", "").strip()
    m = re.match(r"(\d+)\s*(?:-|–|to)\s*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)\s*\+", t)
    if m:
        return int(m.group(1)), None
    m = re.match(r"(\d+(?:\.\d+)?)\s*(k)?$", t)
    if m:
        n = int(float(m.group(1)) * (1000 if m.group(2) else 1))
        return n, n
    return None, None


def age_bounds(founded: str | int | None, today: date) -> tuple[float, float] | None:
    """(lowest possible age, highest possible age) in years given a year / year-month / full date."""
    if founded is None or str(founded).strip() == "":
        return None
    s = str(founded).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        m2 = re.match(r"^(\d{1,2})/(\d{4})$", s)
        if not m2:
            return None
        y, mo, d = int(m2.group(2)), int(m2.group(1)), None
    else:
        y = int(m.group(1))
        mo = int(m.group(2)) if m.group(2) else None
        d = int(m.group(3)) if m.group(3) else None
    if not (1800 <= y <= today.year):
        return None
    try:
        if mo and d:
            exact = (today - date(y, mo, d)).days / 365.25
            return exact, exact
        if mo:
            start = date(y, mo, 1)
            end = date(y + (mo == 12), (mo % 12) + 1, 1)
            return (today - end).days / 365.25, (today - start).days / 365.25
    except ValueError:
        return None
    return (today - date(y, 12, 31)).days / 365.25, (today - date(y, 1, 1)).days / 365.25


# --------------------------------------------------------------- registry
class CompanyRegistry:
    """Lookup of known companies. Sheet rows (user) override the bundled seed."""

    def __init__(self) -> None:
        self._by_key: dict[str, CompanyRecord] = {}
        self.pending: dict[str, str] = {}   # normalised key -> display name (companies to add to the sheet)

    def add(self, rec: CompanyRecord, aliases: list[str] | None = None, override: bool = True) -> None:
        for n in [rec.name, *(aliases or [])]:
            key = norm_company(n)
            if key and (override or key not in self._by_key):
                self._by_key[key] = rec

    def load_seed(self, path: Path = SEED_PATH) -> int:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Could not read company seed registry: %s", exc)
            return 0
        n = 0
        for item in data.get("companies", []):
            url, vdate = str(item.get("evidence_url", "") or ""), str(item.get("verified_date", "") or "")
            rec = CompanyRecord(item["name"], item.get("classification", ""), str(item.get("founded", "") or ""),
                                str(item.get("employees", "") or ""),
                                url or "UNVERIFIED bundled seed - general knowledge, no evidence recorded", "Seed",
                                "Verified" if (url and vdate) else "Unverified", url, vdate)
            self.add(rec, item.get("aliases") or [], override=False)
            n += 1
        return n

    def load_sheet_rows(self, rows: list[dict[str, str]]) -> int:
        n = 0
        for r in rows:
            name = (r.get("Company") or "").strip()
            if not name:
                continue
            cls = (r.get("Classification") or "").strip()
            url = (r.get("Evidence / Source URL") or "").strip()
            rec = CompanyRecord(name, cls, str(r.get("Founded Year") or "").strip(), str(r.get("Employee Count") or "").strip(),
                                url or "Company Registry tab (entered by you, no evidence URL)", "User",
                                "Verified" if url else "User-provided (no evidence)", url,
                                (r.get("Last Updated") or "").strip())
            if cls or rec.founded or rec.employees:
                self.add(rec, override=True)
                n += 1
            else:
                self._by_key.setdefault(norm_company(name), rec)   # known to the sheet already, still pending
        return n

    def lookup(self, name: str) -> CompanyRecord | None:
        return self._by_key.get(norm_company(name))

    def note_unknown(self, name: str) -> None:
        key = norm_company(name)
        if key and key not in self._by_key:
            self.pending.setdefault(key, name.strip())


def name_hint(name: str) -> str:
    for cls, rx in _NAME_HINTS:
        if rx.search(name or ""):
            return cls
    return ""


def startup_language(text: str) -> bool:
    head = (text or "")[:3500]
    return any(re.search(p, head, re.I) for p in STARTUP_LANGUAGE)


# --------------------------------------------------------------- decision
def _startup_test(founded: str, employees: str, min_age: float, min_emp: int, today: date) -> tuple[str, str, str, str]:
    """Return (status, startup_status, age_text, reason) for a company known/assumed to be a startup."""
    bounds = age_bounds(founded, today)
    lo_e, hi_e = parse_employees(employees)
    age_text = "" if not bounds else (fmt_num(round(bounds[0], 1)) if abs(bounds[0] - bounds[1]) < 0.05
                                       else f"{fmt_num(round(bounds[0], 1))}-{fmt_num(round(bounds[1], 1))}")
    age_ok = None if bounds is None else (True if bounds[0] >= min_age else (False if bounds[1] < min_age else None))
    emp_ok = None if lo_e is None else (True if lo_e >= min_emp else (False if (hi_e is not None and hi_e < min_emp) else None))
    if age_ok is False or emp_ok is False:
        why = []
        if age_ok is False:
            why.append(f"younger than {fmt_num(min_age)} years (age ~{age_text})")
        if emp_ok is False:
            why.append(f"fewer than {min_emp} employees ({employees})")
        return EXCLUDED, "Startup - fails criteria", age_text, "; ".join(why)
    if age_ok and emp_ok:
        return ELIGIBLE, "Startup - meets criteria", age_text, f"age ~{age_text} years and {employees} employees"
    missing = []
    if age_ok is None:
        missing.append("founding year/month" if bounds is None else f"exact founding date (age ~{age_text} straddles {fmt_num(min_age)})")
    if emp_ok is None:
        missing.append("employee count" if lo_e is None else f"employee count ({employees} straddles {min_emp})")
    return VERIFY, "Startup - verification required", age_text, "need " + " and ".join(missing)


def evaluate_company(name: str, description: str, hints: dict, registry: CompanyRegistry,
                     min_age: float, min_emp: int, today: date, seed_mode: str = "flag") -> CompanyResult:
    """Decide whether a company passes the company rule. ``hints`` is the connector-provided company data."""
    hints = hints or {}
    rec = registry.lookup(name)
    founded = str(hints.get("founded_year") or "")
    employees = str(hints.get("employees") or "")
    guess = name_hint(name)

    # 1) registry (sheet > seed) is authoritative for classification
    if rec and rec.classification:
        f = rec.founded or founded
        e = rec.employees or employees
        unverified_seed = rec.verified_by == "Seed" and rec.verification_status != "Verified"
        if unverified_seed and seed_mode == "strict":
            registry.note_unknown(name)
            return CompanyResult(VERIFY, rec.classification + " (unverified seed)", "Verification required",
                                 "Unverified bundled seed (COMPANY_SEED_MODE=strict)", f, "", e,
                                 "classification exists only in the unverified bundled seed - confirm it in the Company Registry tab")
        if rec.classification in NON_STARTUP:
            return CompanyResult(ELIGIBLE, rec.classification, "Not a startup (unverified)" if unverified_seed else "Not a startup",
                                 rec.evidence or rec.verified_by, f, "", e,
                                 f"classified as {rec.classification}; startup limits do not apply", unverified=unverified_seed)
        status, ss, age_text, reason = _startup_test(f, e, min_age, min_emp, today)
        return CompanyResult(status, "Startup", ss, rec.evidence or rec.verified_by, f, age_text, e, reason)

    # 2a) the source labels the company type explicitly (e.g. IIMJobs "companyType")
    ctype = str(hints.get("company_type") or "").lower()
    if re.search(r"(?<!non[- ])\bmnc\b|multinational", ctype):
        return CompanyResult(ELIGIBLE, "MNC", "Not a startup", "Source label: company type " + ctype, "", "", "",
                             "source labels it an MNC; startup limits do not apply")
    # 2) the source itself supplied founded year and/or headcount
    if founded or employees:
        status, ss, age_text, reason = _startup_test(founded, employees, min_age, min_emp, today)
        if status == ELIGIBLE:
            return CompanyResult(ELIGIBLE, "Unclassified (meets startup thresholds)", "Meets startup thresholds regardless of category",
                                 "Source-provided company data", founded, age_text, employees, reason)
        if status == EXCLUDED and startup_language(description):
            return CompanyResult(EXCLUDED, "Startup (self-described)", ss, "Source-provided data + startup wording in description",
                                 founded, age_text, employees, reason)
        registry.note_unknown(name)
        return CompanyResult(VERIFY, guess + " (guess)" if guess else "", "Verification required",
                             "Source-provided company data (incomplete)", founded, age_text, employees,
                             "fails/does not prove startup thresholds and startup status is unclear: " + reason)

    # 3) nothing known
    registry.note_unknown(name)
    cls = "Startup (self-described)" if startup_language(description) else (guess + " (guess)" if guess else "")
    return CompanyResult(VERIFY, cls, "Verification required", "No evidence", "", "", "",
                         "company classification, age and headcount unknown - add it to the Company Registry tab")
