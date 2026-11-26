"""Safety patch 2: a dry run is genuinely read-only. A recording fake of the Google Sheets API (no network) logs EVERY call
that could change the spreadsheet; the tests assert that log stays empty. Mocked - not a live Google test."""
import dataclasses
from pathlib import Path

import pytest

from connectors.base import Connector, ConnectorResult
from core.models import MASTER_COLUMNS, Job
from integrations import google_sheets as G
from integrations import sheet_schema as S
from integrations.store import MemoryStore
from processing.ai_assist import AIAssistant
from integrations.ai_router import AIRouter
from processing.pipeline import run_pipeline
from tests.ai_fakes import FakeProvider, role_aware_answer
from tests.helpers import StubConnector, TODAY, mk, settings

GOOD = "Experience: 1-2 years. CTC 10 LPA. Own lead generation and client outreach for enterprise accounts. " * 2


# ----------------------------------------------------------------------------- recording fake of gspread
class RecWS:
    """Only reads are implemented. ANY other attribute used (update, batch_update, add_rows, clear ...) is a recorded mutation."""

    def __init__(self, title, values, log):
        self.title, self.values, self._log = title, values, log
        self.row_count, self.col_count, self.id = 1000, 30, abs(hash(title)) % 1000

    def get_all_values(self): return [list(r) for r in self.values]
    def row_values(self, n): return list(self.values[n - 1]) if len(self.values) >= n else []
    def col_values(self, c): return [r[c - 1] if c - 1 < len(r) else "" for r in self.values]

    def __getattr__(self, name):
        def mutation(*a, **k):
            self._log.append((self.title, name))
        return mutation


class RecSheet:
    def __init__(self, tabs: dict[str, list[list[str]]]):
        self.log: list = []
        self.tabs = {t: RecWS(t, v, self.log) for t, v in tabs.items()}

    def worksheets(self): return list(self.tabs.values())

    def batch_update(self, *a, **k): self.log.append(("<spreadsheet>", "batch_update"))
    def add_worksheet(self, *a, **k): self.log.append(("<spreadsheet>", "add_worksheet"))
    def del_worksheet(self, *a, **k): self.log.append(("<spreadsheet>", "del_worksheet"))


def full_tabs(master_rows=()):
    tabs = {S.TAB_ALL: [list(MASTER_COLUMNS)] + [list(r) for r in master_rows], S.TAB_STATS: [S.STATS_HEADERS], S.TAB_LOGS: [S.LOG_HEADERS],
            S.TAB_REGISTRY: [S.REGISTRY_HEADERS], S.TAB_AI_CACHE: [S.CACHE_HEADERS]}
    tabs.update({t: [S.VIEW_COLUMNS] for t in S.VIEW_RULES})
    return tabs


def sheets_store(sheet, read_only=True):
    st = G.GoogleSheetsStore.__new__(G.GoogleSheetsStore)
    st.s, st.read_only, st.sh, st.ws = settings(), read_only, sheet, sheet.tabs
    st._hdr, st._rows, st._ids, st._pos = {}, [], [], {}
    st._retry_call = lambda fn, *a, **k: fn(*a, **k)
    st.url = lambda: "https://sheet.example/test"
    return st


def row_for(job):
    rec = job.to_record()
    return [rec[c] for c in MASTER_COLUMNS]


class PaidConnector(Connector):
    name, free = "apify", False

    def __init__(self, cfg):
        super().__init__(cfg, None)
        self.executed = 0

    def enabled(self): return True

    def fetch(self, result: ConnectorResult, deadline):
        self.executed += 1
        result.jobs = [mk("p1", desc=GOOD)]


class MailSpy:
    def __init__(self): self.sent = []
    def __call__(self, *a, **k):
        self.sent.append(a)
        return True, "ok"


def dry_cfg(**kw):
    return settings(dry_run=True, gmail_address="a@example.com", gmail_app_password="x" * 16, email_recipient="a@example.com", **kw)


