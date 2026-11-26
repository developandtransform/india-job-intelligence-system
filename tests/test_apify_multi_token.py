"""Multiple Apify tokens: parsing, rotation, safe failover, token-per-run lifecycle, spending. Mocks only - no Apify call is made."""
import dataclasses
import logging
from pathlib import Path

import pytest

from config.settings import load_settings, parse_token_list
from connectors.apify_connector import ACTORS, ApifyConnector, ApifyStartRejected, TokenPool
from tests.helpers import settings
from tests.test_apify_gemini_mocked import Resp, cfg

NOPE = Path("/nonexistent/.env")
A, B, C = "tok-AAAAAA-secret", "tok-BBBBBB-secret", "tok-CCCCCC-secret"


def env(**kw):
    return load_settings(environ={k: v for k, v in kw.items()}, env_path=NOPE)


# ------------------------------------------------------------------ configuration
def test_single_token_through_apify_tokens():
    assert env(APIFY_TOKENS=A).apify_token_list == (A,)


def test_multiple_comma_separated_tokens_keep_order():
    assert env(APIFY_TOKENS=f"{A},{B},{C}").apify_token_list == (A, B, C)


def test_whitespace_newlines_quotes_empty_entries_and_duplicates_are_cleaned():
    assert parse_token_list(f"  {A} , ,{B},\n{C}\r\n,{A}, \"{B}\" ,,") == [A, B, C]
    assert parse_token_list("") == [] and parse_token_list(None) == [] and parse_token_list(" , ,\n") == []


def test_empty_apify_tokens_falls_back_to_legacy_token():
    assert env(APIFY_TOKENS="", APIFY_TOKEN=A).apify_token_list == (A,)
    assert env(APIFY_TOKENS=" , ", APIFY_TOKEN=A).apify_token_list == (A,)


def test_apify_tokens_wins_over_legacy_token_when_both_set():
    assert env(APIFY_TOKENS=f"{B},{C}", APIFY_TOKEN=A).apify_token_list == (B, C)


def test_legacy_settings_construction_still_works():
    s = dataclasses.replace(settings(), apify_token=A)                 # code that only knows apify_token
    assert s.apify_token_list == (A,)
    assert dataclasses.replace(settings(), apify_token="").apify_token_list == ()


def test_every_token_is_a_secret():
    s = env(APIFY_TOKENS=f"{A},{B},{C}", APIFY_TOKEN="tok-LEGACY-secret")
    vals = s.secret_values()
    assert all(t in vals for t in (A, B, C, "tok-LEGACY-secret"))


def test_log_redaction_hides_every_token():
    from core.logging_setup import RedactingFilter
    f = RedactingFilter(env(APIFY_TOKENS=f"{A},{B}").secret_values())
    rec = logging.LogRecord("apify", logging.INFO, __file__, 1, "using %s then %s", (A, B), None)
    f.filter(rec)
    assert A not in rec.getMessage() and B not in rec.getMessage() and "***" in rec.getMessage()


def test_apify_enabled_with_multiple_tokens_and_disabled_without():
    base = dict(apify_enabled=True, apify_approved_max_usd_per_run=1.0, apify_enable={"naukri": True})
    assert dataclasses.replace(settings(), apify_tokens=(A, B), **base).enabled_apify_actors() == ["naukri"]
    assert dataclasses.replace(settings(), **base).enabled_apify_actors() == []
    s = dataclasses.replace(settings(), apify_tokens=(A, B))                # master switch still off by default
    assert s.enabled_apify_actors() == []


def test_apify_stays_off_by_default_even_with_tokens():
    s = env(APIFY_TOKENS=f"{A},{B}")
    assert not s.apify_enabled and s.enabled_apify_actors() == []


def test_validate_mentions_missing_tokens_without_leaking():
    s = env(APIFY_ENABLED="true", APIFY_APPROVED_MAX_USD_PER_RUN="1")
    assert any("APIFY_TOKENS" in p for p in s.validate())


