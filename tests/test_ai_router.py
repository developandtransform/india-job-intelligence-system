"""Stage 2: Groq / Cloudflare / OpenRouter adapters and the central router. Fake HTTP only (mocked, not live)."""
import pytest
import requests

from integrations.ai_providers import (AIError, AUTH, CONFIG, MALFORMED, MODEL, QUOTA, RATE_LIMIT, CloudflareProvider,
                                       GeminiProvider, GroqProvider, OpenRouterProvider)
from integrations.ai_router import AIRouter, build_providers, build_router
from config.settings import load_settings
from tests.ai_fakes import FakeHttp, R, cf_body, gemini_body, jerr, openai_body

ACC = "0123456789abcdef0123456789abcdef"
OKJ = '{"ok": true, "word": "pong"}'
FAST = dict(sleep=lambda s: None, rand=lambda: 0.5)


def groq(*r, **kw): return GroqProvider("gsk_" + "x" * 30, "openai/gpt-oss-20b", FakeHttp(*r), **FAST, **kw)
def gem(*r, **kw): return GeminiProvider("k" * 20, "gemini-3.8-flash", FakeHttp(*r), **FAST, **kw)
def cf(*r, **kw): return CloudflareProvider("tok" * 10, "@cf/meta/llama-3.1-8b-instruct", FakeHttp(*r), account_id=ACC, **FAST, **kw)
def orr(*r, **kw): return OpenRouterProvider("sk-or-v1-" + "y" * 30, "openrouter/free", FakeHttp(*r), **FAST, **kw)


# ---------------- request format
def test_groq_request_format_and_success():
    p = groq(R(200, openai_body(OKJ)))
    assert p.complete_json("Reply as JSON") == {"ok": True, "word": "pong"}
    url, kw = p.http.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert kw["headers"]["Authorization"].startswith("Bearer gsk_")
    assert kw["json"]["model"] == "openai/gpt-oss-20b" and kw["json"]["response_format"] == {"type": "json_object"}


def test_cloudflare_request_and_both_response_shapes():
    p = cf(R(200, cf_body(OKJ)))
    assert p.complete_json("x")["ok"] is True
    url, kw = p.http.calls[0]
    assert url == f"https://api.cloudflare.com/client/v4/accounts/{ACC}/ai/run/@cf/meta/llama-3.1-8b-instruct"
    assert cf(R(200, cf_body({"ok": True, "word": "pong"}))).complete_json("x")["word"] == "pong"


def test_openrouter_request_format():
    p = orr(R(200, openai_body(OKJ)))
    p.complete_json("x")
    url, kw = p.http.calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions" and kw["json"]["model"] == "openrouter/free"


# ---------------- failure handling per provider
def test_groq_429_uses_retry_after_then_succeeds():
    p = groq(jerr(429, "Rate limit reached", {"retry-after": "3"}), R(200, openai_body(OKJ)))
    p.complete_json("x")
    assert len(p.http.calls) == 2 and p.stats.retries == 1


def test_groq_daily_limit_is_quota_not_retried():
    p = groq(jerr(429, "Rate limit reached for model on tokens per day (TPD): Limit 200000"))
    with pytest.raises(AIError) as e:
        p.complete_json("x")
    assert e.value.kind == QUOTA and len(p.http.calls) == 1


def test_groq_bad_key_and_bad_model():
    with pytest.raises(AIError) as e:
        groq(jerr(401, "Invalid API Key")).complete_json("x")
    assert e.value.kind == AUTH
    with pytest.raises(AIError) as e:
        groq(jerr(400, "The model `foo` does not exist")).complete_json("x")
    assert e.value.kind == MODEL


def test_cloudflare_neuron_quota_exhausted():
    p = cf(R(429, {"success": False, "errors": [{"code": 4006, "message": "you have used up your daily free allocation of 10,000 neurons"}]}))
    with pytest.raises(AIError) as e:
        p.complete_json("x")
    assert e.value.kind == QUOTA and len(p.http.calls) == 1


def test_cloudflare_json_mode_failure_and_malformed():
    with pytest.raises(AIError) as e:
        cf(R(400, {"success": False, "errors": [{"code": 8006, "message": "JSON Mode couldn't be met"}]})).complete_json("x")
    assert e.value.kind == MALFORMED
    with pytest.raises(AIError):
        cf(R(200, {"success": True, "result": {}})).complete_json("x")


def test_cloudflare_bad_account_id_is_config_error_without_request():
    p = CloudflareProvider("t", "@cf/meta/llama-3.1-8b-instruct", FakeHttp(R(200, cf_body(OKJ))), account_id="abc", **FAST)
    with pytest.raises(AIError) as e:
        p.complete_json("x")
    assert e.value.kind == CONFIG and p.http.calls == []


