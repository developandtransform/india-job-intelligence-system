"""Builds the daily e-mail digest (HTML + plain text) from the master job list.

A job is included when it is Active, not already applied/rejected/closed by you, not Excluded, and it is NEW or
MATERIALLY CHANGED since it was last e-mailed (``Last Reported Hash`` != ``Content Hash``). Jobs cut off by the
per-category limit are NOT marked as reported, so they appear the next day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from config.settings import Settings
from core.models import Job
from core.textutil import safe_url

SECTIONS = [
    ("Strong Matches", "Strong Matches", "Meets every rule and scores high."),
    ("Possible Matches", "Possible Matches", "Meets every rule; weaker fit."),
    ("Paid Internships", "Paid Internships", "Stipend of at least the configured monthly minimum."),
    ("Salary Undisclosed", "Salary Undisclosed", "Everything else fits, but pay is not stated. Check before applying."),
    ("Salary Verification Required", "Salary Verification Required", "The pay range crosses the threshold or is unclear."),
    ("Experience Verification Required", "Experience Verification Required", "Experience requirement missing or ambiguous."),
    ("Location Verification Required", "Location Verification Required", "Remote or vague location with no stated eligibility for candidates in India."),
    ("Company Verification Required", "Company Verification Required", "Startup age/size could not be verified. Fill the 'Company Registry' tab."),
]
SKIP_APP_STATUS = {"Applied", "Interview", "Offer", "Rejected", "Closed"}


@dataclass
class Digest:
    subject: str
    html: str
    text: str
    marks: dict[str, tuple[str, str]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0


def select_reportable(jobs: list[Job]) -> list[Job]:
    return [j for j in jobs if j.job_status == "Active" and j.category not in ("", "Excluded")
            and j.application_status not in SKIP_APP_STATUS and j.content_hash != j.last_reported_hash]


def _url(j: Job) -> str:
    return safe_url(j.apply_url) or safe_url(j.job_url)


def _pay(j: Job) -> str:
    return j.stipend_display or j.ctc_display or "Not disclosed"


def _line_text(j: Job) -> str:
    return (f"- [{j.match_score}] {j.title} | {j.company} | {j.location or 'n/a'} | {_pay(j)} | exp: {j.exp_display or 'n/a'}\n"
            f"    {j.match_explanation}\n    Apply: {_url(j) or '(no valid link)'}")


def _row_html(j: Job) -> str:
    url = escape(_url(j), quote=True)
    link = f'<a href="{url}">{escape(j.title)}</a>' if url else escape(j.title)
    flags = f'<div style="color:#8a5a00;font-size:12px">{escape(j.verification_flags)}</div>' if j.verification_flags else ""
    return (f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eee"><b>{link}</b><br>'
            f'{escape(j.company)} &middot; {escape(j.location or "n/a")}'
            f'<div style="color:#555;font-size:12px">{escape(j.match_explanation)}</div>{flags}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;white-space:nowrap">{escape(_pay(j))}<br>'
            f'<span style="color:#555;font-size:12px">exp: {escape(j.exp_display or "n/a")}</span></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:center"><b>{j.match_score}</b></td></tr>')


def build_digest(jobs: list[Job], summary: dict, settings: Settings, today: str, sheet_url: str = "") -> Digest:
    cap = max(1, settings.email_max_jobs_per_category)
    cand = select_reportable(jobs)
    by_cat: dict[str, list[Job]] = {}
    for j in cand:
        by_cat.setdefault(j.category, []).append(j)
    for lst in by_cat.values():
        lst.sort(key=lambda x: (-x.match_score, x.title))
    marks: dict[str, tuple[str, str]] = {}
    counts: dict[str, int] = {}
    html_parts: list[str] = []
    text_parts: list[str] = []
    shown_total = 0
    total_cap = max(1, getattr(settings, "email_max_jobs_total", 40))
    for cat, title, blurb in SECTIONS:
        lst = by_cat.get(cat, [])
        counts[cat] = len(lst)
        if not lst:
            continue
        room = total_cap - shown_total           # sections are in priority order, so lower ones are trimmed first
        if room <= 0:
            html_parts.append(f'<div style="font-size:12px;color:#666;margin-top:10px">{escape(title)}: {len(lst)} more in the Google Sheet / tomorrow\'s digest</div>')
            text_parts.append(f"== {title}: {len(lst)} more in the sheet / tomorrow ==")
            continue
        if cat == "Company Verification Required":
            groups: dict[str, list[Job]] = {}
            for j in lst:
                groups.setdefault(j.company, []).append(j)
            names = sorted(groups, key=lambda c: -max(x.match_score for x in groups[c]))
            shown, chosen = 0, []
            for n in names:
                if shown >= min(cap, room):
                    break
                chosen.append(n)
                shown += len(groups[n])
            shown_jobs = [j for n in chosen for j in groups[n]]
        else:
            shown_jobs = lst[:cap]
        shown_jobs = shown_jobs[:room]
        for j in shown_jobs:
            marks[j.internal_id] = (j.content_hash, today)
        shown_total += len(shown_jobs)
        more = len(lst) - len(shown_jobs)
        html_parts.append(f'<h3 style="margin:18px 0 2px">{escape(title)} ({len(lst)})</h3>'
                          f'<div style="color:#666;font-size:12px;margin-bottom:4px">{escape(blurb)}</div>'
                          f'<table style="border-collapse:collapse;width:100%;font-size:13px">{"".join(_row_html(j) for j in shown_jobs)}</table>'
                          + (f'<div style="font-size:12px;color:#666">+{more} more in the Google Sheet / tomorrow\'s digest</div>' if more else ""))
        text_parts.append(f"== {title} ({len(lst)}) ==\n{blurb}\n" + "\n".join(_line_text(j) for j in shown_jobs)
                          + (f"\n(+{more} more)" if more else ""))
    src_rows = summary.get("sources", [])
    health_html = "".join(f'<tr><td style="padding:2px 8px">{escape(str(s["name"]))}</td><td style="padding:2px 8px">{escape(str(s["status"]))}</td>'
                          f'<td style="padding:2px 8px;text-align:right">{s["jobs"]}</td></tr>' for s in src_rows)
    health_text = "\n".join(f"  {s['name']}: {s['status']} ({s['jobs']} jobs)" for s in src_rows)
    cost = summary.get("apify_cost_usd", 0.0)
    head = (f"Fetched {summary.get('fetched', 0)} postings, {summary.get('new', 0)} new in scope, "
            f"{summary.get('dropped', 0)} dropped as out of scope, {summary.get('closed', 0)} closed. "
            f"Apify spend this run: ${cost:.2f}.")
    issues = summary.get("issues", [])
    issue_html = ("<h3>Needs attention</h3><ul>" + "".join(f"<li>{escape(i)}</li>" for i in issues[:8]) + "</ul>") if issues else ""
    issue_text = ("\nNeeds attention:\n" + "\n".join(f"  - {i}" for i in issues[:8])) if issues else ""
    overview = " | ".join(f"{t}: {counts.get(c, 0)}" for c, t, _ in SECTIONS if counts.get(c, 0))
    head += f" Waiting to be reported - {overview}." if overview else ""
    errs = summary.get("errors", [])
    err_html = ("<h3>Source problems</h3><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errs[:10]) + "</ul>") if errs else ""
    err_text = ("\nSource problems:\n" + "\n".join(f"  - {e}" for e in errs[:10])) if errs else ""
    nothing = "" if shown_total else "<p>No new or changed matching jobs today.</p>"
    link = f'<p><a href="{escape(sheet_url, quote=True)}">Open the Google Sheet</a></p>' if sheet_url.startswith("https://") else ""
    html = (f'<html><body style="font-family:Arial,sans-serif;max-width:820px"><h2>India Job Intelligence - {escape(today)}</h2>'
            f'<p>{escape(head)}</p>{issue_html}{nothing}{"".join(html_parts)}'
            f'<h3>Source health</h3><table style="font-size:12px">{health_html}</table>{err_html}{link}'
            f'<p style="color:#888;font-size:11px">Experience &le; {settings.maximum_experience_years:g} yrs, CTC &ge; {settings.minimum_ctc_lpa:g} LPA, '
            f'stipend &ge; Rs {settings.minimum_internship_stipend_inr_monthly:,.0f}/month. Verify details on the employer page before applying. '
            f'This tool never applies to jobs for you.</p></body></html>')
    text = (f"India Job Intelligence - {today}\n{head}\n\n" + ("\n\n".join(text_parts) if text_parts else "No new or changed matching jobs today.")
            + f"\n{issue_text}\n\nSource health:\n{health_text}{err_text}\n" + (f"\nSheet: {sheet_url}\n" if sheet_url else ""))
    subject = f"Job digest {today}: {shown_total} new/changed ({counts.get('Strong Matches', 0)} strong)"
    return Digest(subject, html, text, marks, counts, shown_total)