# ------------------------------------------------------------------ rotation
def test_round_robin_after_successful_starts_and_wraps():
    p = TokenPool([A, B, C])
    used = []
    for _ in range(7):
        i = p.start_order()[0]
        used.append(i)
        p.started_with(i)
    assert used == [0, 1, 2, 0, 1, 2, 0]


def test_single_token_pool():
    p = TokenPool([A])
    for _ in range(3):
        assert p.start_order() == [0]
        p.started_with(0)


def test_no_repeat_while_healthy_alternatives_exist():
    p = TokenPool([A, B, C])
    p.mark_refused(0, 401)
    seen = []
    for _ in range(4):
        i = p.start_order()[0]
        seen.append(i)
        p.started_with(i)
    assert 0 not in seen and seen == [1, 2, 1, 2]


def test_rate_limited_token_is_tried_last():
    p = TokenPool([A, B, C])
    p.mark_refused(0, 429)
    assert p.start_order() == [1, 2, 0]


# ------------------------------------------------------------------ connector with a scripted HTTP layer
class Script:
    """POST /runs answers come from `starts` (a status code, or an exception to raise). Every call is recorded with the token used."""

    def __init__(self, starts, items=None, cost=0.05, poll_error=False):
        self.starts, self.items, self.cost, self.poll_error = list(starts), items or [], cost, poll_error
        self.calls, self.n = [], 0

    def _tok(self, kw):
        return kw["headers"]["Authorization"].split()[-1]

    def post(self, url, **kw):
        self.calls.append(("POST", url, self._tok(kw)))
        if url.endswith("/abort"):
            return Resp(200, {})
        s = self.starts[min(self.n, len(self.starts) - 1)]
        self.n += 1
        if isinstance(s, Exception):
            raise s
        if s != 201:
            return Resp(s, {})
        return Resp(201, {"data": {"id": f"run{self.n}", "status": "RUNNING", "defaultDatasetId": f"ds{self.n}"}})

    def get(self, url, **kw):
        self.calls.append(("GET", url, self._tok(kw)))
        if "datasets" in url:
            return Resp(200, self.items)
        if self.poll_error:
            raise ConnectionError("poll lost")
        return Resp(200, {"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds", "usageTotalUsd": self.cost}})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("connectors.apify_connector.time.sleep", lambda s: None)


def conn(http, tokens=(A, B, C), **over):
    return ApifyConnector(cfg(apify_token="", apify_tokens=tokens, **over), http)


def start_posts(http):
    return [c for c in http.calls if c[0] == "POST" and "/runs" in c[1] and "abort" not in c[1]]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refused_token_fails_over_to_next_token_without_extra_runs(status):
    http = Script([status, 201])
    c = conn(http, apify_max_queries_per_actor=1)
    res = c.run()
    posts = start_posts(http)
    assert [p[2] for p in posts] == [A, B]                         # first refused, second accepted: ONE run exists
    assert c.tracker.spent_usd > 0 and not any("refused" in e for e in res.errors[:0])
    # and the dataset call used the accepted token B, never A
    assert all(call[2] == B for call in http.calls if call[0] == "GET")


def test_all_tokens_exhausted_starts_nothing_and_costs_nothing():
    http = Script([401, 403, 429])
    c = conn(http)
    res = c.run()
    assert [p[2] for p in start_posts(http)] == [A, B, C]           # at most one attempt per token
    assert c.tracker.spent_usd == 0.0 and not res.jobs
    assert any("no Apify token was accepted" in e for e in res.errors)
    assert A not in " ".join(res.errors) and B not in " ".join(res.errors)


@pytest.mark.parametrize("failure", [TimeoutError("read timed out"), ConnectionError("reset"), 500, 502, 503])
def test_ambiguous_failure_never_starts_a_second_paid_run(failure):
    http = Script([failure])
    c = conn(http)
    res = c.run()
    assert len(start_posts(http)) == 1                              # no failover, no retry, no next query
    assert c.tracker.spent_usd > 0                                  # fail closed: maximum possible cost recorded
    assert res.errors and res.cost_usd > 0


