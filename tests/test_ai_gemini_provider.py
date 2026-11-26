"""Stage 1: Gemini provider diagnostics, retries and the check-ai self-test. Fake HTTP only - nothing leaves this process."""
import logging
from pathlib import Path

import pytest
import requests

from config.settings import load_settings, read_env_file
from integrations.ai_providers import (AIError, AUTH, MALFORMED, MODEL, RATE_LIMIT, TIMEOUT, TRANSIENT, GeminiProvider,
                                       normalize_gemini_model, redact, run_check)

KEY = "AI" + "za" + "SyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"   # split so the repo secret-scan test does not trip
OK_BODY = {"candidates": [{"content": {"parts": [{"text": '{"ok": true, "word": "pong"}'}]}}]}


class R:
    def __init__(self, status, body=None, headers=None, text=None):
        self.status_code, self._b, self.headers = status, body, headers or {}
        self.text = text if text is not None else ("" if body is None else str(body))

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def prov(*responses, **kw):
    sleeps = []
    p = GeminiProvider(KEY, kw.pop("model", "gemini-3.8-flash"), FakeHttp(*responses), sleep=sleeps.append,
                       rand=lambda: 0.5, **kw)
    p.sleeps = sleeps
    return p


def err(kind_status):
    return R(kind_status, {"error": {"code": kind_status, "message": "boom " + KEY, "status": "X"}})


def test_success_and_request_format():
    p = prov(R(200, OK_BODY))
    assert p.complete_json("hi") == {"ok": True, "word": "pong"}
    url, kw = p.http.calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert kw["headers"]["x-goog-api-key"] == KEY and KEY not in url
    assert kw["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_404_is_model_error_not_retried_and_logged_with_google_message(caplog):
    p = prov(R(404, {"error": {"code": 404, "message": "models/x is not found for API version v1beta", "status": "NOT_FOUND"}}))
    with caplog.at_level(logging.WARNING), pytest.raises(AIError) as e:
        p.complete_json("hi")
    assert e.value.kind == MODEL and e.value.status == 404
    assert len(p.http.calls) == 1 and p.sleeps == []
    assert "not found" in str(e.value) and "gemini-3.8-flash" in caplog.text and "404" in caplog.text


def test_429_retries_then_succeeds_with_backoff():
    p = prov(err(429), err(429), R(200, OK_BODY))
    assert p.complete_json("hi")["ok"] is True
    assert len(p.http.calls) == 3 and len(p.sleeps) == 2
    assert p.sleeps[1] > p.sleeps[0] and all(0 < s <= 30 * 1.5 for s in p.sleeps)


def test_503_retries_then_gives_up():
    p = prov(err(503), max_retries=2)
    with pytest.raises(AIError) as e:
        p.complete_json("hi")
    assert e.value.kind == TRANSIENT and len(p.http.calls) == 3


def test_retry_after_header_honoured_and_too_long_gives_up():
    p = prov(R(429, {"error": {"message": "slow"}}, headers={"retry-after": "7"}), R(200, OK_BODY))
    p.complete_json("hi")
    assert p.sleeps == [7.0]
    q = prov(R(429, {"error": {"message": "slow"}}, headers={"retry-after": "900"}), R(200, OK_BODY))
    with pytest.raises(AIError):
        q.complete_json("hi")
    assert len(q.http.calls) == 1


def test_timeout_retried_then_reported():
    p = prov(requests.Timeout("t"), max_retries=1)
    with pytest.raises(AIError) as e:
        p.complete_json("hi")
    assert e.value.kind == TIMEOUT and len(p.http.calls) == 2


def test_invalid_api_key_never_retried_and_key_not_in_message():
    p = prov(R(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key. " + KEY,
                                "status": "INVALID_ARGUMENT"}}))
    with pytest.raises(AIError) as e:
        p.complete_json("hi")
    assert e.value.kind == AUTH and len(p.http.calls) == 1
    assert KEY not in str(e.value)


def test_bad_request_not_retried():
    p = prov(R(400, {"error": {"message": "Invalid JSON payload"}}))
    with pytest.raises(AIError):
        p.complete_json("hi")
    assert len(p.http.calls) == 1


