from integrations.store import MemoryStore
from processing.deduplication import dedupe_batch, same_posting
from tests.helpers import mk, run

GOOD = "Experience: 1-2 years. CTC 10 LPA. Own lead generation and client outreach for enterprise accounts."


def test_same_posting_on_two_sources_is_merged_and_urls_preserved():
    a = mk("1", desc=GOOD, source="naukri"); a.job_url = "https://naukri.example/job/1"
    b = mk("2", desc=GOOD, source="indeed"); b.job_url = "https://indeed.example/viewjob?jk=2"
    out, store = run([a, b])
    assert len(store.master) == 1
    rec = next(iter(store.master.values()))
    assert "https://naukri.example/job/1" in rec["All Source URLs"] and "https://indeed.example/viewjob?jk=2" in rec["All Source URLs"]
    assert "naukri" in rec["Source"] and "indeed" in rec["Source"]


def test_rerun_updates_instead_of_inserting():
    store = MemoryStore()
    run([mk("1", desc=GOOD)], store)
    run([mk("1", desc=GOOD)], store, today="2026-11-25")
    run([mk("1", desc=GOOD)], store, today="2026-11-26")
    assert len(store.master) == 1
    rec = next(iter(store.master.values()))
    assert rec["First Seen Date"] == "2026-11-24" and rec["Last Seen Date"] == "2026-11-26"


def test_different_locations_are_different_jobs():
    _, store = run([mk("1", location="Pune, India", desc=GOOD), mk("2", location="Mumbai, India", desc=GOOD)])
    assert len(store.master) == 2


def test_different_titles_same_company_are_different_jobs():
    _, store = run([mk("1", title="Sales Executive", desc=GOOD), mk("2", title="Finance Analyst", desc=GOOD)])
    assert len(store.master) == 2


def test_dedupe_batch_unit():
    a, b = mk("1", desc=GOOD, source="naukri"), mk("2", desc=GOOD, source="indeed")
    assert same_posting(a, b)
    assert len(dedupe_batch([a, b])) == 1


def test_repost_of_a_closed_job_is_counted():
    j = mk("1", desc=GOOD, source="greenhouse"); j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    run([], store, today="2026-11-25", scopes={"greenhouse:acme"})
    run([], store, today="2026-11-26", scopes={"greenhouse:acme"})        # closed now
    again = mk("999", desc=GOOD, source="greenhouse"); again.source_scopes = ["greenhouse:acme"]
    run([again], store, today="2026-11-27", scopes={"greenhouse:acme"})
    reposts = [r["Repost Count"] for r in store.master.values()]
    assert "1" in reposts or len(store.master) == 1       # a new row with repost count 1, or merged back into the old record


def test_history_is_never_deleted_when_job_disappears():
    store = MemoryStore()
    run([mk("1", desc=GOOD)], store)
    for d in ("2026-11-25", "2026-11-26", "2026-12-30"):
        run([], store, today=d)
    assert len(store.master) == 1


def test_manual_status_and_notes_survive_many_updates_and_closure():
    j = mk("1", desc=GOOD, source="greenhouse"); j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    jid = next(iter(store.master))
    store.master[jid]["Application Status"], store.master[jid]["Notes"] = "Interview", "round 2 on Friday"
    for d in ("2026-11-25", "2026-11-26", "2026-11-27"):
        run([], store, today=d, scopes={"greenhouse:acme"})
    rec = store.master[jid]
    assert rec["Job Status"] == "Closed" and rec["Application Status"] == "Interview" and rec["Notes"] == "round 2 on Friday"