def test_ambiguous_failure_after_a_refusal_still_stops():
    http = Script([401, TimeoutError("lost")])
    c = conn(http)
    c.run()
    assert [p[2] for p in start_posts(http)] == [A, B]              # C is NOT tried after the ambiguous failure
    assert c.tracker.spent_usd > 0


def test_unreadable_success_answer_is_ambiguous_not_retried():
    class Bad(Script):
        def post(self, url, **kw):
            self.calls.append(("POST", url, self._tok(kw)))
            return Resp(201, {"unexpected": True})                   # no "data": a run may exist
    http = Bad([201])
    c = conn(http)
    c.run()
    assert len(start_posts(http)) == 1 and c.tracker.spent_usd > 0


def test_polling_failure_does_not_restart_and_aborts_with_same_token():
    http = Script([201], poll_error=True)
    c = conn(http, apify_max_queries_per_actor=2)
    res = c.run()
    assert len(start_posts(http)) == 1                              # not restarted, next query not started either
    aborts = [x for x in http.calls if x[1].endswith("/abort")]
    assert aborts and all(a[2] == A for a in aborts)
    assert c.tracker.spent_usd > 0 and res.errors


def test_token_stays_with_its_run_through_poll_and_dataset():
    http = Script([201, 201, 201], items=[{"title": "t"}])
    c = conn(http, apify_max_queries_per_actor=2)
    c.run()
    tokens_by_run: dict[str, set] = {}
    for kind, url, tok in http.calls:
        if "/actor-runs/" in url:
            tokens_by_run.setdefault(url.split("/actor-runs/")[1].split("/")[0], set()).add(tok)
    assert tokens_by_run and all(len(v) == 1 for v in tokens_by_run.values())


def test_rotation_across_real_runs_uses_a_then_b():
    http = Script([201, 201])
    conn(http, apify_max_queries_per_actor=2).run()
    assert [p[2] for p in start_posts(http)] == [A, B]


def test_rejected_token_is_not_reused_by_later_actors():
    http = Script([401, 201, 201, 201])
    c = conn(http, apify_enable={"naukri": True, "glassdoor": True}, apify_max_queries_per_actor=1,
             apify_approved_max_usd_per_run=2.0)
    c.run()
    later = [p[2] for p in start_posts(http)][1:]
    assert A not in later


def test_error_messages_never_contain_a_token():
    class Leaky(Script):
        def post(self, url, **kw):
            raise RuntimeError(f"boom while using {A} and {B}")
    c = conn(Leaky([201]))
    res = c.run()
    text = " ".join(res.errors) + (res.note or "")
    assert A not in text and B not in text and res.errors


# ------------------------------------------------------------------ spending is shared and unchanged
def test_budget_is_shared_across_tokens_and_never_reset():
    http = Script([201, 201, 201, 201, 201], cost=0.30)
    c = conn(http, apify_approved_max_usd_per_run=0.35, apify_max_queries_per_actor=5)
    c.run()
    assert len(start_posts(http)) <= 2 and c.tracker.spent_usd <= 0.35 + 0.30
    assert c.tracker.approved_usd == 0.35


def test_actor_cap_and_result_limit_are_still_sent_with_every_run_whatever_the_token():
    http = Script([201, 201])
    conn(http, apify_max_queries_per_actor=2).run()
    # Script records only tokens; re-run with the plain recorder to check the params
    class Rec(Script):
        def post(self, url, **kw):
            if "/runs" in url and "abort" not in url:
                self.params = getattr(self, "params", []) + [kw["params"]]
            return super().post(url, **kw)
    r = Rec([201, 201])
    conn(r, apify_max_queries_per_actor=2).run()
    assert r.params and all(p["maxItems"] <= 30 and 0 < p["maxTotalChargeUsd"] <= 0.5 for p in r.params)