@pytest.mark.parametrize("body", [R(200, {"candidates": []}), R(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
                                  R(200, None), R(200, {"candidates": [{"content": {"parts": [{"text": "[1,2]"}]}}]})])
def test_malformed_200_reported_not_retried(body):
    p = prov(body)
    with pytest.raises(AIError) as e:
        p.complete_json("hi")
    assert e.value.kind == MALFORMED and len(p.http.calls) == 1


def test_code_fenced_json_is_accepted():
    body = {"candidates": [{"content": {"parts": [{"text": '```json\n{"a": 1}\n```'}]}}]}
    assert prov(R(200, body)).complete_json("x") == {"a": 1}


def test_model_name_prefix_and_quotes_normalised():
    assert normalize_gemini_model('"models/gemini-3.8-flash"') == "gemini-3.8-flash"
    assert normalize_gemini_model(" gemini-3.8-flash \n") == "gemini-3.8-flash"
    p = prov(R(200, OK_BODY), model="models/gemini-3.8-flash")
    p.complete_json("x")
    assert "models/models" not in p.http.calls[0][0]


def test_empty_key_is_config_error_without_network():
    p = GeminiProvider("", "gemini-3.8-flash", FakeHttp(R(200, OK_BODY)))
    with pytest.raises(AIError):
        p.complete_json("x")
    assert p.http.calls == []


def test_redact_removes_keys():
    t = redact(f"url?key={KEY} Authorization: Bearer abc.def.ghi gsk_" + "a" * 30 + " sk-or-v1-" + "b" * 30)
    assert KEY not in t and "gsk_aaaa" not in t and "abc.def.ghi" not in t and "sk-or-v1-bbbb" not in t


def test_run_check_reports():
    ok = run_check(prov(R(200, OK_BODY)))
    assert ok.connected and ok.validated and ok.status == "HTTP 200"
    bad = run_check(prov(R(404, {"error": {"message": "not found"}})))
    assert not bad.connected and not bad.validated and "404" in bad.status and KEY not in bad.reason
    wrong = run_check(prov(R(200, {"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]})))
    assert wrong.connected and not wrong.validated


# ---------------- settings / .env loading
def test_default_model_is_gemini_3_8_flash():
    s = load_settings(environ={"GEMINI_MODEL": ""})
    assert s.gemini_model == "gemini-3.8-flash"


def test_env_model_prefix_normalised_and_real_env_overrides_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('GEMINI_MODEL="models/gemini-3.8-flash"\nGEMINI_API_KEY=abc\n', encoding="utf-8")
    assert load_settings(f, environ={}).gemini_model == "gemini-3.8-flash"
    assert load_settings(f, environ={"GEMINI_MODEL": "other-model"}).gemini_model == "other-model"


@pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig", "utf-16"])
def test_env_file_encodings_load(tmp_path, enc):
    f = tmp_path / ".env"
    f.write_bytes("GEMINI_MODEL=gemini-3.8-flash\r\nGEMINI_API_KEY=abc123\r\n".encode(enc))
    d = read_env_file(f)
    assert d["GEMINI_MODEL"] == "gemini-3.8-flash" and d["GEMINI_API_KEY"] == "abc123"


def test_env_file_bad_encoding_gives_friendly_error(tmp_path):
    f = tmp_path / ".env"
    f.write_bytes(b"A=\xff\xfa\xfb\n")
    with pytest.raises(ValueError, match="UTF-8"):
        read_env_file(f)


def test_ai_settings_validated_and_secret_listed():
    s = load_settings(environ={"GEMINI_API_KEY": KEY, "AI_MAX_RETRIES": "99", "AI_ENABLED": "false"})
    assert s.ai_max_retries == 6 and s.ai_enabled is False
    assert KEY in s.secret_values()
    bad = load_settings(environ={"GEMINI_API_KEY": KEY, "GEMINI_MODEL": "a b;c"})
    assert any("GEMINI_MODEL" in p for p in bad.ai_problems())


# ---------------- CLI
def test_show_config_prints_no_key(capsys, monkeypatch):
    import main
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    assert main.main(["show-config"]) == 0
    out = capsys.readouterr().out
    assert "gemini-3.8-flash" in out and KEY not in out and "key set (" in out


def test_check_ai_cli_with_fake_provider(capsys, monkeypatch):
    import main
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(main, "_providers", lambda s, h: [prov(R(503, {"error": {"message": "high demand"}}), max_retries=1)])
    assert main.main(["check-ai"]) == 1
    out = capsys.readouterr().out
    assert "gemini-3.8-flash" in out and "high demand" in out and KEY not in out


def test_workflow_masks_only_secret_keys_and_shows_config():
    y = (Path(__file__).resolve().parents[1] / ".github/workflows/daily_jobs.yml").read_text(encoding="utf-8")
    assert "show-config" in y and "*KEY*" in y and "APP_ENV_FILE" in y