def test_openrouter_free_model_unavailable_and_200_error_body():
    with pytest.raises(AIError) as e:
        orr(jerr(404, "No endpoints found that support the requested response_format")).complete_json("x")
    assert e.value.kind == MODEL
    p = orr(R(200, {"error": {"message": "Provider returned error", "code": 502}}), max_retries=1)
    with pytest.raises(AIError):
        p.complete_json("x")
    assert len(p.http.calls) == 1                       # 200-with-error is reported, not looped on
    with pytest.raises(AIError) as e:
        orr(jerr(402, "Insufficient credits")).complete_json("x")
    assert e.value.kind == QUOTA


@pytest.mark.parametrize("mk,body", [(groq, openai_body("not json")), (groq, {"choices": []}), (orr, openai_body("")),
                                     (cf, cf_body("plain words")), (gem, gemini_body("{broken"))])
def test_malformed_replies_rejected_by_every_provider(mk, body):
    with pytest.raises(AIError) as e:
        mk(R(200, body)).complete_json("x")
    assert e.value.kind == MALFORMED


# ---------------- router
def router(*providers, **kw):
    return AIRouter(list(providers), **kw)


def test_router_uses_first_provider_and_counts():
    g, m = groq(R(200, openai_body(OKJ))), gem(R(200, gemini_body(OKJ)))
    r = router(g, m)
    out, name = r.complete_json("x")
    assert name == "groq" and out["ok"] and m.http.calls == []
    assert r.usage()["groq"]["successes"] == 1 and r.fallback_attempts == 0


def test_router_falls_back_in_priority_order_and_counts_fallbacks():
    g = groq(jerr(503, "overloaded"), max_retries=0)
    m = gem(R(500, {"error": {"message": "x"}}), max_retries=0)
    c = cf(R(200, cf_body(OKJ)))
    r = router(g, m, c)
    _, name = r.complete_json("x")
    assert name == "cloudflare" and r.fallback_attempts == 2
    u = r.usage()
    assert u["groq"]["failures"] == 1 and u["gemini"]["failures"] == 1 and u["cloudflare"]["successes"] == 1


def test_router_permanent_error_disables_provider_for_run():
    g = groq(jerr(401, "Invalid API Key"))
    m = gem(R(200, gemini_body(OKJ)))
    r = router(g, m)
    r.complete_json("a"); r.complete_json("b"); r.complete_json("c")
    assert len(g.http.calls) == 1 and len(m.http.calls) == 3          # the bad provider is not called again
    assert r.usage()["groq"]["disabled"]


def test_router_quota_disables_and_two_transient_failures_disable():
    g = groq(jerr(503, "down"), max_retries=0)
    m = gem(R(200, gemini_body(OKJ)))
    r = router(g, m)
    for _ in range(5):
        r.complete_json("x")
    assert len(g.http.calls) == 2 and len(m.http.calls) == 5


def test_router_respects_long_retry_after_with_cooldown_then_returns():
    t = [0.0]
    g = groq(jerr(429, "slow down", {"retry-after": "900"}), R(200, openai_body(OKJ)), max_retry_after=30)
    m = gem(R(200, gemini_body(OKJ)))
    r = AIRouter([g, m], clock=lambda: t[0])
    assert r.complete_json("x")[1] == "gemini"
    assert r.complete_json("y")[1] == "gemini"                         # still cooling down: groq untouched
    assert len(g.http.calls) == 1
    t[0] = 901
    assert r.complete_json("z")[1] == "groq"                           # cool-down over, groq tried again


def test_router_per_provider_call_limit():
    g, m = groq(R(200, openai_body(OKJ))), gem(R(200, gemini_body(OKJ)))
    r = AIRouter([g, m], {"groq": 2})
    names = [r.complete_json(str(i))[1] for i in range(4)]
    assert names == ["groq", "groq", "gemini", "gemini"]


def test_router_validator_triggers_fallback_on_wrong_shape():
    g = groq(R(200, openai_body('{"unexpected": 1}')))
    m = gem(R(200, gemini_body('{"quote": "2 years"}')))
    r = router(g, m)
    out, name = r.complete_json("x", validate=lambda d: "quote" in d)
    assert name == "gemini" and out["quote"] == "2 years"


def test_router_all_fail_raises_and_none_usable_raises():
    r = router(groq(jerr(401, "bad")), gem(R(401, {"error": {"message": "bad"}})))
    with pytest.raises(AIError):
        r.complete_json("x")
    assert not r.available
    with pytest.raises(AIError):
        r.complete_json("x")


