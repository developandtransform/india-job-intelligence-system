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
from integrations.store import Store
from processing.company_verification import CompanyRegistry
from processing.eligibility import EvalContext
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


def run_pipeline(settings: Settings, store: Store, connectors: list[Connector], today: str | None = None,
                 log_buffer=None, send_mail: bool = True, ai=None) -> RunOutcome:
    today = today or today_in_zone(settings.timezone)
    out = RunOutcome(today)
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
    ctx = EvalContext(settings, registry, datetime.fromisoformat(today).date(), ai)

    # ---- fetch (every connector isolated)
    known = {k for j in master.values() for k in j.source_keys}
    incoming: list[Job] = []
    scopes: set[str] = set()
    for c in connectors:
        c.known_keys = known
        res = c.run()
        out.results.append(res)
        for j in res.jobs:
            try:
                incoming.append(normalize_job(j, today))
            except Exception as exc:  # noqa: BLE001 - one malformed record must not stop the run
                log.warning("Skipped a malformed record from %s: %s", res.name, type(exc).__name__)
        scopes |= res.completed_scopes
    # ---- merge + evaluate
    st = merge_into_master(master, incoming, scopes, today, ctx, settings)
    out.stats = st
    jobs = list(master.values())
    out.jobs = jobs
    new_by_source: dict[str, int] = {}
    for jid in st.new_ids:
        for k in {x.split(":", 1)[0] for x in master[jid].source_keys} or {master[jid].source}:
            new_by_source[k] = new_by_source.get(k, 0) + 1
    log.info("Merge: incoming=%d unique=%d new=%d updated=%d closed=%d dropped=%d", st.incoming, st.after_batch_dedup,
             st.new, st.updated, st.closed, st.dropped_out_of_scope)

    cost = round(sum(r.cost_usd for r in out.results), 4)
    out.summary = {
        "fetched": sum(len(r.jobs) for r in out.results), "new": st.new, "dropped": st.dropped_out_of_scope,
        "closed": st.closed, "apify_cost_usd": cost,
        "sources": [{"name": r.name, "status": r.status, "jobs": len(r.jobs)} for r in out.results],
        "errors": [f"{r.name}: {e}" for r in out.results for e in r.errors[:3]],
    }

    if settings.dry_run:
        out.email_status = "dry run: nothing written, no e-mail"
        out.digest = build_digest(jobs, out.summary, settings, today, store.url())
        return out

    # ---- persist
    out.save_info = store.save_master(jobs)
    store.write_views(S.build_views(jobs, today))
    if registry.pending:
        out.registry_added = store.add_registry_names(list(registry.pending.values()), today)
    store.append_stats(_stat_rows(today, out.results, new_by_source, st.dropped_by_source))

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
    if log_buffer is not None:
        try:
            store.append_logs(log_buffer.rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write System Logs: %s", type(exc).__name__)
    return out
