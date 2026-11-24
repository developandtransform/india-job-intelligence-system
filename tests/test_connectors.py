"""Connector logic that can be tested WITHOUT network access: parsers (on fixture payloads), Apify gating and spend control.

NOTE: these use hand-written fixture payloads shaped like the vendors' documented responses. They prove our parsing code,
NOT that the live endpoints still return this shape - live behaviour is untested (see docs/TESTING.md)."""
import dataclasses

from config.settings import load_settings
from connectors import ats_connector as A
from connectors.apify_connector import ACTORS, ApifyConnector, SpendTracker
from connectors.base import Connector
from core.http import HttpClient
from tests.helpers import settings


def test_greenhouse_parser():
    jobs = A.parse_greenhouse({"jobs": [{"id": 7, "title": "Sales Executive", "location": {"name": "Bengaluru, India"},
                                         "absolute_url": "https://boards.greenhouse.io/acme/jobs/7", "content": "&lt;p&gt;2 years experience&lt;/p&gt;",
                                         "departments": [{"name": "Sales"}]}]}, "acme", "Acme")
    assert len(jobs) == 1 and jobs[0].original_id == "7" and jobs[0].company == "Acme" and "Bengaluru" in jobs[0].location


def test_lever_parser_structured_salary():
    jobs = A.parse_lever([{"id": "x1", "text": "Growth Analyst", "hostedUrl": "https://jobs.lever.co/a/x1",
                           "categories": {"location": "Mumbai", "commitment": "Full-time"}, "descriptionPlain": "desc",
                           "salaryRange": {"min": 1000000, "max": 1500000, "currency": "INR", "interval": "per-year-salary"}}], "a", "A")
    assert jobs[0].hint_salary["min"] == 1000000 and jobs[0].hint_salary["period"] == "year"


def test_parsers_tolerate_garbage():
    assert A.parse_greenhouse({}, "x", "X") == []
    assert A.parse_lever({"not": "a list"}, "x", "X") == []
    assert A.parse_ashby({}, "x", "X") == []


def test_parse_target():
    assert A.parse_target("greenhouse:stripe|Stripe") == ("greenhouse", "stripe", "Stripe")
    assert A.parse_target("nonsense") is None
    assert A.parse_target("unknownats:x") is None


def _apify_cfg(**over):
    base = dict(apify_enabled=True, apify_token="tok_secret_value", apify_approved_max_usd_per_run=1.0,
                apify_enable={"naukri": True})
    base.update(over)
    return dataclasses.replace(settings(), **base)


def test_apify_disabled_by_default():
    s = load_settings(environ={}, env_path=__import__("pathlib").Path("/nonexistent"))
    assert s.enabled_apify_actors() == [] and not ApifyConnector(s, HttpClient()).enabled()


def test_apify_requires_every_gate():
    assert _apify_cfg().enabled_apify_actors() == ["naukri"]
    assert _apify_cfg(apify_enabled=False).enabled_apify_actors() == []
    assert _apify_cfg(apify_token="").enabled_apify_actors() == []
    assert _apify_cfg(apify_approved_max_usd_per_run=0.0).enabled_apify_actors() == []
    assert _apify_cfg(apify_enable={}).enabled_apify_actors() == []


def test_spend_tracker_limits_results_to_budget():
    t = SpendTracker(approved_usd=0.10, per_actor_cap_usd=0.5)
    limit, cap = t.plan(ACTORS["iimjobs"], wanted=1000, actor_spent=0.0)
    assert limit * ACTORS["iimjobs"].price_per_result + ACTORS["iimjobs"].price_per_run <= 0.10
    assert cap <= 0.10


def test_spend_tracker_refuses_when_budget_gone():
    t = SpendTracker(approved_usd=0.10, per_actor_cap_usd=0.5)
    t.record("x", 0.10)
    assert t.plan(ACTORS["naukri"], 100, 0.0)[0] == 0


def test_spend_tracker_per_actor_cap():
    t = SpendTracker(approved_usd=5.0, per_actor_cap_usd=0.02)
    assert t.plan(ACTORS["google_jobs"], 50, 0.0)[0] == 0      # flat $0.10 start > $0.02 cap


def test_secret_values_include_token_for_log_redaction():
    assert "tok_secret_value" in _apify_cfg().secret_values()
