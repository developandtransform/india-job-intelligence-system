import pytest

from processing import salary_filter as sf

ELIG, EXCL, VER, UND = sf.ELIGIBLE, sf.EXCLUDED, sf.VERIFY, sf.UNDISCLOSED


def intern(text, months=None):
    c = sf.resolve_compensation(None, text, True)
    return sf.evaluate_internship(c, 10000, months or sf.internship_duration_months(text))


def fulltime(text):
    c = sf.resolve_compensation(None, text, False)
    return sf.evaluate_fulltime(c, 8.0)


@pytest.mark.parametrize("text,expected", [
    ("Internship. Stipend Rs 8,000 per month.", EXCL),
    ("Internship. Stipend Rs 9,999 per month.", EXCL),
    ("Internship. Stipend Rs 10,000 per month.", ELIG),
    ("Internship. Stipend ₹15,000/month", ELIG),
    ("Unpaid internship", EXCL),
    ("Internship. Stipend based on performance", UND),
    ("Internship. Stipend not disclosed", UND),
    ("Internship. Stipend ₹8,000 - ₹12,000 per month", VER),
    ("Internship. Stipend 8k-12k per month", VER),
])
def test_internship_table(text, expected):
    assert intern(text).status == expected


def test_internship_conversion_only_when_terms_are_clear():
    assert intern("Internship 3 months. Stipend Rs 2,500 per week.").status == ELIG                    # 52/12 x 2500 = 10,833
    assert intern("Internship 6 months. Stipend Rs 60,000 total.").status == ELIG                      # 10,000/month
    assert intern("Internship 6 months. Stipend Rs 30,000 for the entire internship.").status == EXCL  # 5,000/month
    assert intern("Internship. Stipend Rs 60,000 total.").status == UND                                # duration unknown
    assert intern("Internship. Stipend Rs 1,20,000 per annum.").status == UND                          # duration unknown
    assert intern("Internship. Stipend 15000").status == UND                                           # period unknown


def test_internship_maximum_only_is_not_a_minimum():
    assert intern("Internship. Stipend up to Rs 15,000 per month.").status == VER
    assert intern("Internship. Stipend up to Rs 8,000 per month.").status == EXCL


@pytest.mark.parametrize("text,expected", [
    ("CTC 7 LPA", EXCL),
    ("CTC: 8 LPA", ELIG),
    ("CTC: 9-12 LPA", ELIG),
    ("CTC: 6-10 LPA", VER),
    ("CTC: 5-7 LPA", EXCL),
    ("Salary not disclosed", UND),
    ("Competitive salary", UND),
    ("", UND),
])
def test_fulltime_table(text, expected):
    assert fulltime(text).status == expected


def test_up_to_is_a_maximum_not_a_minimum():
    assert fulltime("CTC up to 12 LPA").status == VER
    assert fulltime("Salary: upto 15 LPA").status == VER
    assert fulltime("CTC up to 7 LPA").status == EXCL
    assert fulltime("CTC 8 LPA and above").status == ELIG


@pytest.mark.parametrize("text", ["Fixed pay 9 LPA plus variable", "Base salary 12 LPA", "In-hand 10 LPA", "OTE 20 LPA", "Variable pay up to 4 LPA"])
def test_fixed_variable_ote_are_not_treated_as_ctc(text):
    assert fulltime(text).status == VER


def test_monthly_figure_is_not_ctc():
    r = fulltime("Salary Rs 70,000 per month")
    assert r.status == VER and r.ctc_min_lpa is None


def test_no_invented_salary():
    c = sf.resolve_compensation(None, "Great role, competitive pay, apply today", False)
    assert not c.has_amount


def test_foreign_currency_not_converted():
    assert fulltime("Salary $120,000 per year").status == VER


def test_remote_word_does_not_trigger_ote_basis():
    c = sf.resolve_compensation(None, "Fully remote role. 1-2 years experience. CTC 12 LPA. Reports and SQL.", False)
    assert c.basis == "CTC"
    assert sf.evaluate_fulltime(c, 8.0).status == ELIG


def test_thresholds_are_parameters():
    c = sf.resolve_compensation(None, "CTC 9 LPA", False)
    assert sf.evaluate_fulltime(c, 8.0).status == ELIG
    assert sf.evaluate_fulltime(c, 12.0).status == EXCL
