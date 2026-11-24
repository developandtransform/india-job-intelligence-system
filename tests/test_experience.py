import pytest

from processing import experience_filter as xf

ELIG, EXCL, VER = xf.ELIGIBLE, xf.EXCLUDED, xf.VERIFY


def ev(text, title="Executive", **kw):
    return xf.evaluate_experience(title, text, limit=3.0, **kw)


@pytest.mark.parametrize("text,expected", [
    ("Experience: 0 years", ELIG),
    ("Experience: 0-1 years", ELIG),
    ("Experience: 0-2 years", ELIG),
    ("Experience: 0-3 years", ELIG),
    ("Experience: 0 - 3 yrs", ELIG),
    ("Experience: 1-2 years", ELIG),
    ("Experience: 1-3 years", ELIG),
    ("Experience: 2-3 years", ELIG),
    ("2 to 3 years of experience", ELIG),
    ("Freshers can apply", ELIG),
    ("Fresher", ELIG),
    ("No experience required", ELIG),
    ("Experience: 2-4 years", EXCL),
    ("2 to 4 years of experience", EXCL),
    ("Experience: 3-5 years", EXCL),
    ("3+ years of experience", EXCL),
    ("Experience: 4+ years", EXCL),
    ("5+ years of experience", EXCL),
    ("Experience: 0-5 years", EXCL),
    ("0-5 years of experience", EXCL),
])
def test_spec_table(text, expected):
    assert ev(text).status == expected


@pytest.mark.parametrize("text", ["0-5 years", "0 to 5 years", "5+ years", "Candidates with 5+ years", "Great culture, apply now", ""])
def test_bare_or_missing_numbers_never_become_eligible(text):
    assert ev(text).status != ELIG


@pytest.mark.parametrize("text", ["Minimum 2 years of experience", "At least 2 years of relevant experience", "1 to 3 years of experience; 5+ years of Excel is a plus"])
def test_open_ended_or_conflicting_statements_need_verification(text):
    assert ev(text).status == VER


def test_structured_field_wins():
    assert xf.evaluate_experience("x", "", hint_min=0, hint_max=2, limit=3.0).status == ELIG
    assert xf.evaluate_experience("x", "", hint_min=2, hint_max=5, limit=3.0).status == EXCL
    assert xf.evaluate_experience("x", "", hint_text="0-2 Yrs", limit=3.0).status == ELIG


def test_months_are_converted():
    assert ev("Experience: 6 months to 1 year").status == ELIG


def test_company_history_years_are_not_experience():
    assert ev("We have 25 years of excellence in manufacturing. Experience: 1-2 years").status == ELIG


@pytest.mark.parametrize("limit,text,expected", [(2.0, "Experience: 1-3 years", EXCL), (5.0, "Experience: 3-5 years", ELIG)])
def test_limit_is_configurable_not_hardcoded(limit, text, expected):
    assert xf.evaluate_experience("x", text, limit=limit).status == expected


def test_internship_or_contract_duration_is_not_experience():
    assert ev("Internship 3 months. 4+ years experience.").status == EXCL
    assert ev("Duration of internship: 6 months. Experience: 1-2 years").status == ELIG
    assert ev("3 months contract. Experience: 1-2 years").status == ELIG


def test_titles_never_infer_eligibility():
    for t in ("Junior Executive", "Associate", "Management Trainee", "Intern"):
        assert xf.evaluate_experience(t, "", limit=3.0, kind="Internship" if t == "Intern" else "").status == VER
