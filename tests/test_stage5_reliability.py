"""Stage 5: discovery helper, monitoring/alerts, change log, registry queue, dedup/history/dry-run/digest/workflow checks.
Everything uses fakes - no live network, Sheets or Gmail."""
import hashlib
from pathlib import Path

import pytest
import yaml

from connectors.base import ConnectorResult
from discovery.board_discovery import Candidate, discover, load_candidates, probe, slug_guesses, write_report
from integrations.store import MemoryStore
from processing import monitoring as mon
from processing.company_verification import CompanyRecord, CompanyRegistry
from reports.daily_digest import build_digest
from tests.helpers import StubConnector, TODAY, mk, run, settings

GOOD = "Experience: 1-2 years. CTC 10 LPA. Own lead generation and client outreach for enterprise accounts."
ROOT = Path(__file__).resolve().parent.parent


# ======================================================================= A. board discovery
class FakeConn:
    """Pretends to be AtsConnector.fetch_board: only the (ats, ident) pairs in ``boards`` exist."""

    def __init__(self, boards, errors=()):
        self.boards, self.errors, self.calls = boards, set(errors), []

    def fetch_board(self, ats, ident, name, result, deadline):
        self.calls.append((ats, ident))
        if (ats, ident) in self.errors:
            raise RuntimeError("HTTP 500 server error")
        if (ats, ident) not in self.boards:
            raise RuntimeError("404 Client Error: Not Found")
        result.jobs = list(self.boards[(ats, ident)])


def test_slug_guesses_are_mechanical_and_deduplicated():
    assert slug_guesses("Urban Company") == ["urbancompany", "urban-company", "urban"]
    assert slug_guesses("Razorpay Pvt Ltd") == ["razorpay"]
    assert slug_guesses("   ") == []


def test_only_verified_boards_are_suggested_and_nothing_is_invented():
    conn = FakeConn({("greenhouse", "razorpay"): [mk("1", location="Bengaluru, India"), mk("2", location="London, UK")]})
    res = discover([Candidate("Razorpay", "Fintech"), Candidate("Ghost Corp", "Other")], conn)
    by = {r.company: r for r in res}
    assert by["Razorpay"].suggest and by["Razorpay"].total == 2 and by["Razorpay"].india == 1
    assert "greenhouse:razorpay|Razorpay" in by["Razorpay"].row()[8]
    assert not by["Ghost Corp"].suggest and by["Ghost Corp"].identifier == "" and by["Ghost Corp"].result == "not found"


def test_probe_budget_per_company_is_capped():
    conn = FakeConn({})
    discover([Candidate("Some Long Company Name")], conn, max_probes=5)
    assert len(conn.calls) == 5


def test_explicit_identifier_is_tested_as_given_and_server_errors_are_reported():
    conn = FakeConn({}, errors={("lever", "acme")})
    res = discover([Candidate("Acme", "", "lever", "acme")], conn)
    assert len(conn.calls) == 1 and res[0].result == "error" and not res[0].suggest


def test_empty_board_is_found_but_not_suggested():
    r = probe(FakeConn({("lever", "empty"): []}), "Empty Co", "", "lever", "empty")
    assert r.result == "found" and r.total == 0 and not r.suggest


def test_report_is_csv_and_never_touches_targets_yaml(tmp_path):
    targets = ROOT / "config" / "targets.yaml"
    before = hashlib.sha256(targets.read_bytes()).hexdigest()
    res = discover([Candidate("Razorpay", "F")], FakeConn({("greenhouse", "razorpay"): [mk("1")]}))
    out = write_report(res, tmp_path / "o" / "r.csv")
    assert out.read_text(encoding="utf-8-sig").splitlines()[0].startswith("Company,Category,ATS,Identifier")
    assert hashlib.sha256(targets.read_bytes()).hexdigest() == before


def test_shipped_candidate_list_has_names_only_no_identifiers():
    cands = load_candidates(ROOT / "config" / "board_candidates.csv")
    assert len(cands) >= 25 and all(c.company for c in cands)
    assert all(not c.ats and not c.identifier for c in cands)               # nothing unverified is presented as fact
    assert {"Tech / fintech", "Financial services", "FMCG", "GCC", "Consulting"} <= {c.category for c in cands}


# ======================================================================= C. monitoring + alerts
def R(name, n=0, status="ok", errors=()):
    r = ConnectorResult(name)
    r.jobs = [mk(str(i)) for i in range(n)]
    r.status, r.errors = status, list(errors)
    return r


