import pytest

from tests.helpers import category_of

GOOD = "Experience: 1-2 years. CTC 10 LPA. "


@pytest.mark.parametrize("loc", ["Pune, Maharashtra, India", "Bengaluru", "Hybrid - Mumbai, India", "Gurugram, Haryana", "Chennai, Tamil Nadu, India"])
def test_indian_cities_and_hybrid_pass(loc):
    assert category_of(GOOD, location=loc) == "Strong Matches"


def test_relocation_within_india_passes():
    assert category_of(GOOD + "Relocation within India supported.", location="Hyderabad, India") == "Strong Matches"


def test_remote_explicitly_open_to_india_passes():
    assert category_of(GOOD + "Remote role, open to candidates in India.", location="Remote") == "Strong Matches"
    assert category_of(GOOD, location="Remote, India") == "Strong Matches"


def test_worldwide_remote_without_india_evidence_needs_verification():
    assert category_of(GOOD + "Work from anywhere.", location="Remote") == "Location Verification Required"


def test_india_scoped_search_is_not_proof_for_remote_roles():
    # hint_country="India" is set by the helper's jobs (as JobSpy does); it must not unlock a remote role
    assert category_of(GOOD, location="Remote") == "Location Verification Required"


@pytest.mark.parametrize("loc,desc", [
    ("Remote - US", "Remote within the United States only."),
    ("London, United Kingdom", ""),
    ("New York, USA", ""),
    ("Singapore", ""),
])
def test_explicit_foreign_locations_are_dropped(loc, desc):
    assert category_of(GOOD + desc, location=loc) == "DROPPED"


def test_foreign_location_is_not_rescued_by_mentioning_india():
    assert category_of(GOOD + "Our India office handles support.", location="Berlin, Germany") == "DROPPED"
