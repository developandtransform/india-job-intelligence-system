import smtplib

from config.settings import load_settings
from core.textutil import safe_url
from integrations import email as E
from reports.daily_digest import build_digest
from tests.helpers import mk, run, settings


def test_safe_url():
    assert safe_url("https://example.com/a?b=1") == "https://example.com/a?b=1"
    for bad in ("javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "ftp://x.com/a", "//evil.com", "https://", "not a url", "",
                "https://exa mple.com", "https://localhost/x"):
        assert safe_url(bad) == "", bad


def test_invalid_application_url_is_dropped_at_ingestion():
    out, store = run([mk("1", desc="Experience: 1-2 years. CTC 10 LPA.", apply_url="javascript:alert(1)")])
    rec = next(iter(store.master.values()))
    assert "javascript" not in rec["Direct Apply URL"] and rec["Direct Apply URL"] == rec["Job URL"]
    assert "javascript" not in out.digest.html and "javascript" not in out.digest.text


def test_digest_escapes_html_in_every_field():
    evil = "<img src=x onerror=alert(1)>"
    out, _ = run([mk("1", title="Business Development Executive", company="Accenture", desc="Experience: 1-2 years. CTC 10 LPA.")])
    job = out.jobs[0]
    job.title, job.company, job.location, job.match_explanation, job.verification_flags = (evil,) * 5   # bypass normaliser on purpose
    d = build_digest([job], {"sources": [{"name": evil, "status": evil, "jobs": 1}], "errors": [evil]}, settings(), "2026-11-24")
    assert "<img" not in d.html and "&lt;img" in d.html


def test_normaliser_strips_markup_from_scraped_text():
    out, _ = run([mk("1", title="Business Development <b>Executive</b>", company="Accenture", desc="<script>x()</script>Experience: 1-2 years. CTC 10 LPA.")])
    assert "<" not in out.jobs[0].title


def test_digest_link_attribute_cannot_break_out():
    j = mk("1", desc="Experience: 1-2 years. CTC 10 LPA.")
    out, _ = run([j])
    job = out.jobs[0]
    job.apply_url = 'https://example.com/x" onclick="alert(1)'
    d = build_digest([job], {}, settings(), "2026-11-24")
    assert 'onclick="alert' not in d.html


class FakeSMTP:
    mode = "ok"
    sent = []

    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def login(self, user, pw):
        if FakeSMTP.mode == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def mail_settings():
    return settings(gmail_address="me@example.com", gmail_app_password="abcd efgh ijkl mnop", email_recipient="me@example.com", email_enabled=True)


def test_email_send_success_uses_mock_only(monkeypatch):
    monkeypatch.setattr(E.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.mode, FakeSMTP.sent = "ok", []
    ok, _ = E.send_email(mail_settings(), "s", "<p>h</p>", "t")
    assert ok and FakeSMTP.sent[0]["To"] == "me@example.com"


def test_email_auth_failure_is_friendly_and_leaks_no_password(monkeypatch):
    monkeypatch.setattr(E.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.mode = "auth"
    ok, msg = E.send_email(mail_settings(), "s", "<p>h</p>", "t")
    assert not ok and "App Password" in msg and "abcd" not in msg


def test_email_missing_credentials_does_not_raise():
    ok, msg = E.send_email(settings(gmail_address="", gmail_app_password=""), "s", "h", "t")
    assert not ok and msg
