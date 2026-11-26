import logging
import re
from pathlib import Path

import yaml

from core.logging_setup import RedactingFilter

ROOT = Path(__file__).resolve().parent.parent


def test_env_is_gitignored_and_example_has_no_secrets():
    assert re.search(r"^\.env$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.M)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith(("GMAIL_APP_PASSWORD=", "APIFY_TOKEN=", "GEMINI_API_KEY=", "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=")):
            assert line.split("=", 1)[1].strip() == ""


def test_no_credentials_json_file_in_project():
    bad = [p for p in ROOT.rglob("*.json") if "service" in p.name.lower() or "credentials" in p.name.lower()]
    assert not bad


def test_daily_workflow():
    wf = yaml.safe_load((ROOT / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8"))
    on = wf.get("on") or wf.get(True)
    assert on["schedule"][0]["cron"] == "30 3 * * *"
    assert "workflow_dispatch" in on
    text = (ROOT / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8")
    assert "secrets.APP_ENV_FILE" in text
    assert "upload-artifact" not in text
    assert "git add" not in text and "git push" not in text
    assert "rm -f .env" in text


def test_log_redaction():
    f = RedactingFilter(["super-secret-token-123"])
    rec = logging.LogRecord("x", logging.INFO, "", 0, "token is super-secret-token-123 ok", (), None)
    f.filter(rec)
    assert "super-secret" not in rec.getMessage() and "***" in rec.getMessage()


def test_no_hardcoded_keys_in_source():
    pat = re.compile(r"(AIza[0-9A-Za-z_\-]{30,}|apify_api_[A-Za-z0-9]{20,}|-{5}BEGIN PRIVATE KEY-{5})")
    for p in ROOT.rglob("*.py"):
        assert not pat.search(p.read_text(encoding="utf-8")), p


def test_workflow_uses_exactly_one_secret_and_never_echoes_env():
    text = (ROOT / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"APP_ENV_FILE"}
    assert "cat .env" not in text and "printenv" not in text and "env |" not in text
    assert "add-mask" in text
    assert "timeout-minutes" in text
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}


def test_tests_workflow_needs_no_secrets():
    text = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert "secrets." not in text and "pytest" in text


def test_gitignore_covers_secrets_caches_and_envs():
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in (".env", ".venv", "__pycache__", ".pytest_cache", "*.log", "demo_output"):
        assert needle in ign, needle


def test_apify_default_off_in_example_env():
    ex = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^APIFY_ENABLED=false", ex, re.M)
    assert not re.findall(r"^APIFY_ENABLE_[A-Z_]+=true", ex, re.M)
    assert re.search(r"^APIFY_APPROVED_MAX_USD_PER_RUN=0(\.0+)?\s*$", ex, re.M)