def hist(name, counts, start=10):
    return [[f"2026-11-{start + i:02d}", "00:00:00", name, name, "ok", str(c)] + ["0"] * 8 for i, c in enumerate(counts)]


def keys(issues):
    return {i.key for i in issues}


def test_zero_jobs_three_runs_in_a_row_alerts_but_two_does_not():
    assert "zero:jobspy" in keys(mon.detect_issues([R("jobspy", 0)], hist("jobspy", [5, 0, 0]), "2026-11-24"))
    assert "zero:jobspy" not in keys(mon.detect_issues([R("jobspy", 0)], hist("jobspy", [5, 3, 0]), "2026-11-24"))
    assert "zero:jobspy" not in keys(mon.detect_issues([R("jobspy", 4)], hist("jobspy", [0, 0, 0]), "2026-11-24"))


def test_connector_failure_and_partial_errors():
    k = keys(mon.detect_issues([R("ats", 0, "failed", ["HTTP 500"]), R("apify", 3, "partial", ["one actor failed"])], [], TODAY))
    assert {"failed:ats", "errors:apify"} <= k


def test_sudden_volume_drop():
    h = hist("ats", [100, 110, 95, 105])
    assert "drop:ats" in keys(mon.detect_issues([R("ats", 10)], h, TODAY))
    assert "drop:ats" not in keys(mon.detect_issues([R("ats", 80)], h, TODAY))
    assert "drop:ats" not in keys(mon.detect_issues([R("ats", 1)], hist("ats", [5, 4, 6, 3]), TODAY))      # small sources are noisy


def test_skipped_connectors_and_ai_outage():
    assert mon.detect_issues([R("apify", 0, "skipped")], hist("apify", [0, 0, 0]), TODAY) == []
    ai_row = ["2026-11-24", "00:00:00", "AI", "summary", "all failed"]
    assert "ai:outage" in keys(mon.detect_issues([], [], TODAY, [ai_row]))


def test_alert_deduplication_and_reminder_after_three_days():
    issue = mon.Issue("zero:jobspy", "warning", "jobspy: no jobs")
    log = mon.alert_log_rows([issue], "2026-11-24 03:30:00")
    assert mon.issues_to_send([issue], log, "2026-11-24") == []
    assert mon.issues_to_send([issue], log, "2026-11-26") == []
    assert mon.issues_to_send([issue], log, "2026-11-27") == [issue]                 # reminder after 3 days
    assert mon.issues_to_send([mon.Issue("other", "warning", "x")], log, "2026-11-24")                     # different issue still alerts


def test_alert_email_escapes_html():
    _, html, text = mon.alert_email([mon.Issue("k", "error", "<script>x</script>")], TODAY)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<script>" in text


class MailSpy:
    def __init__(self, ok=True): self.sent, self.ok = [], ok
    def __call__(self, s, subject, html, text):
        self.sent.append(subject)
        return self.ok, "ok" if self.ok else "smtp error"


def mail_settings():
    return settings(gmail_address="a@example.com", gmail_app_password="x" * 16, email_recipient="a@example.com")


def run_failing(store, today, monkeypatch, spy):
    import processing.pipeline as P
    monkeypatch.setattr(P, "send_email", spy)
    c = StubConnector([], fail=True, name="jobspy")
    return P.run_pipeline(mail_settings(), store, [c], today=today, send_mail=True)


def test_pipeline_sends_failure_alert_once_not_every_day(monkeypatch):
    store, spy = MemoryStore(), MailSpy()
    o1 = run_failing(store, "2026-11-24", monkeypatch, spy)
    assert o1.alert_status == "sent" and any("alert" in s for s in spy.sent)
    n = len(spy.sent)
    o2 = run_failing(store, "2026-11-25", monkeypatch, spy)
    assert o2.alerts_due == [] and not any("alert" in s for s in spy.sent[n:])           # digest yes, repeat alert no
    o4 = run_failing(store, "2026-11-27", monkeypatch, spy)
    assert o4.alerts_due                                                                 # reminder on day 4


def test_failed_alert_email_is_retried_next_run(monkeypatch):
    store = MemoryStore()
    run_failing(store, "2026-11-24", monkeypatch, MailSpy(ok=False))
    assert not any(r[2] == "alert" for r in store.logs)                                  # not remembered as alerted
    o = run_failing(store, "2026-11-25", monkeypatch, MailSpy())
    assert o.alert_status == "sent"


