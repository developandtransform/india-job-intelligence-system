"""Experience extraction and the universal maximum-experience rule.

The rule (project brief): **no job may require more than MAXIMUM_EXPERIENCE_YEARS (default 3)**, for every
company type, internships included.

* ``min``/``max`` are years. ``max=None`` means *open ended* ("3+ years").
* A range is eligible only when its **upper** bound is ≤ the limit ("0-3" ok, "2-4" excluded, "0-5" excluded).
* Open-ended requirements ("3+", "minimum 3 years", "at least 3 years") are excluded because the real
  requirement can exceed the limit.
* A plain "3 years" (no plus / minimum / range) is treated as exactly 3 and is eligible.
* Nothing found, or contradictory statements -> ``Verification Required``. Job titles such as Junior / Associate /
  Executive never make a job eligible on their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.textutil import fmt_num

ELIGIBLE = "Eligible"
EXCLUDED = "Excluded"
VERIFY = "Verification Required"

_DASH = r"(?:-|–|—|to|and)"
_NUM = r"(\d{1,2}(?:\.\d)?)"
_YR = r"(?:years?|yrs?\.?|yr\.?|y\b)"
_MON = r"(?:months?|mos?\.?)"

RANGE_RE = re.compile(rf"{_NUM}\s*\+?\s*({_YR}|{_MON})?\s*{_DASH}\s*{_NUM}\s*\+?\s*({_YR}|{_MON})", re.I)
PLUS_RE = re.compile(rf"{_NUM}\s*\+\s*({_YR}|{_MON})", re.I)
MINIMUM_RE = re.compile(rf"(?:minimum|min\.?|at least|atleast|over|more than|greater than|above)\s*(?:of\s*)?{_NUM}\s*({_YR}|{_MON})", re.I)
UPTO_RE = re.compile(rf"(?:up to|upto|maximum|max\.?|not more than|less than|under|within|below)\s*(?:of\s*)?{_NUM}\s*({_YR}|{_MON})", re.I)
SINGLE_RE = re.compile(rf"(?<![\d.\-–+])\b{_NUM}\s*({_YR}|{_MON})\b", re.I)

EXP_CONTEXT_RE = re.compile(r"\b(experience|exp\b|exp\.|experienced|work experience|relevant experience|hands[- ]on|"
                            r"in a similar role|post[- ]qualification|proven track record)", re.I)
DURATION_BEFORE_RE = re.compile(r"(internship|duration|period|tenure|contract|probation|notice|training|programme|program)\W{0,8}(?:of\W{0,3})?$", re.I)
DURATION_AFTER_RE = re.compile(r"\W{0,3}(?:internship|intern\b|contract|duration|training|programme|probation|notice)", re.I)
# a number of years that is *about the company or something else*, not the candidate
NEGATIVE_CONTEXT_RE = re.compile(
    r"\b(we have|we've|we are|our |the company|company has|team has|founded|established|since|legacy|heritage|"
    r"over the (past|last)|in (the|last|past) \d+|for (the )?(past|last)|warranty|guarantee|age of|aged|"
    r"years? old|years? of (age|history|excellence|legacy|operations|presence)|contract(s)? of|bond of|"
    r"service agreement|notice period|probation|tenure|loan|policy|lock[- ]in|trusted by|serving)\b", re.I)

FRESHER_RE = re.compile(
    r"\b(freshers?|fresh graduates?|fresh grads?|recent graduates?|no (prior |previous |relevant |work )?experience"
    r"(?: is)?(?: required| needed| necessary)?|experience (is )?not (required|mandatory|necessary)|"
    r"zero experience)\b", re.I)
# NOTE: titles (Trainee, Junior, Associate, Executive, Intern ...) are never used to infer eligibility.


@dataclass
class ExperienceResult:
    status: str = VERIFY
    min: float | None = None
    max: float | None = None          # None = open ended / unknown
    open_ended: bool = False
    text: str = ""
    source: str = ""                  # structured | description | title | gemini
    reason: str = ""

    @property
    def min_str(self) -> str:
        return fmt_num(self.min)

    @property
    def max_str(self) -> str:
        return "" if self.max is None else fmt_num(self.max)


def _unit_is_month(unit: str) -> bool:
    return unit.lower().startswith("mo")


def classify_bounds(lo: float | None, hi: float | None, open_ended: bool, limit: float) -> tuple[str, str]:
    """Apply the limit to a (min, max) pair."""
    if lo is None and hi is None:
        return VERIFY, "no experience requirement found"
    if open_ended:
        if (lo or 0) >= limit:
            return EXCLUDED, f"open-ended requirement starting at {fmt_num(lo)} years (limit {fmt_num(limit)})"
        # e.g. "1+ years": may demand more than the limit
        return VERIFY, f"open-ended requirement ({fmt_num(lo)}+ years): upper bound unknown"
    top = hi if hi is not None else lo
    if top is not None and top > limit:
        return EXCLUDED, f"requires up to {fmt_num(top)} years (limit {fmt_num(limit)})"
    return ELIGIBLE, f"requires {fmt_num(lo)}-{fmt_num(top)} years" if lo != top else f"requires {fmt_num(top)} years"


def _candidate_statements(text: str, need_context: bool) -> list[tuple[float, float | None, bool, str]]:
    """Return (min, max, open_ended, snippet) for every experience statement in ``text``."""
    out: list[tuple[float, float | None, bool, str]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < y and x < b for x, y in taken)

    def context_ok(start: int, end: int) -> bool:
        # a duration of an internship / contract / probation / notice period is not an experience requirement
        if DURATION_BEFORE_RE.search(text[max(0, start - 25): start]) or DURATION_AFTER_RE.match(text[end: end + 25]):
            return False
        window = text[max(0, start - 70): end + 70]
        if NEGATIVE_CONTEXT_RE.search(text[max(0, start - 45): start + 5]):
            return False
        return (not need_context) or bool(EXP_CONTEXT_RE.search(window))

    for regex, kind in ((RANGE_RE, "range"), (PLUS_RE, "plus"), (MINIMUM_RE, "min"), (UPTO_RE, "upto"), (SINGLE_RE, "single")):
        for m in regex.finditer(text):
            a, b = m.span()
            if overlaps(a, b) or not context_ok(a, b):
                continue
            snippet = text[max(0, a - 15): b + 25].replace("\n", " ").strip()
            if kind == "range":
                lo, hi = float(m.group(1)), float(m.group(3))
                unit_lo, unit_hi = m.group(2) or m.group(4), m.group(4)
                lo = round(lo / 12, 2) if _unit_is_month(unit_lo) else lo
                hi = round(hi / 12, 2) if _unit_is_month(unit_hi) else hi
                if lo > hi:
                    continue
                out.append((lo, hi, False, snippet))
            elif kind == "plus":
                v, unit = float(m.group(1)), m.group(2)
                v = round(v / 12, 2) if _unit_is_month(unit) else v
                out.append((v, None, True, snippet))
            elif kind == "min":
                v, unit = float(m.group(1)), m.group(2)
                v = round(v / 12, 2) if _unit_is_month(unit) else v
                out.append((v, None, True, snippet))
            elif kind == "upto":
                v, unit = float(m.group(1)), m.group(2)
                v = round(v / 12, 2) if _unit_is_month(unit) else v
                out.append((0.0, v, False, snippet))
            else:
                v, unit = float(m.group(1)), m.group(2)
                v = round(v / 12, 2) if _unit_is_month(unit) else v
                out.append((v, v, False, snippet))
            taken.append((a, b))
    return out


def parse_structured(text: str | None, lo: float | None, hi: float | None) -> tuple[float | None, float | None, bool, str] | None:
    """Parse a source-provided experience field (e.g. Naukri '0-2 Yrs', IIMJobs min/max numbers)."""
    if lo is not None or hi is not None:
        label = text or f"{fmt_num(lo)}-{fmt_num(hi)} years"
        return (lo if lo is not None else 0.0), hi, hi is None, label
    if not text:
        return None
    stm = _candidate_statements(text, need_context=False)
    if stm:
        l, h, o, snip = stm[0]
        return l, h, o, text.strip()
    if FRESHER_RE.search(text):
        return 0.0, 0.0, False, text.strip()
    return None


def evaluate_experience(title: str, description: str, hint_text: str = "", hint_min: float | None = None,
                        hint_max: float | None = None, limit: float = 3.0, kind: str = "") -> ExperienceResult:
    """Extract the experience requirement and apply the universal limit."""
    # 1) structured field from the source wins
    st = parse_structured(hint_text, hint_min, hint_max)
    if st:
        lo, hi, open_ended, label = st
        status, reason = classify_bounds(lo, hi, open_ended, limit)
        return ExperienceResult(status, lo, hi, open_ended, label, "structured", reason)

    text = f"{title}\n{description or ''}"
    statements = _candidate_statements(text, need_context=True)
    if statements:
        verdicts = [(classify_bounds(lo, hi, oe, limit), (lo, hi, oe, snip)) for lo, hi, oe, snip in statements]
        statuses = {v[0][0] for v in verdicts}
        if statuses == {EXCLUDED}:
            (st_, reason), (lo, hi, oe, snip) = max(verdicts, key=lambda v: (v[1][0], v[1][1] or 99))
            return ExperienceResult(EXCLUDED, lo, hi, oe, snip, "description", reason)
        if statuses == {ELIGIBLE}:
            # report the most demanding eligible statement
            (st_, reason), (lo, hi, oe, snip) = max(verdicts, key=lambda v: (v[1][1] if v[1][1] is not None else v[1][0]))
            return ExperienceResult(ELIGIBLE, lo, hi, oe, snip, "description", reason)
        # conflicting statements -> do not guess
        snips = " | ".join(v[1][3] for v in verdicts[:3])
        lo = min(v[1][0] for v in verdicts)
        return ExperienceResult(VERIFY, lo, None, False, snips[:200], "description",
                                "conflicting or partly open-ended experience statements")

    # 2) explicit fresher language in the description
    if FRESHER_RE.search(description or ""):
        m = FRESHER_RE.search(description)
        return ExperienceResult(ELIGIBLE, 0.0, 0.0, False, m.group(0), "description",
                                "description explicitly welcomes freshers / no experience")
    return ExperienceResult(VERIFY, None, None, False, "", "", "no explicit experience requirement found")
