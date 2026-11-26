"""Apify and Gemini behaviour with a fake HTTP layer. No request leaves this process."""
import dataclasses
import time

from connectors.apify_connector import ACTORS, ApifyConnector
from processing.ai_assist import GeminiAssistant
from tests.helpers import settings


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    def __init__(self, items=None, cost=0.05):
        self.calls, self.items, self.cost = [], items or [], cost

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return Resp(201, {"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1", "usageTotalUsd": self.cost}})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if "datasets" in url:
            return Resp(200, self.items)
        return Resp(200, {"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds1", "usageTotalUsd": self.cost}})


def cfg(**over):
    base = dict(apify_enabled=True, apify_token="tok", apify_approved_max_usd_per_run=1.0, apify_max_usd_per_actor=0.5,
                apify_enable={"naukri": True}, apify_max_results={"naukri": 30}, apify_max_queries_per_actor=2)
    base.update(over)
    return dataclasses.replace(settings(), **base)


def test_nothing_is_called_when_disabled_by_default():
    http = FakeHttp()
    conn = ApifyConnector(settings(), http)
    assert not conn.enabled()
    res = conn.run()
    assert http.calls == [] and res.status in ("skipped", "disabled", "ok") and not res.jobs


def test_result_limit_and_budget_are_sent_with_every_run():
    http = FakeHttp(items=[{"title": f"t{i}"} for i in range(500)])
    res = ApifyConnector(cfg(), http).run()
    posts = [c for c in http.calls if c[0] == "POST" and "/runs" in c[1]]
    assert posts
    for _, _, kw in posts:
        assert kw["params"]["maxItems"] <= 30 and 0 < kw["params"]["maxTotalChargeUsd"] <= 0.5
    assert res.raw_count <= 30 * len(posts)                       # never accepts more than the limit


def test_total_budget_stops_further_runs():
    http = FakeHttp(cost=0.30)
    ApifyConnector(cfg(apify_approved_max_usd_per_run=0.35, apify_max_queries_per_actor=5), http).run()
    assert len([c for c in http.calls if c[0] == "POST" and "/runs" in c[1]]) <= 2


def test_failure_of_actor_is_isolated_and_reported():
    class Boom(FakeHttp):
        def post(self, url, **kw):
            raise ConnectionError("network down")
    res = ApifyConnector(cfg(), Boom()).run()
    assert res.errors and not res.jobs


def test_token_is_sent_only_in_header_never_in_url():
    http = FakeHttp()
    ApifyConnector(cfg(apify_token="SECRET-TOKEN-XYZ"), http).run()
    assert all("SECRET-TOKEN-XYZ" not in c[1] for c in http.calls)


# ---------------- Gemini

class GemHttp:
    def __init__(self, statuses, body=None):
        self.statuses, self.n, self.body = statuses, 0, body or {"candidates": [{"content": {"parts": [{"text": '{"quote": "1-2 years"}'}]}}]}

    def post(self, url, **kw):
        s = self.statuses[min(self.n, len(self.statuses) - 1)]
        self.n += 1
        return Resp(s, self.body)


def gem(statuses, **kw):
    # sleep is injected as a no-op so back-off pauses cost no real time
    return GeminiAssistant("k", "m", kw.get("max_calls", 5), GemHttp(statuses), sleep=lambda s: None)


def test_gemini_caches_identical_prompts():
    g = gem([200])
    g.find_experience_quote("t", "Experience: 1-2 years")
    g.find_experience_quote("t", "Experience: 1-2 years")
    assert g.http.n == 1


def test_gemini_retries_transient_error():
    g = gem([503, 200])
    g.find_experience_quote("t", "Experience: 1-2 years")
    assert g.http.n == 2


def test_gemini_does_not_retry_permanent_errors():
    g = gem([401])
    assert g.find_experience_quote("t", "Experience: 1-2 years") is None
    assert g.http.n == 1


def test_gemini_call_limit_and_self_disable():
    g = gem([500], max_calls=2)
    for i in range(10):
        g.find_experience_quote("t", f"text {i}")
    # max_calls now limits QUESTIONS; each question may use up to 1 + 3 HTTP attempts (retry policy changed in Stage 1)
    assert g.http.n <= 2 * 4
    assert not g.available


def test_gemini_quote_must_exist_verbatim_in_text():
    g = gem([200])
    assert g.find_experience_quote("t", "Experience: five years") is None       # reply quotes text that is not there