def test_dry_run_sends_nothing_and_writes_nothing(monkeypatch):
    import processing.pipeline as P
    spy = MailSpy()
    monkeypatch.setattr(P, "send_email", spy)
    store = MemoryStore()
    cfg = settings(dry_run=True, gmail_address="a@example.com", gmail_app_password="x" * 16, email_recipient="a@example.com")
    out = P.run_pipeline(cfg, store, [StubConnector([mk("1", desc=GOOD)], fail=False), StubConnector([], fail=True, name="x")],
                         today=TODAY, send_mail=True)
    assert spy.sent == [] and store.master == {} and store.stats == [] and store.logs == [] and store.registry == []
    assert out.jobs and out.issues


# ======================================================================= E. change log
def test_change_log_records_salary_location_experience_and_status_changes():
    store = MemoryStore()
    body = "Own lead generation and client outreach for enterprise accounts."
    run([mk("1", desc="Experience: 1-2 years. CTC 10 LPA. " + body)], store)
    run([mk("1", desc="Experience: 0-1 years. CTC 12 LPA. " + body)], store, today="2026-11-25")
    msgs = [r[3] for r in store.logs if r[2] == "change"]
    assert any("CTC LPA" in m and "12" in m for m in msgs), msgs
    assert any("Experience" in m for m in msgs)


def test_change_log_empty_when_nothing_changed():
    store = MemoryStore()
    run([mk("1", desc=GOOD)], store)
    out, _ = run([mk("1", desc=GOOD)], store, today="2026-11-25")
    assert out.changes == []


def test_closing_a_job_is_logged_as_status_change_and_history_kept():
    j = mk("1", desc=GOOD, source="greenhouse"); j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    for d in ("2026-11-25", "2026-11-26"):
        run([], store, today=d, scopes={"greenhouse:acme"})
    assert any("Job Status" in r[3] and "Closed" in r[3] for r in store.logs if r[2] == "change")
    assert len(store.master) == 1


# ======================================================================= B. registry queue
def test_registry_queue_orders_by_relevant_jobs_and_skips_verified():
    reg = CompanyRegistry()
    reg.add(CompanyRecord("Known Ltd", "Startup", "2018", "120", "https://e", "User", "Verified", "https://e", "2026-01-01"))
    jobs = []
    for i in range(3):
        j = mk(f"a{i}", company="Alpha Labs"); j.category = "Strong Matches"; j.job_status = "Active"; jobs.append(j)
    j = mk("b", company="Beta Works"); j.category = "Possible Matches"; j.job_status = "Active"; jobs.append(j)
    j = mk("k", company="Known Ltd"); j.category = "Strong Matches"; j.job_status = "Active"; jobs.append(j)
    j = mk("x", company="Excluded Co"); j.category = "Excluded"; j.job_status = "Active"; jobs.append(j)
    q = mon.registry_queue(jobs, reg)
    assert [e["company"] for e in q] == ["Alpha Labs", "Beta Works"]
    assert q[0]["relevant_jobs"] == 3 and "founding year" in q[0]["missing"]


# ======================================================================= D. dedup / reposts
def test_same_job_different_urls_same_source_is_one_job():
    a, b = mk("1", desc=GOOD), mk("2", desc=GOOD)
    b.job_url = "https://example.com/other/path?utm=1"
    _, store = run([a, b])
    assert len(store.master) == 1


def test_similar_titles_same_company_different_roles_stay_separate():
    _, store = run([mk("1", title="Sales Executive", desc=GOOD), mk("2", title="Marketing Executive", desc=GOOD)])
    assert len(store.master) == 2


def test_posting_that_returns_after_closure_reopens_the_same_record():
    j = mk("1", desc=GOOD, source="greenhouse"); j.source_scopes = ["greenhouse:acme"]
    store = MemoryStore()
    run([j], store, scopes={"greenhouse:acme"})
    for d in ("2026-11-25", "2026-11-26"):
        run([], store, today=d, scopes={"greenhouse:acme"})
    again = mk("777", desc=GOOD, source="greenhouse"); again.source_scopes = ["greenhouse:acme"]
    run([again], store, today="2026-11-27", scopes={"greenhouse:acme"})
    # current behaviour: the returning posting is matched back to its closed record, which is re-opened (history kept)
    assert len(store.master) == 1
    assert next(iter(store.master.values()))["Job Status"] == "Active"


