"""The decision engine: applies every hard rule, then scores, then assigns exactly one category.

Order of precedence for the category
------------------------------------
1. Any definite failure                    -> ``Excluded`` (with a reason; the job row is kept for audit)
2. Experience unknown/ambiguous            -> ``Experience Verification Required``
3. Company unknown / startup unproven      -> ``Company Verification Required``
4. Salary range crosses the threshold      -> ``Salary Verification Required``
5. Salary / stipend not disclosed          -> ``Salary Undisclosed``
6. Everything confirmed                    -> ``Paid Internships`` (internships) or ``Strong``/``Possible Matches``

All outstanding issues are listed in ``Verification Flags`` even though only one category is assigned.
A high match score can never override a failed rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from config.settings import Settings
from core.models import Job
from core.textutil import fmt_num, parse_num, short_hash
from processing import experience_filter as xf
from processing import salary_filter as sf
from processing.ai_assist import GeminiAssistant
from processing.company_verification import CompanyRegistry, evaluate_company
from processing.scoring import FunctionMatch, detect_function, score_job

OUT_OF_SCOPE_REASONS = ("Location restriction", "Irrelevant position", "Other")


@dataclass
class EvalContext:
    settings: Settings
    registry: CompanyRegistry
    today: date
    ai: GeminiAssistant | None = None


def _exp_display(j: Job) -> str:
    if j.exp_min == "" and j.exp_max == "":
        return j.exp_display if j.exp_source else ""
    if j.exp_max == "":
        return f"{j.exp_min}+ yrs"
    return f"{j.exp_min} yrs" if j.exp_min == j.exp_max else f"{j.exp_min}-{j.exp_max} yrs"


def _extract_experience(j: Job, ctx: EvalContext) -> None:
    limit = ctx.settings.maximum_experience_years
    res = xf.evaluate_experience(j.title, j.full_description, j.hint_exp_text, j.hint_exp_min, j.hint_exp_max, limit, j.job_kind)
    if res.status == xf.VERIFY and not res.source and ctx.ai and len(j.full_description) >= 200:
        quote = ctx.ai.find_experience_quote(j.title, j.full_description)
        if quote:
            ai_res = xf.evaluate_experience("", quote, limit=limit, kind=j.job_kind)
            if ai_res.status != xf.VERIFY:
                ai_res.source = "gemini (quote verified)"
                res = ai_res
    j.exp_min, j.exp_max = res.min_str, res.max_str
    j.exp_source = res.source + ("-conflict" if res.status == xf.VERIFY and res.source else "")
    j.exp_display = (res.text or "")[:120]


def _rederive_experience(j: Job, limit: float) -> tuple[str, str]:
    if not j.exp_source or j.exp_source.endswith("conflict"):
        return xf.VERIFY, "no explicit or consistent experience requirement found"
    lo, hi = parse_num(j.exp_min), parse_num(j.exp_max)
    return xf.classify_bounds(lo, hi, hi is None, limit)


def _extract_comp(j: Job) -> None:
    is_intern = j.job_kind == "Internship"
    text = f"{j.title}\n{j.full_description}"
    comp = sf.resolve_compensation(j.hint_salary, text, is_intern)
    j.sal_min = fmt_num(comp.min)
    j.sal_max = fmt_num(comp.max)
    j.sal_currency, j.sal_period, j.sal_basis = comp.currency, comp.period, comp.basis
    j.sal_disclosure = comp.disclosure


def _comp_from_row(j: Job) -> sf.Compensation:
    return sf.Compensation(parse_num(j.sal_min), parse_num(j.sal_max), j.sal_currency, j.sal_period,
                           j.sal_basis or "Unspecified", j.sal_disclosure or sf.NOT_DISCLOSED)


def evaluate_job(j: Job, ctx: EvalContext) -> None:
    """Fill every derived column of ``j`` and set ``j.category``."""
    s = ctx.settings
    exclusions: list[str] = []
    flags: list[str] = []

    # ---------------- employment type / location / function
    fm: FunctionMatch = detect_function(j.title, j.full_description)
    if fm.strength == "none" and not fm.irrelevant and ctx.ai:
        label = ctx.ai.suggest_function(j.title, j.full_description)
        if label:
            fm = FunctionMatch(label, "description", True, False)
    j.function = fm.name
    if j.job_kind == "Other":
        exclusions.append("Other: employment type not supported (part-time/contract/freelance)")
    location_unclear = j.india_remote == "Unclear"
    if j.india_remote == "No":
        exclusions.append("Location restriction (outside India)")
    elif location_unclear:
        flags.append("Location: remote role / location with no stated eligibility for candidates in India - verify before applying")
    if fm.strength == "none":
        exclusions.append("Irrelevant position")

    # ---------------- experience
    if j.dirty or j.is_new_sighting and not j.exp_source:
        _extract_experience(j, ctx)
    exp_status, exp_reason = _rederive_experience(j, s.maximum_experience_years)
    j.exp_status = exp_status
    j.exp_display = _exp_display(j) or j.exp_display
    if exp_status == xf.EXCLUDED:
        exclusions.append(f"Experience above {fmt_num(s.maximum_experience_years)} years ({exp_reason})")
    elif exp_status == xf.VERIFY:
        flags.append("Experience: " + exp_reason)

    # ---------------- compensation
    if j.dirty or (j.is_new_sighting and not j.sal_disclosure):
        _extract_comp(j)
    comp = _comp_from_row(j)
    comp_status = sf.UNDISCLOSED
    if j.job_kind == "Internship":
        dur = sf.internship_duration_months(j.full_description)
        res = sf.evaluate_internship(comp, s.minimum_internship_stipend_inr_monthly, dur)
        j.stipend_status = res.status
        j.sal_status = ""
        j.stipend_min, j.stipend_max = fmt_num(res.stipend_min and round(res.stipend_min)), fmt_num(res.stipend_max and round(res.stipend_max))
        j.stipend_display = sf.display_range(*(round(x) if x is not None else None for x in (res.stipend_min, res.stipend_max)))
        j.ctc_min_lpa = j.ctc_max_lpa = j.ctc_display = ""
        comp_status, comp_reason = res.status, res.reason
        if res.status == sf.EXCLUDED:
            exclusions.append("Unpaid internship" if res.disclosure == sf.UNPAID else
                              f"Internship stipend below ₹{s.minimum_internship_stipend_inr_monthly:,.0f}/month")
    elif j.job_kind == "Full-time":
        res = sf.evaluate_fulltime(comp, s.minimum_ctc_lpa)
        j.sal_status = res.status
        j.stipend_status = ""
        j.ctc_min_lpa, j.ctc_max_lpa = fmt_num(res.ctc_min_lpa and round(res.ctc_min_lpa, 2)), fmt_num(res.ctc_max_lpa and round(res.ctc_max_lpa, 2))
        j.ctc_display = sf.display_range(*(round(x, 2) if x is not None else None for x in (res.ctc_min_lpa, res.ctc_max_lpa)))
        j.stipend_min = j.stipend_max = j.stipend_display = ""
        comp_status, comp_reason = res.status, res.reason
        if res.status == sf.EXCLUDED:
            exclusions.append(f"Full-time salary below ₹{fmt_num(s.minimum_ctc_lpa)} LPA" if comp.disclosure != sf.UNPAID else "Unpaid role")
    else:
        comp_reason = ""
    if comp_status in (sf.VERIFY, sf.UNDISCLOSED):
        flags.append(("Stipend: " if j.job_kind == "Internship" else "Salary: ") + comp_reason)

    # ---------------- company
    cres = evaluate_company(j.company, j.full_description, j.hint_company, ctx.registry,
                            s.startup_min_age_years, s.startup_min_employees, ctx.today, s.company_seed_mode)
    j.company_classification = cres.classification
    j.startup_status = cres.startup_status
    j.company_evidence = cres.evidence
    j.company_founded = cres.founded or j.company_founded
    j.company_age = cres.age_text
    j.employee_count = cres.employees_text or j.employee_count
    if cres.status == "Excluded":
        exclusions.append("Ineligible startup (" + cres.reason + ")")
    elif cres.status == "Verification Required":
        flags.append("Company: " + cres.reason)
    if cres.unverified and cres.status == "Eligible":
        flags.append("Company: classification comes from the UNVERIFIED bundled seed - confirm it in the Company Registry tab")

    # ---------------- score (always computed, only ranks)
    sc = score_job(j, fm, ctx.today)
    j.match_score = sc.score
    j.match_explanation = sc.explanation

    # ---------------- category
    j.exclusion_reason = "; ".join(exclusions)
    j.verification_flags = "; ".join(flags)
    if exclusions:
        j.category = "Excluded"
    elif exp_status == xf.VERIFY:
        j.category = "Experience Verification Required"
    elif location_unclear:
        j.category = "Location Verification Required"
    elif cres.status == "Verification Required":
        j.category = "Company Verification Required"
    elif comp_status == sf.VERIFY:
        j.category = "Salary Verification Required"
    elif comp_status == sf.UNDISCLOSED:
        j.category = "Salary Undisclosed"
    elif j.job_kind == "Internship":
        j.category = "Paid Internships"
    elif sc.score >= s.strong_match_min_score:
        j.category = "Strong Matches"
    elif sc.score >= s.possible_match_min_score:
        j.category = "Possible Matches"
    else:
        j.category = "Excluded"
        j.exclusion_reason = f"Irrelevant position (match score {sc.score} below {s.possible_match_min_score})"
    j.content_hash = material_hash(j)
    j.dirty = False


def material_hash(j: Job) -> str:
    """Hash of the fields whose change justifies re-sending a job in the email."""
    return short_hash(j.title, j.company, j.category, j.ctc_display, j.stipend_display, j.exp_display,
                      j.apply_url, j.location, j.job_status)


def is_out_of_scope(j: Job) -> bool:
    """True when the job is outside India / not our functions / unsupported type (counted, not stored)."""
    return j.category == "Excluded" and any(r in j.exclusion_reason for r in
                                            ("Location restriction", "Irrelevant position", "Other: employment"))
