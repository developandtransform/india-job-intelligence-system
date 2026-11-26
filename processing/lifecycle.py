"""History: merges one run's jobs into the permanent master, tracking first/last seen, closure and reposts.

Guarantees
----------
* Nothing is ever deleted. Jobs that disappear are marked ``Closed`` / ``Possibly Closed``.
* The user-owned columns (Application Status, Notes) are never modified here.
* A board that FAILED to load can never close jobs - only boards listed in ``completed_scopes`` can.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from config.settings import Settings
from core.models import MAX_CELL_CHARS, Job
from core.textutil import norm_company, norm_text, short_hash
from processing.deduplication import JobIndex, dedupe_batch, merge_into, sources_label
from processing.eligibility import EvalContext, evaluate_job, is_out_of_scope

log = logging.getLogger("lifecycle")


@dataclass
class MergeStats:
    incoming: int = 0
    after_batch_dedup: int = 0
    new: int = 0
    updated: int = 0
    seen_again: int = 0
    reactivated: int = 0
    reposts: int = 0
    closed: int = 0
    possibly_closed: int = 0
    dropped_out_of_scope: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    new_ids: list[str] = field(default_factory=list)
    changed_ids: list[str] = field(default_factory=list)
    dropped_by_source: dict[str, dict[str, int]] = field(default_factory=dict)


def _fingerprint(j: Job) -> str:
    return short_hash(norm_company(j.company), norm_text(j.title), (j.city or j.location or "").lower())


def _bump(d: dict[str, int], k: str) -> None:
    d[k] = d.get(k, 0) + 1


def _refresh_text_if_edited(existing: Job, inc: Job) -> None:
    """The employer edited the SAME posting (same source key): take the new text so salary / experience / location changes
    are noticed and re-evaluated. Guards against flapping: only the same source, and never a much shorter text (a
    list-page snippet replacing a full description). Hints from that fresh sighting travel with the text."""
    if not (set(inc.source_keys) & set(existing.source_keys)):
        return
    new = (inc.full_description or inc.description or "")
    old = (existing.full_description or existing.description or "")
    if not new or not old or new[:MAX_CELL_CHARS] == old[:MAX_CELL_CHARS] or len(new) < 0.6 * len(old):
        return
    existing.full_description = existing.description = new
    existing.hint_salary, existing.hint_exp_text = inc.hint_salary or existing.hint_salary, inc.hint_exp_text or existing.hint_exp_text
    if inc.hint_exp_min is not None or inc.hint_exp_max is not None:
        existing.hint_exp_min, existing.hint_exp_max = inc.hint_exp_min, inc.hint_exp_max
    existing.dirty = True


def _refresh_from_incoming(existing: Job, inc: Job, today: str) -> None:
    """Update an existing master job with a fresh sighting."""
    before = existing.content_hash
    _refresh_text_if_edited(existing, inc)
    merge_into(existing, inc)
    if inc.title and inc.title != existing.title:
        existing.title = inc.title
        existing.dirty = True
    if inc.location and inc.location != existing.location:
        existing.location = inc.location
        existing.city, existing.state, existing.country = inc.city, inc.state, inc.country
        existing.india_remote, existing.work_arrangement = inc.india_remote, inc.work_arrangement
    existing.job_kind = inc.job_kind or existing.job_kind
    existing.hint_country = inc.hint_country or existing.hint_country
    existing.hint_remote = inc.hint_remote if inc.hint_remote is not None else existing.hint_remote
    existing.last_seen = today
    existing.missed_runs = 0
    existing._before_hash = before  # type: ignore[attr-defined]


def merge_into_master(master: dict[str, Job], incoming: list[Job], completed_scopes: set[str], today: str,
                      ctx: EvalContext, settings: Settings, source_of_job: dict[str, str] | None = None) -> MergeStats:
    st = MergeStats(incoming=len(incoming))
    unique = dedupe_batch(incoming)
    st.after_batch_dedup = len(unique)
    index = JobIndex(list(master.values()))
    by_fp: dict[str, list[Job]] = {}
    for j in master.values():
        by_fp.setdefault(_fingerprint(j), []).append(j)
    touched: set[str] = set()
    evaluated: set[str] = set()

    for inc in unique:
        match = index.find(inc)
        if match is not None:
            was_closed = match.job_status in ("Closed", "Possibly Closed")
            _refresh_from_incoming(match, inc, today)
            if was_closed:
                match.job_status = "Active"
                st.reactivated += 1
            touched.add(match.internal_id)
            index.add(match)
            continue
        # brand new posting (maybe a repost of a closed one)
        inc.is_new_sighting = True
        inc.dirty = True
        evaluate_job(inc, ctx)
        evaluated.add(inc.internal_id)
        if is_out_of_scope(inc) and not settings.store_out_of_scope_jobs:
            st.dropped_out_of_scope += 1
            reason = inc.exclusion_reason.split(" (")[0].split(";")[0]
            _bump(st.dropped_by_reason, reason)
            _bump(st.dropped_by_source.setdefault(inc.source.split(",")[0], {}), reason)
            continue
        prior = [m for m in by_fp.get(_fingerprint(inc), []) if m.job_status == "Closed"]
        if prior:
            inc.repost_count = max(p.repost_count for p in prior) + 1
            st.reposts += 1
        while inc.internal_id in master:           # id collision with an unrelated job
            inc.internal_id = short_hash(inc.internal_id, today)
        inc.first_seen = inc.last_seen = inc.last_updated = today
        inc.date_discovered = inc.date_discovered or today
        inc.job_status = "Active"
        inc.application_status = "New"
        inc.dup_group_id = inc.dup_group_id or inc.internal_id
        inc.is_new_sighting = False
        master[inc.internal_id] = inc
        by_fp.setdefault(_fingerprint(inc), []).append(inc)
        index.add(inc)
        touched.add(inc.internal_id)
        evaluated.add(inc.internal_id)
        st.new += 1
        st.new_ids.append(inc.internal_id)

    # closure / staleness for jobs not seen this run
    for j in master.values():
        if j.internal_id in touched:
            continue
        if j.job_status == "Closed":
            continue
        scopes_done = [s for s in j.source_scopes if s in completed_scopes]
        if scopes_done:
            j.missed_runs += 1
            if j.missed_runs >= settings.close_after_missed_runs:
                j.job_status = "Closed"
                st.closed += 1
            continue
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(j.last_seen or j.first_seen or today)).days
        except ValueError:
            age = 0
        if j.job_status == "Active" and age >= settings.stale_after_days:
            j.job_status = "Possibly Closed"
            st.possibly_closed += 1

    # (re)evaluate everything else: rules, thresholds and the company registry may have changed
    for j in master.values():
        if j.internal_id in evaluated:
            continue
        before = getattr(j, "_before_hash", None) or j.content_hash
        evaluate_job(j, ctx)
        if j.internal_id in touched:
            j.source = sources_label(j)
            if j.content_hash != before:
                j.last_updated = today
                st.updated += 1
                st.changed_ids.append(j.internal_id)
            else:
                st.seen_again += 1
            j.last_seen = today
        elif j.content_hash != before and before:
            j.last_updated = today
    for jid in st.new_ids:
        master[jid].source = sources_label(master[jid])
    return st
