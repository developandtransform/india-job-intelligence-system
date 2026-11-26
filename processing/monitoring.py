"""Source monitoring, alert de-duplication, change log and registry work queue (pure functions, no network).

History comes from the Source Statistics tab (one row per connector per run). An alert is e-mailed only when its key has
not been alerted in the last ``REMIND_AFTER_DAYS`` days (the 'System Logs' tab records every alert as component 'alert'),
so the same problem does not mail you every morning.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

from core.models import Job
from processing.company_verification import CompanyRegistry
from core.textutil import norm_company

ZERO_RUNS_FOR_ALERT = 3          # this run + the two before it returned no jobs
DROP_RATIO = 0.30                # fetched < 30 % of the recent median ...
DROP_MIN_MEDIAN = 20             # ... when the median is at least this large
REMIND_AFTER_DAYS = 3
TRACKED_FIELDS = [("category", "Category"), ("job_status", "Job Status"), ("ctc_display", "CTC LPA"),
                  ("stipend_display", "Stipend"), ("exp_display", "Experience"), ("location", "Location")]
MAX_CHANGES_PER_RUN = 200

_COL = {"date": 0, "connector": 2, "source": 3, "status": 4, "fetched": 5}


@dataclass(frozen=True)
class Issue:
    key: str            # stable id, e.g. "zero:jobspy"
    severity: str       # "error" | "warning"
    text: str


def _history(rows: list[list[str]], connector: str, today: str) -> list[tuple[str, str, int]]:
    """(date, status, jobs) for earlier runs of one connector, oldest first, today excluded."""
    out = []
    for r in rows:
        if len(r) > 5 and r[_COL["connector"]] == connector and r[_COL["date"]] < today:
            try:
                out.append((r[_COL["date"]], r[_COL["status"]], int(float(r[_COL["fetched"]] or 0))))
            except ValueError:
                continue
    return sorted(out)


def detect_issues(results: list, history: list[list[str]], today: str, ai_rows: list[list[str]] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for r in results:
        if r.status == "skipped":
            continue
        n = len(r.jobs)
        past = _history(history, r.name, today)
        if r.status == "failed":
            msg = "; ".join(r.errors[:2]) or "no details"
            issues.append(Issue(f"failed:{r.name}", "error", f"{r.name}: the connector failed ({msg[:160]})"))
        elif r.errors:
            issues.append(Issue(f"errors:{r.name}", "warning", f"{r.name}: finished with {len(r.errors)} error(s), first: {r.errors[0][:160]}"))
        if n == 0 and len(past) >= ZERO_RUNS_FOR_ALERT - 1 and all(j == 0 for _, _, j in past[-(ZERO_RUNS_FOR_ALERT - 1):]):
            issues.append(Issue(f"zero:{r.name}", "warning", f"{r.name}: no jobs in {ZERO_RUNS_FOR_ALERT} runs in a row"))
        recent = [j for _, _, j in past[-5:] if j > 0]
        if n > 0 and len(recent) >= 3:
            med = statistics.median(recent)
            if med >= DROP_MIN_MEDIAN and n < DROP_RATIO * med:
                issues.append(Issue(f"drop:{r.name}", "warning", f"{r.name}: only {n} jobs today, usually about {int(med)} (sudden drop)"))
    for row in ai_rows or []:
        if len(row) > 4 and row[2] == "AI" and row[3] == "summary" and row[4] == "all failed":
            issues.append(Issue("ai:outage", "warning", "AI: every AI provider failed today (the rules still ran; nothing was lost)"))
    return issues


def alerted_recently(alert_log: list[list[str]], today: str, days: int = REMIND_AFTER_DAYS) -> set[str]:
    """Keys alerted within ``days``. ``alert_log`` rows are System Logs rows [timestamp, level, component, message]."""
    keys: set[str] = set()
    for r in alert_log:
        if len(r) < 4 or r[2] != "alert":
            continue
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(r[0][:10])).days
        except ValueError:
            continue
        if 0 <= age < days:
            keys.add(r[3].split(" | ", 1)[0])
    return keys


def issues_to_send(issues: list[Issue], alert_log: list[list[str]], today: str) -> list[Issue]:
    seen = alerted_recently(alert_log, today)
    return [i for i in issues if i.key not in seen]


def alert_log_rows(issues: list[Issue], now_iso: str) -> list[list[str]]:
    return [[now_iso, "WARNING" if i.severity != "error" else "ERROR", "alert", f"{i.key} | {i.text}"] for i in issues]


def alert_email(issues: list[Issue], today: str, sheet_url: str = "") -> tuple[str, str, str]:
    from html import escape
    subject = f"Job system alert: {len(issues)} issue(s) - {today}"
    lines = [f"- [{i.severity}] {i.text}" for i in issues]
    text = "These need a look (you will not be e-mailed about the same issue again for a few days):\n\n" + "\n".join(lines)
    if sheet_url:
        text += f"\n\nSheet: {sheet_url}\nSee 'Source Statistics' and 'System Logs'."
    html = "<p>These need a look (the same issue will not be e-mailed again for a few days):</p><ul>" + \
           "".join(f"<li><b>{escape(i.severity)}</b>: {escape(i.text)}</li>" for i in issues) + "</ul>"
    return subject, html, text


# ------------------------------------------------------------------ change log
def snapshot(master: dict[str, Job]) -> dict[str, tuple]:
    return {jid: tuple(getattr(j, f) for f, _ in TRACKED_FIELDS) for jid, j in master.items()}


def diff_snapshot(before: dict[str, tuple], master: dict[str, Job]) -> list[tuple[str, str, str, str, str]]:
    """(job id, 'title @ company', field label, old, new) for jobs that existed before this run. Capped."""
    out = []
    for jid, old in before.items():
        j = master.get(jid)
        if j is None:
            continue
        for (f, label), o in zip(TRACKED_FIELDS, old):
            n = getattr(j, f)
            if o != n:
                out.append((jid, f"{j.title} @ {j.company}", label, str(o), str(n)))
                if len(out) >= MAX_CHANGES_PER_RUN:
                    return out
    return out


def change_log_rows(changes: list[tuple], now_iso: str) -> list[list[str]]:
    return [[now_iso, "INFO", "change", f"{jid} | {title[:80]} | {label}: '{o[:60]}' -> '{n[:60]}'"] for jid, title, label, o, n in changes]


# ------------------------------------------------------------------ company registry work queue
RELEVANT = {"Strong Matches", "Possible Matches", "Paid Internships", "Salary Undisclosed", "Company Verification Required",
            "Location Verification Required", "Salary Verification Required", "Experience Verification Required"}


def registry_queue(jobs: list[Job], registry: CompanyRegistry, limit: int = 25) -> list[dict]:
    """Companies that still need classification / founding year / employee count, most relevant jobs first."""
    counts: dict[str, dict] = {}
    for j in jobs:
        if j.job_status in ("Closed",) or j.category not in RELEVANT or not j.company:
            continue
        key = norm_company(j.company)
        rec = registry.lookup(j.company)
        missing = []
        if rec is None or not rec.classification:
            missing.append("classification")
        if rec is None or not rec.founded:
            missing.append("founding year")
        if rec is None or not rec.employees:
            missing.append("employee count")
        if rec is not None and rec.verification_status == "Verified" and not missing:
            continue
        if not missing and rec is not None and rec.verification_status == "Verified":
            continue
        if not missing:
            missing.append("evidence URL")
        e = counts.setdefault(key, {"company": j.company, "relevant_jobs": 0, "strong": 0, "missing": missing})
        e["relevant_jobs"] += 1
        e["strong"] += j.category == "Strong Matches"
    rows = sorted(counts.values(), key=lambda e: (-e["relevant_jobs"], -e["strong"], e["company"].lower()))
    return rows[:limit]