# ======================================================================= F. sheets efficiency / idempotence
def test_running_twice_does_not_duplicate_rows_or_rewrite_unchanged_data():
    store = MemoryStore()
    jobs = [mk(str(i), title=f"Sales Executive {chr(65 + i)}x", company=f"Zq{chr(97 + i)}n Ltd", desc=GOOD) for i in range(5)]
    run(jobs, store)
    n = len(store.master)
    run(jobs, store, today="2026-11-25")
    assert len(store.master) == n == 5


# ======================================================================= G. digest
def _cat(j, c):
    j.category, j.job_status = c, "Active"
    return j


def test_digest_total_cap_priority_order_and_overview():
    jobs = []
    for i in range(30):
        jobs.append(_cat(mk(f"s{i}", title=f"Sales Exec {i}", company=f"Strong{i}"), "Strong Matches"))
    for i in range(30):
        jobs.append(_cat(mk(f"p{i}", title=f"Ops Exec {i}", company=f"Poss{i}"), "Possible Matches"))
    for i in range(10):
        jobs.append(_cat(mk(f"v{i}", title=f"Verif {i}", company=f"Verif{i}"), "Location Verification Required"))
    for j in jobs:
        j.content_hash, j.internal_id = "h" + j.original_id, "id" + j.original_id
    cfg = settings(email_max_jobs_total=20, email_max_jobs_per_category=15)
    d = build_digest(jobs, {"sources": [], "issues": ["jobspy: no jobs in 3 runs in a row"]}, cfg, TODAY)
    assert d.total == 20 and len(d.marks) == 20
    assert "Strong Matches" in d.text and d.text.index("Strong Matches") < d.text.index("Possible Matches")
    assert "Needs attention" in d.text and "Waiting to be reported" in d.text
    unreported = [j for j in jobs if j.internal_id not in d.marks]
    assert len(unreported) == 50                                                          # carried to tomorrow, not lost
    assert any(j.category == "Location Verification Required" for j in unreported)        # lowest priority trimmed first


def test_digest_empty_day():
    d = build_digest([], {"sources": []}, settings(), TODAY)
    assert d.total == 0 and "No new or changed" in d.text


# ======================================================================= H. workflow review
def test_workflow_production_safety():
    y = yaml.safe_load((ROOT / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8"))
    on = y.get("on", y.get(True))
    assert on["schedule"][0]["cron"] == "30 3 * * *"                                      # 03:30 UTC == 09:00 IST
    assert on["workflow_dispatch"]["inputs"]["mode"]["default"] == "dry-run"          # input renamed in the safety patch
    assert y["concurrency"]["cancel-in-progress"] is False and y["concurrency"]["group"]
    assert y["permissions"] == {"contents": "read"}
    job = y["jobs"]["run"]
    assert job["timeout-minutes"] <= 60
    names = [s.get("name", "") for s in job["steps"]]
    assert any("failed" in n.lower() for n in names)                                       # failure notification step
    fail = next(s for s in job["steps"] if "failed" in s.get("name", "").lower())
    assert fail["if"] == "failure()" and "notify-failure" in fail["run"]
    last = job["steps"][-1]
    assert last["if"] == "always()" and "rm -f .env" in last["run"]                       # secret file always removed
    text = (ROOT / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8")
    assert "secrets.APP_ENV_FILE" in text and "add-mask" in text
    assert "echo $APP_ENV_FILE" not in text and "cat .env" not in text


def test_notify_failure_never_raises_without_email_config(capsys, monkeypatch):
    import main
    for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "EMAIL_RECIPIENT"):
        monkeypatch.delenv(k, raising=False)
    assert main.main(["notify-failure"]) == 0


def test_edited_text_refresh_has_guards_against_flapping():
    body = "Own lead generation and client outreach for enterprise accounts. " * 3
    store = MemoryStore()
    run([mk("1", desc="CTC 10 LPA. Experience: 1-2 years. " + body, source="naukri")], store)
    # a much shorter snippet (list page) must NOT replace the full text
    run([mk("1", desc="CTC 30 LPA.", source="naukri")], store, today="2026-11-25")
    assert next(iter(store.master.values()))["CTC LPA"] == "10"
    # the same text from a DIFFERENT source key is not treated as an edit of this posting
    other = mk("9", desc="CTC 20 LPA. Experience: 1-2 years. " + body, source="indeed")
    run([other], store, today="2026-11-26")
    assert next(iter(store.master.values()))["CTC LPA"] == "10"
