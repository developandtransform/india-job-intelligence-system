"""Safety patch 3: COMPANY_SEED_MODE=strict is the default; the unverified bundled seed can never decide eligibility."""
import dataclasses
from pathlib import Path

import pytest

from config.settings import load_settings
from integrations.store import MemoryStore
from tests.helpers import TODAY, mk, run

GOOD = "Experience: 1-2 years. CTC 10 LPA. Own lead generation and client outreach for enterprise accounts."
ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = load_settings(environ={}, env_path=Path("/nonexistent/.env"))          # the real defaults, no overrides


def cat(company="Acme Startup", reg=None, desc=GOOD, cfg=DEFAULTS, **kw):
    out, _ = run([mk("1", company=company, desc=desc, **kw)], MemoryStore(reg), cfg=cfg)
    return out.jobs[0].category if out.jobs else "DROPPED"


def REG(**kw):
    return [{"Company": "Acme Startup", "Classification": "Startup", **{k.replace("_", " ").title(): str(v) for k, v in kw.items()}}]


def test_default_and_example_env_are_strict():
    assert DEFAULTS.company_seed_mode == "strict"
    assert "\nCOMPANY_SEED_MODE=strict\n" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("value,expected", [("", "strict"), ("strict", "strict"), ("STRICT", "strict"), ("flag", "flag"),
                                            ("garbage", "strict"), ("  flag ", "flag")])
def test_setting_parsing_unclear_values_fall_back_to_strict(value, expected):
    assert load_settings(environ={"COMPANY_SEED_MODE": value}, env_path=Path("/nonexistent/.env")).company_seed_mode == expected


def test_unverified_seed_company_cannot_bypass_verification_by_default():
    out, _ = run([mk("1", company="Infosys", desc=GOOD)], cfg=DEFAULTS)
    j = out.jobs[0]
    assert j.category == "Company Verification Required"
    assert "Not a startup" not in j.startup_status                      # the seed classification is not used as evidence


def test_seed_company_with_unverified_seed_is_not_treated_as_verified_even_for_other_big_names():
    for name in ("Accenture", "Infosys", "Tata Consultancy Services"):
        assert cat(name) == "Company Verification Required", name


def test_user_verified_registry_row_still_works_in_strict_mode():
    reg = [{"Company": "Infosys", "Classification": "Large Indian company", "Evidence / Source URL": "https://example.org/proof"}]
    assert cat("Infosys", reg) == "Strong Matches"


def test_flag_mode_is_still_available_but_must_be_chosen():
    flag = dataclasses.replace(DEFAULTS, company_seed_mode="flag")
    assert cat("Infosys", cfg=flag) == "Strong Matches"


# ---- the startup rules always apply
def test_startup_younger_than_five_years_is_excluded():
    assert cat(reg=REG(founded_year=2024, employee_count=200)) == "Excluded"


def test_startup_with_fewer_than_90_employees_is_excluded():
    assert cat(reg=REG(founded_year=2015, employee_count=89)) == "Excluded"


def test_startup_meeting_both_limits_is_eligible():
    assert cat(reg=REG(founded_year=2015, employee_count=90)) == "Strong Matches"


def test_unknown_founding_year_or_employee_count_needs_verification():
    assert cat(reg=REG(employee_count=500)) == "Company Verification Required"
    assert cat(reg=REG(founded_year=2015)) == "Company Verification Required"
    assert cat(reg=REG()) == "Company Verification Required"


def test_self_described_startup_with_no_data_needs_verification():
    assert cat("Brand New Labs", desc=GOOD + " We are an early-stage startup disrupting retail.") == "Company Verification Required"


def test_source_data_showing_a_young_small_startup_is_excluded():
    j = mk("1", company="Fresh Labs", desc=GOOD + " We are a fast-growing startup.")
    j.hint_company = {"founded_year": "2024", "employees": "40"}
    out, _ = run([j], cfg=DEFAULTS)
    assert out.jobs[0].category == "Excluded"


def test_non_startup_rules_unchanged_small_established_company_not_excluded_for_headcount():
    reg = [{"Company": "Small Works", "Classification": "SME", "Employee Count": "20", "Founded Year": "2023"}]
    assert cat("Small Works", reg) == "Strong Matches"


def test_seed_never_marks_a_company_as_startup_and_startup_rules_unaffected_by_seed():
    from processing.company_verification import CompanyRegistry
    reg = CompanyRegistry()
    reg.load_seed()
    assert reg._by_key and all("startup" not in (r.classification or "").lower() for r in reg._by_key.values())
