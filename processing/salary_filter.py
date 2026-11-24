"""Salary / stipend extraction, unit conversion and the compensation eligibility rules.

Rules (project brief)
---------------------
Full-time : annual CTC must be >= MINIMUM_CTC_LPA (default 8 LPA).
            range entirely below -> Excluded | range crossing the threshold -> Verification Required
            not disclosed -> Undisclosed | fixed / base / OTE figure (not CTC) -> Verification Required
Internship: monthly stipend must be >= MINIMUM_INTERNSHIP_STIPEND_INR_MONTHLY (default 10,000).
            unpaid or below -> Excluded | crossing -> Verification Required
            not disclosed / period unclear -> Undisclosed

Nothing is ever estimated: a period is converted only when the source states it. Non-INR amounts are never
converted (no exchange rates are assumed) and hourly / daily pay is never annualised.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.textutil import fmt_num

ELIGIBLE = "Eligible"
EXCLUDED = "Excluded"
VERIFY = "Verification Required"
UNDISCLOSED = "Undisclosed"

DISCLOSED = "Disclosed"
NOT_DISCLOSED = "Not disclosed"
UNPAID = "Unpaid"
UNCLEAR = "Unclear"

CTC_BASES = {"CTC", "Platform-CTC", "Unspecified"}

# "up to 12 LPA", "max 15,000/month": the figure is a MAXIMUM; the minimum is unknown.
UPTO_RE = re.compile(r"(?:up\s*to|upto|maximum(?:\s+of)?|max\.?(?:\s+of)?|as\s+high\s+as|capped\s+at|not\s+exceeding)\s*[:\-]?\s*(?:(?:₹|rs\.?|inr|rupees?)\s*)?$", re.I)

_CUR = r"(?:₹|rs\.?|inr|rupees?)"
_AMT = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MULT = r"(k|thousand|lakhs?|lacs?|lpa|l\.p\.a\.?|cr|crores?|m)?"
_DASH = r"(?:-|–|—|to)"
_PERIOD_PATTERNS = [
    ("month", r"per\s*month|/\s*month|\bmonthly\b|/\s*mo\b|\bp\.?m\.?\b|a\s*month|each\s*month|per\s*mth|/\s*mth"),
    ("year", r"per\s*annum|per\s*year|/\s*(?:yr|year|annum)\b|\bannual(?:ly)?\b|\bp\.?a\.?\b|\byearly\b|a\s*year|\blpa\b|\bctc\b"),
    ("week", r"per\s*week|/\s*week|\bweekly\b|a\s*week"),
    ("day", r"per\s*day|/\s*day|\bdaily\b|a\s*day"),
    ("hour", r"per\s*hour|/\s*h(?:ou)?r\b|\bhourly\b"),
    ("total", r"^\s*(?:in\s+)?total\b|for\s+the\s+(?:entire|whole|complete)\s+(?:internship|duration|period|tenure|programme|program)|in\s+total|lump\s*sum|one[- ]time|total\s+stipend"),
]

UNPAID_RE = re.compile(r"\b(unpaid|no stipend|without (?:a )?stipend|non[- ]?paid|stipend\s*[:\-]?\s*(?:nil|none|zero|0\b)|"
                       r"voluntary|volunteer(?:ing)? (?:role|position|internship)|certificate only)\b", re.I)
UNDISCLOSED_RE = re.compile(r"\b(not disclosed|undisclosed|competitive|as per (?:industry|company|market)|best in (?:the )?industry|"
                            r"negotiable|attractive|performance[- ]based|merit[- ]based|incentive[- ]based|commission[- ]only|"
                            r"will be discussed|as per (?:experience|interview|norms|standards)|no bar for|not mentioned|"
                            r"disclosed (?:during|at|in|after))\b", re.I)
CONTEXT_RE = re.compile(r"\b(salary|ctc|stipend|compensation|package|remuneration|pay|lpa|earning|income|"
                        r"per month|per annum|annum|p\.a\.|monthly)\b", re.I)
BASIS_PATTERNS = [
    ("OTE", r"\bote\b|on[- ]target earnings?"),
    ("Fixed/Base", r"\bfixed\b|\bbase (?:salary|pay)\b|\bbasic\b|in[- ]hand|take[- ]?home|\bgross\b|\bnet (?:salary|pay)\b"),
    ("CTC", r"\bctc\b|cost to company|per annum total"),       # explicit CTC wording wins over "incl. variable"
    ("Variable", r"\bvariable\b|\bincentives?\b|\bbonus\b|\bcommission\b"),
    ("CTC", r"\bpackage\b|\blpa\b"),                          # weak signals: only when nothing above matched
]
CURRENCY_MAP = [("USD", r"\$|\busd\b|us dollars?"), ("EUR", r"€|\beur\b"), ("GBP", r"£|\bgbp\b"),
                ("AED", r"\baed\b|\bdirhams?\b"), ("SGD", r"\bsgd\b"), ("INR", _CUR)]


@dataclass
class Compensation:
    min: float | None = None          # amounts in the stated currency (INR already scaled for lakh/k)
    max: float | None = None
    currency: str = ""
    period: str = ""                  # year | month | week | day | hour | total | ""
    basis: str = "Unspecified"
    disclosure: str = NOT_DISCLOSED
    text: str = ""
    source: str = ""                  # structured | description | title

    @property
    def has_amount(self) -> bool:
        return self.min is not None or self.max is not None


@dataclass
class CompResult:
    status: str = UNDISCLOSED
    reason: str = ""
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    stipend_min: float | None = None
    stipend_max: float | None = None
    disclosure: str = NOT_DISCLOSED


# ----------------------------------------------------------------- parsing
def _scale(value: float, unit: str | None) -> tuple[float, bool]:
    """Return (value in plain rupees, is_lpa_unit)."""
    u = (unit or "").lower().replace(".", "")
    if u in ("k", "thousand"):
        return value * 1_000, False
    if u.startswith("lakh") or u.startswith("lac"):
        return value * 100_000, False
    if u == "lpa":
        return value * 100_000, True
    if u.startswith("cr"):
        return value * 10_000_000, False
    return value, False


def _detect_period(window_after: str, window_before: str) -> str:
    for period, pat in _PERIOD_PATTERNS:
        if re.search(pat, window_after, re.I):
            return period
    for period, pat in _PERIOD_PATTERNS:
        if re.search(pat, window_before, re.I):
            return period
    return ""


def _word_window(t: str, a: int, b: int, trim_left: bool = True, trim_right: bool = True) -> str:
    """t[a:b] without words cut in half at the OUTER ends ("re|mote" must not look like the word "ote").
    Edges that touch the matched amount are never trimmed (trim_left/trim_right=False)."""
    a, b = max(0, a), min(len(t), b)
    if trim_left and a > 0 and t[a - 1].isalnum():
        nxt = t.find(" ", a)
        a = len(t) if nxt == -1 else nxt
    if trim_right and b < len(t) and t[b].isalnum():
        prv = t.rfind(" ", a, b)
        b = a if prv == -1 else prv
    return t[a:b]


def _detect_basis(window: str) -> str:
    for basis, pat in BASIS_PATTERNS:
        if re.search(pat, window, re.I):
            return basis
    return "Unspecified"


def _detect_currency(window: str) -> str:
    for cur, pat in CURRENCY_MAP:
        if re.search(pat, window, re.I):
            return cur
    return ""


AMOUNT_RE = re.compile(
    rf"(?P<cur1>{_CUR}|[$€£])?\s*(?P<a1>{_AMT})\s*(?P<u1>{_MULT})\s*(?:{_DASH}\s*(?P<cur2>{_CUR}|[$€£])?\s*(?P<a2>{_AMT})\s*(?P<u2>{_MULT}))?",
    re.I)


def parse_compensation_text(text: str, is_internship: bool = False) -> Compensation | None:
    """Find the compensation statement inside free text. Returns None when nothing relevant is stated."""
    if not text:
        return None
    t = text.replace("₹", "₹")
    if UNPAID_RE.search(t) and is_internship:
        m = UNPAID_RE.search(t)
        return Compensation(disclosure=UNPAID, text=m.group(0), source="description")

    best: Compensation | None = None
    for m in AMOUNT_RE.finditer(t):
        a1_raw = m.group(0)
        start, end = m.span()
        cur_sym = m.group("cur1") or m.group("cur2") or ""
        u1 = (m.group("u1") or "")
        u2 = (m.group("u2") or "")
        unit_any = (u2 or u1).lower().replace(".", "")
        window_after = _word_window(t, end, end + 40, trim_left=False)
        window_before = _word_window(t, start - 40, start, trim_right=False)
        around = _word_window(t, start - 60, end + 60)
        has_period_word = bool(_detect_period(window_after, window_before))
        is_lpa_like = unit_any in ("lpa", "lakh", "lakhs", "lac", "lacs")
        # Need either a currency symbol, a lakh/LPA unit, or (salary-ish context AND a period word)
        if not (cur_sym or is_lpa_like or (CONTEXT_RE.search(around) and has_period_word)):
            continue
        # skip things that look like years, phone numbers, ids, percentages
        raw_num = m.group(0)
        if re.search(r"%|\byears?\b|\byrs?\b", t[end: end + 8], re.I) and not cur_sym:
            continue
        v1 = float(m.group("a1").replace(",", ""))
        v2 = float(m.group("a2").replace(",", "")) if m.group("a2") else None
        unit_first = u1 or u2
        s1, lpa1 = _scale(v1, u1 or u2)
        # a small bare number with no unit/symbol (e.g. "5 per month") is not a salary
        if not cur_sym and not is_lpa_like and s1 < 100:
            continue
        s2 = None
        if v2 is not None:
            s2, _ = _scale(v2, u2 or u1)
            if s2 < s1 and not (u1 and u2):  # e.g. "10 - 15k": scale first by the shared unit
                s1, _ = _scale(v1, unit_first)
        lo, hi = (min(s1, s2), max(s1, s2)) if s2 is not None else (s1, None)
        if s2 is None and UPTO_RE.search(window_before):
            lo, hi = None, s1            # only a maximum is stated; the minimum is unknown
        period = _detect_period(window_after, window_before)
        if lpa1 or unit_any == "lpa" or (is_lpa_like and not period):
            # "8 LPA" is annual by definition; a plain "lakh" without a period is NOT assumed annual
            period = "year" if (unit_any == "lpa" or period == "year") else period
        currency = _detect_currency(around) or ("INR" if (cur_sym.lower() in ("₹", "rs", "rs.", "inr", "rupee", "rupees") or is_lpa_like) else "")
        if not currency and cur_sym == "$":
            currency = "USD"
        if cur_sym in ("€", "£"):
            currency = "EUR" if cur_sym == "€" else "GBP"
        if not currency:
            currency = "INR"   # un-symboled amount with salary context + period word in an Indian listing
        comp = Compensation(lo, hi, currency, period, _detect_basis(_word_window(t, start - 35, end + 40)), DISCLOSED, a1_raw.strip(), "description")
        if best is None or (comp.period and not best.period):
            best = comp
        if comp.period:
            break
    if best:
        return best
    if UNDISCLOSED_RE.search(t):
        return Compensation(disclosure=NOT_DISCLOSED, text=UNDISCLOSED_RE.search(t).group(0), source="description")
    return None


def from_structured(hint: dict | None) -> Compensation | None:
    """Build a Compensation from a connector-supplied dict {min,max,currency,period,basis,text,source}."""
    if not hint:
        return None
    lo, hi = hint.get("min"), hint.get("max")
    try:
        lo = float(lo) if lo not in (None, "", 0, 0.0) else None
        hi = float(hi) if hi not in (None, "", 0, 0.0) else None
    except (TypeError, ValueError):
        lo = hi = None
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return Compensation(lo, hi, (hint.get("currency") or "").upper(), (hint.get("period") or "").lower(),
                        hint.get("basis") or "Unspecified", DISCLOSED, hint.get("text") or "", "structured")


def resolve_compensation(hint: dict | None, text: str, is_internship: bool) -> Compensation:
    comp = from_structured(hint)
    if comp:
        return comp
    comp = parse_compensation_text(text, is_internship)
    return comp or Compensation(disclosure=NOT_DISCLOSED)


# --------------------------------------------------------------- conversion
def annual_inr(comp: Compensation) -> tuple[float | None, float | None, str]:
    """Annual INR (min, max) when the source supports the conversion, else (None, None, reason)."""
    if not comp.has_amount:
        return None, None, "no amount"
    if comp.currency not in ("INR", ""):
        return None, None, f"amount is in {comp.currency}; no exchange rate is assumed"
    factor = {"year": 1.0, "month": 12.0, "week": 52.0}.get(comp.period)
    if factor is None:
        reason = {"day": "daily pay is not annualised", "hour": "hourly pay is not annualised",
                  "total": "a lump sum is not an annual figure", "": "pay period is not stated"}.get(comp.period, "period unclear")
        return None, None, reason
    lo = comp.min * factor if comp.min is not None else None
    hi = comp.max * factor if comp.max is not None else None
    return lo, hi, ""


def monthly_inr(comp: Compensation, duration_months: float | None = None) -> tuple[float | None, float | None, str]:
    if not comp.has_amount:
        return None, None, "no amount"
    if comp.currency not in ("INR", ""):
        return None, None, f"amount is in {comp.currency}; no exchange rate is assumed"
    if comp.period == "month":
        factor = 1.0
    elif comp.period in ("year", "week"):
        if not duration_months:
            return None, None, f"stipend is quoted per {comp.period} but the internship duration is not clear"
        factor = (1 / 12) if comp.period == "year" else (52 / 12)
    elif comp.period == "total":
        if not duration_months:
            return None, None, "stipend is for the whole internship but the duration is not clear"
        factor = 1 / duration_months
    else:
        reason = "daily/hourly stipend is not converted" if comp.period in ("day", "hour") else "stipend period is not stated"
        return None, None, reason
    lo = comp.min * factor if comp.min is not None else None
    hi = comp.max * factor if comp.max is not None else None
    return lo, hi, ""


DURATION_RE = re.compile(r"(?:duration|period|tenure|for|of)?\s*(?:of\s*)?(\d{1,2})\s*(?:-|–|to)?\s*(\d{1,2})?\s*months?", re.I)


def internship_duration_months(text: str) -> float | None:
    """A single unambiguous internship duration in months, else None."""
    found = set()
    for m in re.finditer(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*months?|(\d{1,2})\s*months?", text or "", re.I):
        if m.group(1):
            found.add((float(m.group(1)), float(m.group(2))))
        else:
            found.add((float(m.group(3)), float(m.group(3))))
    found = {f for f in found if f[0] >= 1}
    if len(found) == 1:
        lo, hi = next(iter(found))
        return lo if lo == hi else None
    return None


# ------------------------------------------------------------------ rules
def _range_verdict(lo: float | None, hi: float | None, threshold: float, unit: str) -> tuple[str, str]:
    top = hi if hi is not None else lo
    bottom = lo
    if bottom is None and top is None:
        return UNDISCLOSED, "no amount"
    if bottom is None:                       # only a maximum is known ("up to X")
        if top < threshold:
            return EXCLUDED, f"maximum {fmt_num(top)} {unit} is below the {fmt_num(threshold)} {unit} threshold"
        return VERIFY, f"only a maximum of {fmt_num(top)} {unit} is stated; the minimum is unknown"
    if bottom >= threshold:
        return ELIGIBLE, f"minimum {fmt_num(bottom)} {unit} meets the {fmt_num(threshold)} {unit} threshold"
    if top is not None and top < threshold:
        return EXCLUDED, f"maximum {fmt_num(top)} {unit} is below the {fmt_num(threshold)} {unit} threshold"
    return VERIFY, f"range {fmt_num(bottom)}-{fmt_num(top)} {unit} crosses the {fmt_num(threshold)} {unit} threshold"


def evaluate_fulltime(comp: Compensation, min_lpa: float) -> CompResult:
    r = CompResult(disclosure=comp.disclosure)
    if comp.disclosure == UNPAID:
        r.status, r.reason = EXCLUDED, "role is stated as unpaid"
        return r
    if not comp.has_amount:
        r.status, r.reason = UNDISCLOSED, "salary not disclosed"
        r.disclosure = NOT_DISCLOSED
        return r
    lo, hi, why = annual_inr(comp)
    if lo is None and hi is None:
        r.status, r.reason = VERIFY, f"cannot compute annual INR: {why}"
        r.disclosure = UNCLEAR
        return r
    r.ctc_min_lpa = None if lo is None else lo / 100_000
    r.ctc_max_lpa = None if hi is None else hi / 100_000
    if comp.basis not in CTC_BASES:
        r.status = VERIFY
        r.reason = f"figure is {comp.basis}, not confirmed CTC"
        return r
    if comp.period in ("month", "week"):
        # a per-month/week figure is salary paid out, not CTC: never treat it as CTC without evidence
        r.status = VERIFY
        shown = display_range(None if lo is None else round(lo / 100_000, 2), None if hi is None else round(hi / 100_000, 2))
        r.reason = f"quoted per {comp.period} (about {shown} LPA if annualised); CTC is not confirmed"
        r.ctc_min_lpa = r.ctc_max_lpa = None
        return r
    r.status, r.reason = _range_verdict(r.ctc_min_lpa, r.ctc_max_lpa, min_lpa, "LPA")
    return r


def evaluate_internship(comp: Compensation, min_monthly: float, duration_months: float | None = None) -> CompResult:
    r = CompResult(disclosure=comp.disclosure)
    if comp.disclosure == UNPAID:
        r.status, r.reason = EXCLUDED, "unpaid internship"
        return r
    if not comp.has_amount:
        r.status, r.reason = UNDISCLOSED, "stipend not disclosed"
        r.disclosure = NOT_DISCLOSED
        return r
    lo, hi, why = monthly_inr(comp, duration_months)
    if lo is None and hi is None:
        r.status, r.reason = UNDISCLOSED, f"stipend unclear: {why}"
        r.disclosure = UNCLEAR
        return r
    r.stipend_min, r.stipend_max = lo, hi
    if comp.basis == "Variable" or comp.basis == "OTE":
        r.status, r.reason = VERIFY, "stipend appears to be performance/variable pay"
        return r
    r.status, r.reason = _range_verdict(lo, hi, min_monthly, "INR/month")
    return r


def display_range(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is None:
        return ""
    if hi is None or hi == lo:
        return fmt_num(lo if lo is not None else hi)
    return f"{fmt_num(lo)}-{fmt_num(hi)}"
