"""The daily run: load history -> fetch -> normalise -> merge -> evaluate -> save -> views -> digest."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import Settings
from connectors.base import Connector, ConnectorResult
from core.models import Job
from integrations import sheet_schema as S
from integrations.email import send_email
from processing.ai_cache import AICache
from integrations.store import Store
from processing.company_verification import CompanyRegistry
from processing.eligibility import EvalContext
from processing import monitoring as mon
from processing.lifecycle import MergeStats, merge_into_master
from processing.normalizer import normalize_job
from reports.daily_digest import Digest, build_digest

log = logging.getLogger("pipeline")


@dataclass
class RunOutcome:
    today: str
    results: list[ConnectorResult] = field(default_factory=list)
    stats: MergeStats | None = None
    save_info: dict = field(default_factory=dict)
    digest: Digest | None = None
    email_status: str = "not attempted"
    registry_added: int = 0
    jobs: list[Job] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    ai_rows: list = field(default_factory=list)
    skipped_ops: list = field(default_factory=list)      # what a dry run deliberately did NOT do
    blocked: list = field(default_factory=list)          # dry run stopped safely: spreadsheet structure problems
    changes: list = field(default_factory=list)          # field-level change log (salary, experience, location, status ...)
    issues: list = field(default_factory=list)           # everything monitoring found this run
    alerts_due: list = field(default_factory=list)       # the subset not already alerted in the last few days
    alert_status: str = "none"
    registry_queue: list = field(default_factory=list)   # companies to verify first (most relevant jobs first)


def today_in_zone(tz: str) -> str:
    try:
        return datetime.now(ZoneInfo(tz)).date().isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).date().isoformat()


def _stat_rows(today: str, results: list[ConnectorResult], new_by_source: dict[str, int], dropped: dict[str, dict[str, int]]) -> list[list[str]]:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    rows = []
    for r in results:
        sources = sorted({k.split(":", 1)[0] for j in r.jobs for k in j.source_keys} or {r.name})
        new = sum(new_by_source.get(s, 0) for s in sources)
        drop = sum(sum(dropped.get(s, {}).values()) for s in sources)
        rows.append([today, now, r.name, ", ".join(sources), r.status, str(len(r.jobs)), str(new), str(drop),
                     str(r.dropped_prefilter), str(r.requests), f"{r.cost_usd:.4f}", str(r.seconds),
                     str(len(r.errors)), (r.note or "; ".join(r.errors[:2]))[:300]])
    return rows


def ai_stat_rows(today: str, ai, usage) -> list[list[str]]:
    """Source Statistics rows for AI, built from counters measured during this run (the 'Notes' column holds the detail;
    token figures are chars/4 and labelled as estimates)."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    n_seen, n_needed = len(usage.seen), len(usage.needed)
    base = [today, now]
    if ai is None:
        return [base + ["AI", "summary", "off", "0", "0", "0", "0", "0", "0", "0", "0",
                        f"AI off or no key. jobs_evaluated={n_seen}; jobs_rules_only={n_seen}"]]
    u = ai.router.usage()
    calls = sum(v["calls"] for v in u.values())
    fails = sum(v["failures"] for v in u.values())
    http_reqs = sum(v["http_requests"] for v in u.values())
    status = "ok" if calls == 0 or fails < calls else "all failed"
    rows = [base + ["AI", "summary", status, "0", "0", "0", "0", str(http_reqs), "0", "0", str(fails),
                    (f"jobs_evaluated={n_seen}; total_ai_calls={calls}; cache_hits={usage.cache_hits}; jobs_rules_only={usage.rules_only}; "
                     f"jobs_requiring_ai={n_needed}; jobs_asked_provider={len(usage.asked)}; fallback_attempts={ai.router.fallback_attempts}; "
                     f"ai_function_accepted={len(usage.fn_accepted)}; ai_function_uncertain={len(usage.fn_uncertain)}; ai_function_rejected={len(usage.fn_rejected)}; "
                     f"skipped_already_excluded={usage.skipped_ineligible}; skipped_thin_text={usage.skipped_thin_text}; "
                     f"skipped_ai_unavailable={usage.skipped_unavailable}; est_prompt_tokens~{usage.prompt_chars // 4}")]]
    for name, v in u.items():
        rows.append(base + ["AI", name, "disabled" if v["disabled"] else ("ok" if v["failures"] < max(v["calls"], 1) else "failed"),
                            "0", "0", "0", "0", str(v["http_requests"]), "0", "0", str(v["failures"]),
                            f"model={v['model']}; calls={v['calls']}; successes={v['successes']}; failures={v['failures']}; "
                            f"retries={v['retries']}; fell_back_after_failure={v['fallbacks']}"
                            + (f"; switched off: {v['disabled']}" if v["disabled"] else "")])
    return rows


