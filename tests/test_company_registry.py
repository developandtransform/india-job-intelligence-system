import dataclasses

from integrations.store import MemoryStore
from processing.company_verification import CompanyRegistry
from processing.pipeline import run_pipeline
from tests.helpers import StubConnector, TODAY, mk, settings

GOOD = "Experience: 1-2 years. CTC 10 LPA."


def test_seed_entries_are_all_unverified():
    reg = CompanyRegistry()
    assert reg.load_seed() >= 200
    rec = reg.lookup("Infosys")
    assert rec and rec.verified_by == "Seed" and rec.verification_status == "Unverified" and not rec.evidence_url


def test_seed_match_is_flagged_not_silent():
    out = run_pipeline(settings(), MemoryStore(), [StubConnector([mk("1", company="Infosys", desc=GOOD)])], today=TODAY, send_mail=False)
    j = out.jobs[0]
    assert j.category == "Strong Matches"                       # non-startup: no startup limits
    assert "UNVERIFIED" in j.company_evidence.upper()
    assert j.startup_status == "Not a startup (unverified)"
    assert "unverified" in j.verification_flags.lower()


def test_strict_mode_sends_seed_only_companies_to_verification():
    cfg = dataclasses.replace(settings(), company_seed_mode="strict")
    out = run_pipeline(cfg, MemoryStore(), [StubConnector([mk("1", company="Infosys", desc=GOOD)])], today=TODAY, send_mail=False)
    assert out.jobs[0].category == "Company Verification Required"


def test_verified_sheet_row_overrides_seed_and_is_not_flagged():
    reg_rows = [{"Company": "Infosys", "Classification": "MNC", "Evidence / Source URL": "https://example.org/proof", "Last Updated": "2026-11-01"}]
    out = run_pipeline(settings(), MemoryStore(reg_rows), [StubConnector([mk("1", company="Infosys", desc=GOOD)])], today=TODAY, send_mail=False)
    j = out.jobs[0]
    assert j.startup_status == "Not a startup" and "unverified" not in j.verification_flags.lower()


def test_user_row_without_evidence_is_labelled_as_such():
    reg = CompanyRegistry()
    reg.load_sheet_rows([{"Company": "Acme", "Classification": "SME"}])
    assert reg.lookup("Acme").verification_status == "User-provided (no evidence)"


def test_unknown_companies_are_queued_for_the_registry_tab():
    store = MemoryStore()
    run_pipeline(settings(), store, [StubConnector([mk("1", company="Mystery Corp", desc=GOOD)])], today=TODAY, send_mail=False)
    assert any(r["Company"] == "Mystery Corp" for r in store.registry)


def test_source_company_type_label_mnc_vs_non_mnc():
    from processing.company_verification import evaluate_company
    from datetime import date
    reg = CompanyRegistry()
    assert evaluate_company("X", "", {"company_type": "MNC"}, reg, 5, 90, date(2026, 11, 24)).status == "Eligible"
    assert evaluate_company("X", "", {"company_type": "Non-MNC startup"}, reg, 5, 90, date(2026, 11, 24)).status != "Eligible"


def test_source_supplied_age_and_headcount_can_qualify_an_unclassified_company():
    from processing.company_verification import evaluate_company
    from datetime import date
    r = evaluate_company("Y", "", {"founded_year": "2012", "employees": "300"}, CompanyRegistry(), 5, 90, date(2026, 11, 24))
    assert r.status == "Eligible"


def test_age_is_computed_from_todays_date():
    from processing.company_verification import age_bounds
    from datetime import date
    lo, hi = age_bounds("2021-11-24", date(2026, 11, 24))
    assert abs(lo - 5.0) < 0.02 and abs(hi - 5.0) < 0.02
    lo, hi = age_bounds("2021", date(2026, 11, 24))       # only the year: between ~4.9 and ~5.9 years old -> undecidable
    assert lo < 5 <= hi
    lo, hi = age_bounds("2022", date(2026, 11, 24))       # at most ~4.9 years old -> provably too young
    assert hi < 5
