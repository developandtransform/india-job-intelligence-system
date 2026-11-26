import base64
import json
from pathlib import Path

import pytest

from config.settings import APIFY_ACTORS, load_settings

ROOT = Path(__file__).resolve().parent.parent


def fake_key_b64():
    return base64.b64encode(json.dumps({"type": "service_account", "client_email": "svc@proj.iam.gserviceaccount.com",
                                        "private_key": "FAKE-NOT-A-REAL-KEY"}).encode()).decode()


def test_defaults_without_any_env_file():
    s = load_settings(environ={}, env_path=Path("/nonexistent/.env"))
    assert (s.minimum_ctc_lpa, s.maximum_experience_years, s.startup_min_age_years, s.startup_min_employees) == (8.0, 3.0, 5.0, 90)
    assert s.minimum_internship_stipend_inr_monthly == 10000
    assert not s.apify_enabled and s.enabled_apify_actors() == [] and s.company_seed_mode == "strict"


def test_env_file_is_read_and_real_environment_overrides_it(tmp_path):
    f = tmp_path / ".env"
    f.write_text("MINIMUM_CTC_LPA=9\nGOOGLE_SHEET_ID=from_file\n")
    s = load_settings(env_path=f, environ={})
    assert s.minimum_ctc_lpa == 9.0 and s.google_sheet_id == "from_file"
    s = load_settings(env_path=f, environ={"GOOGLE_SHEET_ID": "from_env"})
    assert s.google_sheet_id == "from_env"


def test_env_example_loads_and_keeps_apify_disabled():
    s = load_settings(env_path=ROOT / ".env.example", environ={})
    assert not s.apify_enabled and s.enabled_apify_actors() == []
    assert all(not v for v in s.apify_enable.values()) and set(s.apify_enable) == set(APIFY_ACTORS)


def test_missing_credentials_are_reported_in_plain_language():
    s = load_settings(environ={}, env_path=Path("/nonexistent/.env"))
    problems = s.validate(need_sheets=True, need_email=True)
    text = " ".join(problems)
    assert "GOOGLE_SHEET_ID" in text and "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64" in text and len(problems) >= 3


def test_invalid_base64_is_reported_not_raised():
    s = load_settings(environ={"GOOGLE_SHEET_ID": "x", "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64": "%%%not-base64%%%"}, env_path=Path("/nonexistent/.env"))
    assert any("Base64" in p or "base64" in p for p in s.validate(need_sheets=True, need_email=False))


def test_valid_service_account_is_decoded_and_secrets_are_listed_for_redaction():
    s = load_settings(environ={"GOOGLE_SHEET_ID": "x", "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64": fake_key_b64(), "APIFY_TOKEN": "tok-123456789"},
                      env_path=Path("/nonexistent/.env"))
    assert s.service_account_info()["client_email"].endswith("iam.gserviceaccount.com")
    secrets = s.secret_values()
    assert "tok-123456789" in secrets and "FAKE-NOT-A-REAL-KEY" in secrets


def test_only_one_env_file_and_no_credential_json_in_project():
    assert [p.name for p in ROOT.rglob(".env*") if ".git/" not in str(p)] == [".env.example"]
    assert not [p for p in ROOT.rglob("*.json") if any(w in p.name.lower() for w in ("service", "credential", "secret", "key"))]