def run_pipeline(settings: Settings, store: Store, connectors: list[Connector], today: str | None = None,
                 log_buffer=None, send_mail: bool = True, ai=None) -> RunOutcome:
    today = today or today_in_zone(settings.timezone)
    out = RunOutcome(today)
    if settings.dry_run:
        # READ-ONLY: never create/repair tabs, headers or formatting. Problems are reported and the run stops safely.
        out.skipped_ops = ["spreadsheet preparation (no tabs, headers or formatting created or changed)",
                           "job writes and manual-column updates", "statistics, log, change-log and AI-cache writes",
                           "e-mail (digest and alerts)", "paid connectors (Apify) - never executed"]
        if ai is not None and not settings.dry_run_allow_ai:
            ai = None
        if ai is None:
            out.skipped_ops.append("AI requests (set DRY_RUN_ALLOW_AI=true to allow them in a dry run)")
        problems = store.validate_structure(check_ai_cache=ai is not None)
        if problems:
            out.blocked = problems
            out.email_status = "dry run stopped: spreadsheet structure problem"
            log.error("Dry run stopped safely (nothing was changed): %s", "; ".join(problems))
            return out
    else:
        store.prepare()

    # ---- history
    rows = store.load_master()
    master: dict[str, Job] = {}
    for r in rows:
        j = Job.from_record(r)
        if j.internal_id:
            master[j.internal_id] = j
    log.info("Loaded %d existing jobs from the sheet", len(master))
    registry = CompanyRegistry()
    registry.load_sheet_rows(store.load_registry_rows())
    registry.load_seed()
    if ai is not None:
        try:
            ai.cache = AICache(store.load_ai_cache(), today, settings.ai_cache_max_entries, settings.ai_cache_ttl_days)
            log.info("AI cache: %d remembered answers", len(ai.cache))
        except Exception as exc:  # noqa: BLE001 - a broken cache tab only means more AI questions, never a failed run
            log.warning("Could not read the AI Cache tab (%s); continuing without it", type(exc).__name__)
    ctx = EvalContext(settings, registry, datetime.fromisoformat(today).date(), ai)

    # ---- fetch (every connector isolated)
    known = {k for j in master.values() for k in j.source_keys}
    incoming: list[Job] = []
    scopes: set[str] = set()
    for c in connectors:
        c.known_keys = known
        if settings.dry_run and not getattr(c, "free", True):          # paid sources never run in a dry run, whatever their own settings say
            res = ConnectorResult(c.name)
            res.status, res.note = "skipped", "dry run: paid connector not executed"
        else:
            res = c.run()
        out.results.append(res)
        for j in res.jobs:
            try:
                incoming.append(normalize_job(j, today))
            except Exception as exc:  # noqa: BLE001 - one malformed record must not stop the run
                log.warning("Skipped a malformed record from %s: %s", res.name, type(exc).__name__)
        scopes |= res.completed_scopes
    # ---- merge + evaluate
    before = mon.snapshot(master)
    st = merge_into_master(master, incoming, scopes, today, ctx, settings)
    out.changes = mon.diff_snapshot(before, master)
    out.stats = st
    jobs = list(master.values())
    out.jobs = jobs
    new_by_source: dict[str, int] = {}
    for jid in st.new_ids:
        for k in {x.split(":", 1)[0] for x in master[jid].source_keys} or {master[jid].source}:
            new_by_source[k] = new_by_source.get(k, 0) + 1
    log.info("Merge: incoming=%d unique=%d new=%d updated=%d closed=%d dropped=%d", st.incoming, st.after_batch_dedup,
             st.new, st.updated, st.closed, st.dropped_out_of_scope)

    out.ai_rows = ai_stat_rows(today, ai, ctx.usage)
    cost = round(sum(r.cost_usd for r in out.results), 4)
    out.summary = {
        "fetched": sum(len(r.jobs) for r in out.results), "new": st.new, "dropped": st.dropped_out_of_scope,
        "closed": st.closed, "apify_cost_usd": cost,
        "sources": [{"name": r.name, "status": r.status, "jobs": len(r.jobs)} for r in out.results],
        "errors": [f"{r.name}: {e}" for r in out.results for e in r.errors[:3]],
    }

    out.registry_queue = mon.registry_queue(jobs, registry)
    if out.registry_queue:
        log.info("Company Registry queue (most relevant jobs first): %s", "; ".join(
            f"{e['company']} ({e['relevant_jobs']})" for e in out.registry_queue[:8]))
    try:       # monitoring must never fail the run
        out.issues = mon.detect_issues(out.results, store.load_recent_stats(), today, out.ai_rows)
        out.alerts_due = mon.issues_to_send(out.issues, store.load_recent_alerts(), today)
    except Exception as exc:  # noqa: BLE001
        log.warning("Monitoring check skipped (%s)", type(exc).__name__)
    out.summary["issues"] = [i.text for i in out.issues]

    if settings.dry_run:
        out.email_status = "dry run: no e-mail sent"
        out.digest = build_digest(jobs, out.summary, settings, today, store.url())
        return out

    # ---- persist
    out.save_info = store.save_master(jobs)
    store.write_views(S.build_views(jobs, today))
    if registry.pending:
        out.registry_added = store.add_registry_names(list(registry.pending.values()), today)
    store.append_stats(_stat_rows(today, out.results, new_by_source, st.dropped_by_source) + out.ai_rows)
    if ai is not None and ai.cache.dirty:
        try:
            store.save_ai_cache(ai.cache.to_rows())
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not save the AI Cache tab (%s)", type(exc).__name__)

    # ---- e-mail, then record what was reported
    digest = build_digest(jobs, out.summary, settings, today, store.url())
    out.digest = digest
    if send_mail and settings.email_enabled:
        ok, msg = send_email(settings, digest.subject, digest.html, digest.text)
        out.email_status = msg
        if ok and digest.marks:
            try:
                store.mark_reported(digest.marks)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not record reported jobs: %s", type(exc).__name__)
        elif not ok:
            log.warning(msg)
    else:
        out.email_status = "email disabled or skipped"
    now_iso = f"{today} {datetime.now(timezone.utc).strftime('%H:%M:%S')}"     # run date (not the clock) so alert de-duplication is consistent
    extra_logs = mon.change_log_rows(out.changes, now_iso)
    if out.alerts_due:
        if send_mail and settings.email_enabled:
            subject, html, text = mon.alert_email(out.alerts_due, today, store.url())
            ok, msg = send_email(settings, subject, html, text)
            out.alert_status = "sent" if ok else f"not sent: {msg}"
        else:
            out.alert_status = "email disabled or skipped (alerts are in System Logs)"
            ok = not (send_mail and settings.email_enabled)
        if ok:                                   # only remember it as alerted when the mail really went out (or mail is off)
            extra_logs += mon.alert_log_rows(out.alerts_due, now_iso)
    log_rows = (log_buffer.rows if log_buffer is not None else []) + extra_logs
    if log_rows:
        try:
            store.append_logs(log_rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write System Logs: %s", type(exc).__name__)
    return out
