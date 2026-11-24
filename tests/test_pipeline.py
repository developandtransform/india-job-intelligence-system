"""History, manual-column preservation, closure rules, dedup, dry run, secrecy."""
import dataclasses

from integrations.store import MemoryStore
from processing.pipeline import run_pipeline
from tests.helpers import StubConnector, TODAY, mk, run, settings

GOOD = "1-2 years experience. CTC 10 LPA."


def test_manual_columns_survive_reruns():
    store = MemoryStore()
    run([mk("1", desc=GOOD)], store)
    jid = next(iter(store.master))
    store.master[jid]["Application Status"] = "Applied"
    store.master[jid]["Notes"] = "called HR"
    run([mk("1", desc=GOOD)], store, today="2026-11-25")
    assert store.master[jid]["Application Status"] == "Applied"
    assert store.master[jid]["Notes"] == "called HR"
    assert store.master[jid]["First Seen Date"] == TODAY
    assert store.master[jid]["Last Seen Date"] == "2026-11-25"
    assert len(store.master) == 1


def test_no_duplicates_across_sources_and_runs():
    a = mk("1", desc=GOOD, source="naukri")
    b = mk("2", desc=GOOD, source="indeed")          # same company/title/city/description, other source
    b.job_url = "https://other.example.org/x"
    out, store = run([a, b])
    assert len(store.master) == 1
    out2, store = run([a, b], store, today="2026-11-25")
    assert len(store.master) == 1


def test_different_locations_are_different_jobs():
    out, store = run([mk("1", location="Pune, India", desc=GOOD), mk("2", location="Mumbai, India", desc=GOOD)])
    assert len(store.master) == 2


def test_closed_only_after_completed_scope_and_n_misses():
    j = mk("1", desc=GOOD, source="greenhouse")
    j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    jid = next(iter(store.master))
    # board fetched OK but job missing, 1st miss -> still active
    run([], store, today="2026-11-25", scopes={"greenhouse:acme"})
    assert store.master[jid]["Job Status"] == "Active"
    run([], store, today="2026-11-26", scopes={"greenhouse:acme"})
    assert store.master[jid]["Job Status"] == "Closed"
    assert jid in store.master                      # never deleted


def test_failed_source_never_closes_jobs():
    j = mk("1", desc=GOOD, source="greenhouse")
    j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    for d in ("2026-11-25", "2026-11-26", "2026-11-27"):
        run([], store, today=d, scopes=())          # board failed: scope not completed
    assert next(iter(store.master.values()))["Job Status"] == "Active"


def test_crashing_connector_does_not_stop_run():
    store = MemoryStore()
    ok = StubConnector([mk("1", desc=GOOD)], name="ok")
    bad = StubConnector([], fail=True, name="bad")
    out = run_pipeline(settings(), store, [bad, ok], today=TODAY, send_mail=False)
    assert {r.name: r.status for r in out.results}["bad"] == "failed"
    assert len(store.master) == 1
    assert any(r[2] == "bad" for r in store.stats)


def test_dry_run_writes_nothing():
    store = MemoryStore()
    cfg = dataclasses.replace(settings(), dry_run=True)
    run_pipeline(cfg, store, [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=False)
    assert not store.master and not store.stats


def test_out_of_scope_not_stored_by_default_but_counted():
    out, store = run([mk("1", title="Chef", desc="Cook food 1 year experience")])
    assert not store.master and out.stats.dropped_out_of_scope == 1


def test_excluded_in_scope_jobs_are_kept_for_history():
    out, store = run([mk("1", desc="6+ years experience. CTC 30 LPA.")])
    assert next(iter(store.master.values()))["Category"] == "Excluded"


def test_threshold_change_reclassifies_existing_rows():
    store = MemoryStore()
    run([mk("1", desc="1-2 years experience. CTC 9 LPA.")], store)
    assert next(iter(store.master.values()))["Category"] == "Strong Matches"
    run([], store, today="2026-11-25", cfg=dataclasses.replace(settings(), minimum_ctc_lpa=12.0))
    assert next(iter(store.master.values()))["Category"] == "Excluded"


def test_digest_only_reports_new_or_changed_once():
    store = MemoryStore()
    out1 = run_pipeline(settings(), store, [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=False)
    assert out1.digest.total == 1
    store.mark_reported(out1.digest.marks)
    out2 = run_pipeline(settings(), store, [StubConnector([mk("1", desc=GOOD)])], today="2026-11-25", send_mail=False)
    assert out2.digest.total == 0


def test_digest_escapes_html():
    out, _ = run([mk("1", title="Business Development <script>alert(1)</script>", desc=GOOD)])
    assert "<script>" not in out.digest.html