# ----------------------------------------------------------------------------- spreadsheet structure
def test_dry_run_with_missing_tabs_reports_and_modifies_nothing():
    tabs = full_tabs()
    del tabs[S.TAB_REGISTRY]
    del tabs["Strong Matches"]
    sheet = RecSheet(tabs)
    out = run_pipeline(dry_cfg(), sheets_store(sheet), [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=True)
    assert sheet.log == []                                                       # no tab created, nothing written
    assert out.blocked and any("Missing tab" in b and S.TAB_REGISTRY in b for b in out.blocked)
    assert out.jobs == [] and out.results == []                                  # stopped before fetching anything


def test_dry_run_with_missing_headers_reports_and_modifies_nothing():
    tabs = full_tabs()
    tabs[S.TAB_STATS] = []                                                        # empty tab: no header row
    tabs[S.TAB_ALL] = [[c for c in MASTER_COLUMNS if c != "Notes"]]              # old schema: a column is missing
    sheet = RecSheet(tabs)
    out = run_pipeline(dry_cfg(), sheets_store(sheet), [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=True)
    assert sheet.log == []
    assert any("no header row" in b for b in out.blocked) and any("missing column" in b and "Notes" in b for b in out.blocked)


def test_fresh_default_sheet_is_not_repaired_during_dry_run():
    sheet = RecSheet({"Sheet1": [[]]})                                           # brand-new spreadsheet: the old code renamed this
    out = run_pipeline(dry_cfg(), sheets_store(sheet), [StubConnector([mk("1", desc=GOOD)])], today=TODAY)
    assert sheet.log == [] and out.blocked


def test_read_only_store_refuses_every_write_method():
    sheet = RecSheet(full_tabs())
    st = sheets_store(sheet)
    calls = [lambda: st.prepare(), lambda: st.save_master([]), lambda: st.write_views({}), lambda: st.mark_reported({}),
             lambda: st.add_registry_names(["X"], TODAY), lambda: st.save_ai_cache([["k"]]), lambda: st.append_stats([["x"]]),
             lambda: st.append_logs([["x"]])]
    for c in calls:
        with pytest.raises(RuntimeError, match="read-only"):
            c()
    assert sheet.log == []


def test_production_preparation_is_still_available_when_not_read_only():
    sheet = RecSheet(full_tabs())
    st = sheets_store(sheet, read_only=False)
    st._refresh_worksheets = lambda: None
    st.prepare()                                                                   # complete sheet: nothing to repair, but allowed
    assert st.validate_structure() == []


def test_validate_structure_passes_on_complete_sheet_and_ai_cache_tab_is_optional():
    tabs = full_tabs()
    del tabs[S.TAB_AI_CACHE]
    assert sheets_store(RecSheet(tabs)).validate_structure() == []


# ----------------------------------------------------------------------------- full dry run on a complete sheet
def existing_row():
    j = Job(internal_id="abc123", source="stub", original_id="9", title="Sales Executive", company="Infosys", location="Pune, India",
            job_url="https://example.com/j/9", category="Strong Matches", job_status="Active", application_status="Interview",
            notes="round 2 on Friday", full_description=GOOD)
    j.source_keys = ["stub:9"]
    rec = row_for(j)
    return rec


def test_dry_run_on_complete_sheet_makes_no_writes_no_mail_no_paid_calls_no_ai(monkeypatch):
    import processing.pipeline as P
    spy = MailSpy()
    monkeypatch.setattr(P, "send_email", spy)
    sheet = RecSheet(full_tabs([existing_row()]))
    cfg = dry_cfg()
    paid = PaidConnector(cfg)
    prov = FakeProvider(answer=role_aware_answer)
    ai = AIAssistant(AIRouter([prov]))
    amb = mk("z1", title="Zebra Wrangler 1", desc="Experience: 1-2 years. " + "A long enough description about the role. " * 12)
    out = P.run_pipeline(cfg, sheets_store(sheet), [StubConnector([mk("1", desc=GOOD), amb]), paid], today=TODAY, send_mail=True, ai=ai)
    assert sheet.log == []                                                        # not a single mutating call
    assert spy.sent == []                                                         # no e-mail (digest or alert)
    assert paid.executed == 0 and any(r.name == "apify" and r.status == "skipped" for r in out.results)
    assert prov.calls == 0                                                        # AI is off by default in a dry run
    assert out.save_info in (None, {}, "") and "dry run" in out.email_status
    assert out.digest is not None                                                 # the digest is still built (for the preview)
    skipped = " | ".join(out.skipped_ops)
    for word in ("spreadsheet preparation", "job writes", "e-mail", "paid connectors", "AI requests"):
        assert word in skipped


def test_dry_run_with_explicit_ai_opt_in_asks_ai_but_still_writes_nothing(monkeypatch):
    import processing.pipeline as P
    monkeypatch.setattr(P, "send_email", MailSpy())
    sheet = RecSheet(full_tabs())
    prov = FakeProvider(answer=role_aware_answer)
    amb = mk("z1", title="Zebra Wrangler 1", desc="Experience: 1-2 years. " + "A long enough description about the role. " * 12)
    P.run_pipeline(dry_cfg(dry_run_allow_ai=True), sheets_store(sheet), [StubConnector([amb])], today=TODAY, ai=AIAssistant(AIRouter([prov])))
    assert prov.calls == 1 and sheet.log == []                                    # AI allowed by opt-in; cache not written either


def test_dry_run_allow_ai_defaults_to_false_and_is_read_from_env():
    from config.settings import load_settings
    from pathlib import Path
    nope = Path("/nonexistent/.env")
    assert load_settings(environ={}, env_path=nope).dry_run_allow_ai is False
    assert load_settings(environ={"DRY_RUN_ALLOW_AI": "true"}, env_path=nope).dry_run_allow_ai is True
    assert "\nDRY_RUN_ALLOW_AI=false\n" in (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")


def test_dry_run_memory_store_untouched_and_apify_connector_class_is_skipped():
    from connectors.apify_connector import ApifyConnector
    cfg = dry_cfg(apify_enabled=True, apify_token="t" * 20, apify_approved_max_usd_per_run=5.0, apify_enable={"naukri": True})
    c = ApifyConnector(cfg, None)
    assert c.enabled()                                                             # fully "approved" ...
    res = c.run()
    assert res.status == "skipped" and "dry run" in res.note and res.requests == 0 and res.cost_usd == 0   # ... yet never executed
    store = MemoryStore()
    run_pipeline(cfg, store, [c, StubConnector([mk("1", desc=GOOD)])], today=TODAY)
    assert store.master == {} and store.stats == [] and store.logs == [] and store.registry == [] and store.ai_cache == []


def test_production_run_still_writes_and_prepares():
    store = MemoryStore()
    out = run_pipeline(settings(), store, [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=False)
    assert len(store.master) == 1 and store.stats and out.skipped_ops == [] and out.blocked == []


# ----------------------------------------------------------------------------- preserved user data (production runs)
def test_existing_application_status_and_notes_are_preserved():
    store = MemoryStore()
    run_pipeline(settings(), store, [StubConnector([mk("1", desc=GOOD)])], today=TODAY, send_mail=False)
    jid = next(iter(store.master))
    store.master[jid]["Application Status"], store.master[jid]["Notes"] = "Interview", "call on Friday"
    for d in ("2026-11-25", "2026-11-26"):
        run_pipeline(settings(), store, [StubConnector([mk("1", desc=GOOD)])], today=d, send_mail=False)
    assert store.master[jid]["Application Status"] == "Interview" and store.master[jid]["Notes"] == "call on Friday"


def test_main_cli_dry_run_builds_read_only_store_and_no_ai(monkeypatch, capsys):
    import main
    seen = {}

    class FakeStore:
        def __init__(self, s, read_only=False): seen["ro"] = read_only
        def connect(self): pass

    monkeypatch.setattr(G, "GoogleSheetsStore", FakeStore)
    monkeypatch.setattr(main, "_connectors", lambda s, h: [])
    import processing.ai_assist as A
    monkeypatch.setattr(A, "build_assistant", lambda s, h, cache=None: pytest.fail("AI must not even be built in a dry run"))
    monkeypatch.setattr("processing.pipeline.run_pipeline", lambda *a, **k: _Out())
    for k, v in {"GOOGLE_SHEET_ID": "x" * 20, "GEMINI_API_KEY": "k" * 20}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("config.settings.Settings.validate", lambda self, **k: [])
    assert main.main(["run", "--dry-run"]) == 0
    assert seen["ro"] is True


class _Out:
    today, stats, results, save_info, email_status, blocked, skipped_ops = TODAY, None, [], None, "dry run", [], ["x"]


# ----------------------------------------------------------------------------- complete structure validation (portability patch)
def _problems(tabs, **kw):
    sheet = RecSheet(tabs)
    out = sheets_store(sheet).validate_structure(**kw)
    assert sheet.log == []                                                         # validation never modifies anything
    return out


@pytest.mark.parametrize("tab,headers", [(S.TAB_STATS, S.STATS_HEADERS), (S.TAB_LOGS, S.LOG_HEADERS),
                                         (S.TAB_REGISTRY, S.REGISTRY_HEADERS), (S.TAB_STRONG, S.VIEW_COLUMNS),
                                         (S.TAB_NEW, S.VIEW_COLUMNS), (S.TAB_ALL, MASTER_COLUMNS)])
def test_missing_column_is_reported_with_tab_and_column_name(tab, headers):
    tabs = full_tabs()
    tabs[tab] = [headers[:-1]]
    probs = _problems(tabs)
    assert any(f"'{tab}'" in p and headers[-1] in p and "missing column" in p for p in probs)


def test_every_required_tab_header_is_checked_and_extra_user_columns_are_allowed():
    tabs = full_tabs()
    for t in tabs:
        tabs[t] = [tabs[t][0] + ["My own column"]] + tabs[t][1:]
    assert _problems(tabs) == []


def test_empty_and_blank_header_rows_are_reported_for_every_required_tab():
    tabs = full_tabs()
    for t in (S.TAB_STATS, S.TAB_LOGS, S.TAB_STRONG):
        tabs[t] = [["", "  "]]
    tabs[S.TAB_REGISTRY] = []
    probs = _problems(tabs)
    for t in (S.TAB_STATS, S.TAB_LOGS, S.TAB_STRONG, S.TAB_REGISTRY):
        assert any(f"'{t}' has no header row" in p for p in probs)


def test_every_missing_view_tab_is_listed():
    tabs = full_tabs()
    for t in list(S.VIEW_RULES):
        del tabs[t]
    probs = _problems(tabs)
    assert len(probs) == 1 and all(t in probs[0] for t in S.VIEW_RULES)


def test_ai_cache_tab_is_optional_but_checked_when_it_exists_and_ai_is_used():
    tabs = full_tabs()
    tabs[S.TAB_AI_CACHE] = [S.CACHE_HEADERS[:-1]]
    assert _problems(tabs) == []                                                   # AI not used: its tab is not checked
    assert any(f"'{S.TAB_AI_CACHE}'" in p and "missing column" in p for p in _problems(tabs, check_ai_cache=True))
    del tabs[S.TAB_AI_CACHE]
    assert _problems(tabs, check_ai_cache=True) == []                              # missing cache tab never blocks a run


def test_dry_run_with_ai_enabled_blocks_on_a_broken_ai_cache_tab_without_modifying_it():
    tabs = full_tabs()
    tabs[S.TAB_AI_CACHE] = [["junk"]]
    sheet = RecSheet(tabs)
    ai = AIAssistant(AIRouter([FakeProvider(answer=role_aware_answer)]))
    out = run_pipeline(dry_cfg(dry_run_allow_ai=True), sheets_store(sheet), [StubConnector([mk("1", desc=GOOD)])], today=TODAY, ai=ai)
    assert sheet.log == [] and any(S.TAB_AI_CACHE in b for b in out.blocked)
