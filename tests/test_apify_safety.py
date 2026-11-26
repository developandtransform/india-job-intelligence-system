"""Safety patch 4: Apify stays off by default and can never spend more than approved. Fake HTTP only (no request leaves the process)."""
import dataclasses
from pathlib import Path

import pytest

from config.settings import APIFY_ACTORS, load_settings
from connectors.apify_connector import ApifyConnector, SpendTracker, ACTORS
from tests.helpers import StubConnector, TODAY, mk, settings
from tests.test_apify_gemini_mocked import FakeHttp, Resp, cfg

NOPE = Path("/nonexistent/.env")


def posts(http):
    return [c for c in http.calls if c[0] == "POST" and "/runs" in c[1] and "abort" not in c[1]]


def test_off_by_default_everywhere():
    s = load_settings(environ={}, env_path=NOPE)
    assert not s.apify_enabled and s.enabled_apify_actors() == [] and s.apify_approved_max_usd_per_run == 0.0
    assert "\nAPIFY_ENABLED=false\n" in (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    http = FakeHttp()
    assert ApifyConnector(s, http).run().status == "skipped" and http.calls == []


@pytest.mark.parametrize("over", [
    dict(apify_enabled=False),                                   # master switch off
    dict(apify_token=""),                                        # no token
    dict(apify_approved_max_usd_per_run=0.0),                    # no approved budget (missing approval)
    dict(apify_enable={}),                                       # no actor individually enabled
    dict(apify_enable={"naukri": False}),                        # the actor is disabled
])
def test_every_gate_alone_prevents_execution(over):
    http = FakeHttp()
    c = ApifyConnector(cfg(**over), http)
    assert c.settings.enabled_apify_actors() == []
    res = c.run()
    assert http.calls == [] and res.status == "skipped"


def test_only_individually_enabled_actors_run():
    http = FakeHttp()
    ApifyConnector(cfg(apify_enable={"naukri": True, "linkedin": False}), http).run()
    urls = " ".join(c[1] for c in posts(http))
    assert ACTORS["naukri"].actor_id.replace("/", "~") in urls
    assert ACTORS["linkedin"].actor_id.replace("/", "~") not in urls


def test_dry_run_never_executes_even_with_everything_approved():
    http = FakeHttp()
    res = ApifyConnector(cfg(dry_run=True), http).run()
    assert http.calls == [] and res.status == "skipped" and "dry run" in res.note
    c = ApifyConnector(cfg(dry_run=True), http)
    with pytest.raises(RuntimeError, match="dry run"):
        c._run_actor(ACTORS["naukri"], {}, 10, 0.5, 9e9)          # even a direct call is refused
    assert http.calls == []


def test_per_run_budget_is_a_hard_limit_across_actors_and_queries():
    http = FakeHttp(cost=0.30)
    c = ApifyConnector(cfg(apify_approved_max_usd_per_run=0.35, apify_max_queries_per_actor=5,
                           apify_enable={a: True for a in APIFY_ACTORS}), http)
    res = c.run()
    assert c.tracker.spent_usd <= 0.35 + 0.30 + 1e-9                # the run that was started can overshoot only to its own cap
    assert len(posts(http)) <= 2
    for _, _, kw in posts(http):
        assert kw["params"]["maxTotalChargeUsd"] <= 0.35 and kw["params"]["maxItems"] >= 1


def test_per_actor_cap_is_enforced():
    t = SpendTracker(approved_usd=10.0, per_actor_cap_usd=0.2)
    spec = ACTORS["naukri"]
    limit, max_usd = t.plan(spec, 1000, actor_spent=0.0)
    assert max_usd <= 0.2
    assert t.plan(spec, 1000, actor_spent=0.2) == (0, 0.0)             # cap used up: do not run


def test_zero_or_negative_budget_means_do_not_run():
    t = SpendTracker(approved_usd=0.0, per_actor_cap_usd=0.5)
    assert t.plan(ACTORS["naukri"], 50, 0.0) == (0, 0.0)
    assert load_settings(environ={"APIFY_APPROVED_MAX_USD_PER_RUN": "-5"}, env_path=NOPE).apify_approved_max_usd_per_run == 0.0


def test_error_after_start_is_never_retried_and_is_counted_as_spent():
    class LostAnswer(FakeHttp):
        def post(self, url, **kw):
            self.calls.append(("POST", url, kw))
            raise TimeoutError("answer lost - the run may have started")
    http = LostAnswer()
    c = ApifyConnector(cfg(apify_max_queries_per_actor=5, apify_approved_max_usd_per_run=1.0), http)
    res = c.run()
    assert len(posts(http)) == 1                                       # exactly one attempt, no retry, no second query
    assert c.tracker.spent_usd > 0 and res.errors                      # conservatively counted against the budget


def test_server_error_response_is_not_retried():
    class Err(FakeHttp):
        def post(self, url, **kw):
            self.calls.append(("POST", url, kw))
            return Resp(503, {})
    http = Err()
    ApifyConnector(cfg(apify_max_queries_per_actor=4), http).run()
    assert len(posts(http)) == 1


def test_http_client_never_retries_post_automatically():
    from core.http import HttpClient
    adapter = HttpClient().session.get_adapter("https://api.apify.com")
    assert "POST" not in adapter.max_retries.allowed_methods


@pytest.mark.parametrize("env", [
    {"APIFY_ENABLED": "true", "APIFY_TOKEN": "t" * 20, "APIFY_APPROVED_MAX_USD_PER_RUN": "abc", "APIFY_ENABLE_NAUKRI": "true"},
    {"APIFY_ENABLED": "maybe", "APIFY_TOKEN": "t" * 20, "APIFY_APPROVED_MAX_USD_PER_RUN": "5", "APIFY_ENABLE_NAUKRI": "true"},
    {"APIFY_ENABLED": "true", "APIFY_TOKEN": "t" * 20, "APIFY_APPROVED_MAX_USD_PER_RUN": "5", "APIFY_ENABLE_NAUKRI": "perhaps"},
    {"APIFY_ENABLED": "true", "APIFY_TOKEN": "", "APIFY_APPROVED_MAX_USD_PER_RUN": "5", "APIFY_ENABLE_NAUKRI": "true"},
])
def test_invalid_or_incomplete_configuration_fails_safe(env):
    s = load_settings(environ=env, env_path=NOPE)
    assert s.enabled_apify_actors() == []                              # nothing may run on unclear input
    http = FakeHttp()
    assert ApifyConnector(s, http).run().status == "skipped" and http.calls == []


def test_broken_actor_input_json_does_not_crash_or_spend():
    s = load_settings(environ={"APIFY_INPUT_JSON_NAUKRI": "{not json"}, env_path=NOPE)
    assert s.apify_input_json["naukri"] == {}


def test_apify_problems_do_not_stop_the_free_pipeline():
    from processing.pipeline import run_pipeline
    from integrations.store import MemoryStore
    bad = dataclasses.replace(settings(), apify_enabled=True, apify_token="", apify_approved_max_usd_per_run=0.0)
    store = MemoryStore()
    out = run_pipeline(bad, store, [StubConnector([mk("1", desc="Experience: 1-2 years. CTC 10 LPA.")]), ApifyConnector(bad, FakeHttp())],
                       today=TODAY, send_mail=False)
    assert len(store.master) == 1 and any(r.name == "apify" and r.status == "skipped" for r in out.results)