def test_router_network_timeout_falls_back():
    g = groq(requests.Timeout("t"), max_retries=0)
    r = router(g, gem(R(200, gemini_body(OKJ))))
    assert r.complete_json("x")[1] == "gemini"


# ---------------- building from settings
def test_build_providers_order_enabled_and_credentials():
    env = {"GROQ_API_KEY": "gsk_" + "a" * 30, "GEMINI_API_KEY": "g" * 20, "CLOUDFLARE_API_TOKEN": "t" * 20,
           "CLOUDFLARE_ACCOUNT_ID": ACC, "OPENROUTER_API_KEY": "o" * 20}
    s = load_settings(environ=env)
    assert [p.name for p in build_providers(s, None)] == ["groq", "gemini", "cloudflare"]      # openrouter off by default
    s = load_settings(environ={**env, "OPENROUTER_ENABLED": "true", "AI_PROVIDER_ORDER": "openrouter, gemini,groq", "GROQ_ENABLED": "false"})
    assert [p.name for p in build_providers(s, None)] == ["openrouter", "gemini"]
    assert build_providers(load_settings(environ={**env, "AI_ENABLED": "false"}), None) == []
    assert build_router(load_settings(environ={}), None) is None
    # cloudflare without account id is skipped, not crashed
    s = load_settings(environ={"CLOUDFLARE_API_TOKEN": "t" * 20})
    assert build_providers(s, None) == []


def test_settings_validation_and_secrets_for_all_providers():
    s = load_settings(environ={"AI_PROVIDER_ORDER": "groq,foo", "OPENROUTER_ENABLED": "true", "CLOUDFLARE_API_TOKEN": "t" * 20,
                               "GROQ_API_KEY": "gsk_" + "k" * 30})
    probs = " ".join(s.ai_problems())
    assert "foo" in probs and "OPENROUTER_API_KEY" in probs and "CLOUDFLARE_ACCOUNT_ID" in probs
    assert "gsk_" + "k" * 30 in s.secret_values() and "t" * 20 in s.secret_values()


def test_check_ai_cli_tests_each_provider_independently(capsys, monkeypatch):
    import main
    monkeypatch.setattr(main, "_providers", lambda s, h: [groq(R(200, openai_body(OKJ))), gem(R(404, {"error": {"message": "model not found"}})),
                                                          cf(R(200, cf_body("nope")))])
    assert main.main(["check-ai"]) == 1
    out = capsys.readouterr().out
    assert "[groq]" in out and "[gemini]" in out and "[cloudflare]" in out
    assert "1 of 3 provider(s) working" in out and "gsk_xxxx" not in out


# ---------------- Stage 4: extensibility (custom OpenAI-compatible provider)
def test_custom_provider_from_settings_only_and_request_url():
    from integrations.ai_providers import CustomProvider
    env = {"CUSTOM_ENABLED": "true", "CUSTOM_BASE_URL": "https://api.example-ai.com/v1/", "CUSTOM_API_KEY": "ck" * 10,
           "CUSTOM_MODEL": "some-model-8b", "GROQ_API_KEY": "gsk_" + "a" * 30}
    s = load_settings(environ=env)
    provs = build_providers(s, FakeHttp(R(200, openai_body(OKJ))))
    assert [p.name for p in provs] == ["groq", "custom"]                      # enabled -> appended last automatically
    c = provs[1]
    assert isinstance(c, CustomProvider) and c.complete_json("x")["ok"]
    assert c.http.calls[0][0] == "https://api.example-ai.com/v1/chat/completions"
    assert "ck" * 10 in s.secret_values()


def test_custom_provider_rejects_plain_http_and_incomplete_config():
    from integrations.ai_providers import CustomProvider
    p = CustomProvider("k", "m", FakeHttp(R(200, openai_body(OKJ))), base_url="http://insecure.example/v1", **FAST)
    with pytest.raises(AIError) as e:
        p.complete_json("x")
    assert e.value.kind == CONFIG and p.http.calls == []
    s = load_settings(environ={"CUSTOM_ENABLED": "true", "CUSTOM_BASE_URL": "http://x"})
    assert any("CUSTOM_ENABLED" in m for m in s.ai_problems())


def test_new_compatible_provider_is_a_five_line_subclass():
    from integrations.ai_providers import OpenAICompatProvider

    class Cerebras(OpenAICompatProvider):
        name, url = "cerebras", "https://api.cerebras.ai/v1/chat/completions"

    p = Cerebras("k", "llama3.1-8b", FakeHttp(R(200, openai_body(OKJ))), **FAST)
    assert p.complete_json("x")["word"] == "pong"