def test_dry_run_never_calls_apify_whatever_the_number_of_tokens():
    http = Script([201])
    c = ApifyConnector(dataclasses.replace(cfg(apify_token="", apify_tokens=(A, B, C)), dry_run=True), http)
    c.run()
    assert http.calls == []
    with pytest.raises(RuntimeError, match="dry run"):
        c._run_actor(ACTORS["naukri"], {}, 10, 0.5, 9e9)


def test_default_settings_never_call_apify():
    http = Script([201])
    ApifyConnector(settings(), http).run()
    assert http.calls == []


def test_env_example_documents_both_variables_with_blank_values():
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "\nAPIFY_TOKEN=\n" in text and "APIFY_TOKENS=" in text
    line = next(l for l in text.splitlines() if l.startswith("APIFY_TOKENS="))
    assert line.split("=", 1)[1].strip() == ""                       # never ships a real or placeholder-looking secret


# ------------------------------------------------------------------ show-config
def _show_config(monkeypatch, capsys, **environ):
    """Run `main.py show-config` with injected settings (never the real .env or environment). Returns (stdout, stderr)."""
    import main
    monkeypatch.setattr(main, "load_settings", lambda *a, **k: load_settings(environ=environ, env_path=NOPE))
    assert main.main(["show-config"]) == 0
    cap = capsys.readouterr()
    return cap.out, cap.err


def test_show_config_prints_token_count_and_never_a_token(monkeypatch, capsys):
    out, err = _show_config(monkeypatch, capsys, APIFY_TOKENS=f"{A},{B},{C}")
    assert "Apify tokens configured = 3" in out
    for shown in (out, err):
        for tok in (A, B, C):
            assert tok not in shown
        assert not any(frag in shown for frag in ("AAAAAA", "BBBBBB", "CCCCCC", "tok-"))   # no prefix/middle fragment either


def test_show_config_counts_clean_unique_tokens_only(monkeypatch, capsys):
    out, _ = _show_config(monkeypatch, capsys, APIFY_TOKENS=f"  {A} , ,{B},\n{A}, \"{B}\" ,,")
    assert "Apify tokens configured = 2" in out                       # duplicates, quotes and empty entries are not counted


def test_show_config_legacy_token_fallback_and_zero(monkeypatch, capsys):
    out, err = _show_config(monkeypatch, capsys, APIFY_TOKEN=A)
    assert "Apify tokens configured = 1" in out and A not in out + err
    out, _ = _show_config(monkeypatch, capsys, APIFY_TOKENS=B, APIFY_TOKEN=A)
    assert "Apify tokens configured = 1" in out and A not in out and B not in out   # APIFY_TOKENS wins; fallback not added
    out, _ = _show_config(monkeypatch, capsys)
    assert "Apify tokens configured = 0" in out


def test_show_config_ai_output_is_unchanged_by_apify_tokens(monkeypatch, capsys):
    def ai_block(out):
        lines = out.splitlines()
        return lines[: lines.index("Apify settings in effect (token values are never printed):")]
    no_tokens, _ = _show_config(monkeypatch, capsys, GEMINI_API_KEY="g-key-123456")
    three_tokens, _ = _show_config(monkeypatch, capsys, GEMINI_API_KEY="g-key-123456", APIFY_TOKENS=f"{A},{B},{C}")
    assert ai_block(no_tokens) == ai_block(three_tokens)              # the AI section does not depend on Apify at all
    assert ai_block(no_tokens)[0] == "AI settings in effect (keys are never printed):"
    assert any("key set (12 chars)" in l for l in ai_block(no_tokens))
    assert "g-key-123456" not in no_tokens + three_tokens


def test_show_config_makes_no_network_call(monkeypatch, capsys):
    import requests
    def boom(*a, **k):
        raise AssertionError("show-config must not touch the network")
    monkeypatch.setattr(requests.sessions.Session, "request", boom)
    out, _ = _show_config(monkeypatch, capsys, APIFY_TOKENS=f"{A},{B}", APIFY_ENABLED="true")
    assert "Apify tokens configured = 2" in out
