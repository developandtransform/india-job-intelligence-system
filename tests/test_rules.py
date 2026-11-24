"""The strict business rules, tested end to end through the real pipeline (normalise -> evaluate -> categorise)."""
import pytest

from tests.helpers import category_of

REG = lambda **kw: [{"Company": "Acme Startup", "Classification": "Startup", **{k.replace("_", " ").title(): str(v) for k, v in kw.items()}}]
GOOD = "Experience: 1-2 years. CTC 10 LPA."


def test_startup_younger_than_5_years_excluded():
    assert category_of(GOOD, company="Acme Startup", registry=REG(founded_year=2024, employee_count=200)) == "Excluded"


def test_startup_with_89_employees_excluded():
    assert category_of(GOOD, company="Acme Startup", registry=REG(founded_year=2015, employee_count=89)) == "Excluded"


def test_startup_with_90_employees_and_5_years_eligible():
    assert category_of(GOOD, company="Acme Startup", registry=REG(founded_year=2015, employee_count=90)) == "Strong Matches"


def test_startup_unknown_employee_count_needs_verification():
    assert category_of(GOOD, company="Acme Startup", registry=REG(founded_year=2015)) == "Company Verification Required"


def test_startup_unknown_age_needs_verification():
    assert category_of(GOOD, company="Acme Startup", registry=REG(employee_count=500)) == "Company Verification Required"


def test_established_non_startup_with_20_employees_has_no_employee_exclusion():
    reg = [{"Company": "Small Manufacturing Works", "Classification": "SME", "Employee Count": "20", "Founded Year": "2023"}]
    assert category_of(GOOD, company="Small Manufacturing Works", registry=reg) == "Strong Matches"


def test_unknown_company_requires_verification():
    assert category_of(GOOD, company="Totally Unknown Pvt Ltd") == "Company Verification Required"


def test_company_name_looking_like_bank_does_not_unlock_eligibility():
    assert category_of(GOOD, company="Random Bank Consulting Pvt Ltd") == "Company Verification Required"


def test_mnc_has_no_startup_restriction():
    assert category_of(GOOD, company="Accenture") == "Strong Matches"


def test_category_precedence_excluded_beats_high_score():
    assert category_of("5+ years experience. CTC 30 LPA. Lead generation, outreach, client acquisition.") == "Excluded"


def test_job_title_never_proves_experience():
    for title in ("Junior Business Development Executive", "Associate Sales Executive", "Management Trainee - Sales", "Sales Trainee"):
        assert category_of("CTC 10 LPA. Great team.", title=title) == "Experience Verification Required", title


def test_internship_without_stated_experience_needs_verification_but_universal_limit_applies():
    assert category_of("Internship 3 months. Stipend Rs 15,000 per month.", title="Sales Intern") == "Experience Verification Required"
    assert category_of("Internship 3 months. 4+ years experience. Stipend Rs 15,000 per month.", title="Sales Intern") == "Excluded"


def test_experience_applies_to_every_company_type():
    assert category_of("Experience: 4+ years. CTC 20 LPA.", company="Accenture") == "Excluded"
    reg = [{"Company": "Acme Startup", "Classification": "Startup", "Founded Year": "2010", "Employee Count": "500"}]
    assert category_of("Experience: 4+ years. CTC 20 LPA.", company="Acme Startup", registry=reg) == "Excluded"
