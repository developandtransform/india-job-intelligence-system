"""Stage 3: rules first, AI only for what is ambiguous, persistent cache, measured statistics.
300+ synthetic jobs through the REAL pipeline; the AI provider is a scripted fake (mocked - not a live test)."""
import pytest

from integrations.ai_providers import MALFORMED
from integrations.ai_router import AIRouter
from integrations.store import MemoryStore
from processing.ai_assist import AIAssistant
from processing.ai_cache import AICache, cache_key
from tests.ai_fakes import FakeProvider, role_aware_answer
from tests.helpers import TODAY, mk, run, settings

import random as _random
_rnd = _random.Random(7)
_letters = "bcdfghjklmnpqrstvwxz"
_vowels = "aeiou"
NAMES = []
while len(NAMES) < 400:                       # distinct pseudo-company names so the de-duplicator keeps every job
    n = "".join(_rnd.choice(_letters) + _rnd.choice(_vowels) for _ in range(4)).capitalize() + " Ltd"
    if n not in NAMES:
        NAMES.append(n)

LONG = "We offer a collaborative culture, learning opportunities and a clear growth path for the right person. " * 4


def straightforward(i):      # rules decide everything: known function, explicit experience, explicit CTC
    return mk(f"s{i}", "Business Development Executive", NAMES[i], "Pune, India",
              f"Number {i}. Experience: 1-2 years. CTC: 10 LPA. " + LONG)


def too_experienced(i):      # unknown title but 8+ years: rules exclude it, AI must not be asked
    return mk(f"x{i}", f"Zebra Wrangler {i}", NAMES[120 + i], "Pune, India", f"Requires 8+ years of experience. {LONG}")


def abroad(i):               # outside India: excluded by rules
    j = mk(f"a{i}", f"Zebra Wrangler {i}", NAMES[180 + i], "London, United Kingdom", f"On-site in London. {LONG}")
    j.hint_country = "United Kingdom"
    return j


def thin(i):                 # unknown title but almost no text: not worth a question
    return mk(f"t{i}", f"Zebra Wrangler {i}", NAMES[220 + i], "Pune, India", "Join us.")


def ambiguous_function(i):   # unknown title, enough text, nothing decisive: AI may help
    return mk(f"f{i}", f"Zebra Wrangler {i}", NAMES[240 + i], "Pune, India", f"Unit {i}. Experience: 1-2 years. Daily operations and process improvement work. {LONG}")   # 2 supporting terms: enough for Python to believe the AI, too few for a rules-only match


def ambiguous_experience(i):  # good title, no experience stated in a structured way: AI may locate the sentence
    return mk(f"e{i}", "Business Development Executive", NAMES[300 + i], "Pune, India",
              f"Unit {i}. Our company has 3-5 years of history. Exp: 1-2 yrs. {LONG}")


def corpus(extra_amb=0):
    jobs = [straightforward(i) for i in range(120)] + [too_experienced(i) for i in range(60)] + \
           [abroad(i) for i in range(40)] + [thin(i) for i in range(20)]
    jobs += [ambiguous_function(i) for i in range(40 + extra_amb)] + [ambiguous_experience(i) for i in range(30)]
    assert len(jobs) >= 300
    return jobs


def assistant(provider=None, max_calls=0, cache=None):
    prov = provider or FakeProvider(answer=role_aware_answer)
    return AIAssistant(AIRouter([prov]), max_calls, cache), prov


def pipeline(jobs, ai, store=None, **cfg):
    return run(jobs, store, cfg=settings(**cfg), ai=ai)


def test_straightforward_and_ruled_out_jobs_never_trigger_ai():
    ai, prov = assistant()
    out, _ = pipeline([straightforward(i) for i in range(120)] + [too_experienced(i) for i in range(60)] +
                      [abroad(i) for i in range(40)] + [thin(i) for i in range(20)], ai)
    assert prov.calls == 0
    u = ai.usage
    assert u.skipped_ineligible >= 100 and u.skipped_thin_text == 20 and len(u.needed) == 0
    assert u.rules_only == len(u.seen) == 240


def test_only_ambiguous_jobs_reach_the_provider():
    ai, prov = assistant()
    out, _ = pipeline(corpus(), ai)
    assert prov.calls == 70                                  # 40 function questions + 30 experience questions
    assert len(ai.usage.asked) == 70 and len(ai.usage.needed) == 70
    assert ai.usage.rules_only == 300 + 0 - 70 or ai.usage.rules_only == len(ai.usage.seen) - 70


def test_ai_answers_are_used_but_validated_by_python():
    ai, _ = assistant()
    out, _ = pipeline([ambiguous_experience(1), ambiguous_function(1)], ai)
    by = {j.original_id: j for j in out.jobs}
    assert by["e1"].exp_source.startswith("gemini") and by["e1"].exp_min == "1"        # quote verified in text, parsed by our code
    assert by["f1"].function == "Operations"


def test_ai_cannot_invent_a_quote_that_is_not_in_the_posting():
    ai, prov = assistant(FakeProvider(answer=lambda p: {"quote": "0 years, freshers welcome"}))
    out, _ = pipeline([ambiguous_experience(1)], ai)
    j = out.jobs[0]
    assert prov.calls == 1 and j.category == "Experience Verification Required"                              # rules kept control


def test_second_run_is_served_from_the_persistent_cache():
    store = MemoryStore()
    ai1, p1 = assistant()
    pipeline(corpus(), ai1, store)
    assert p1.calls == 70 and len(store.ai_cache) >= 70                                  # cache written to the store
    ai2, p2 = assistant()                                                                # fresh process = empty memory
    pipeline(corpus(), ai2, store)
    assert p2.calls == 0
    # the 30 experience answers are already stored with their jobs (a stored job is not re-asked); the 40 function
    # questions about stored jobs are answered from the cache
    assert ai2.usage.cache_hits == 40


def test_changed_description_invalidates_only_that_entry():
    """Jobs the AI could not place are dropped (not stored), so they come back as 'new' every day: the cache must
    answer the unchanged ones and ask again only for the ones whose text changed."""
    store = MemoryStore()
    nolabel = FakeProvider(answer=lambda p: {"function": ""})            # AI finds no valid function -> job stays dropped
    ai1, p1 = assistant(nolabel)
    jobs = [ambiguous_function(i) for i in range(40)]
    out, _ = pipeline(jobs, ai1, store)
    assert p1.calls == 40 and out.jobs == []
    for j in jobs:
        if j.original_id in ("f0", "f1", "f2"):
            j.full_description += " Updated: now also hiring in the night shift."
    ai2, p2 = assistant(FakeProvider(answer=lambda p: {"function": ""}))
    pipeline(jobs, ai2, store)
    assert p2.calls == 3 and ai2.usage.cache_hits == 37


def test_prompt_text_change_is_a_cache_miss_at_unit_level():
    ai, prov = assistant()
    assert ai.suggest_function("Zebra Wrangler", "A" * 300) == "Operations"
    ai.suggest_function("Zebra Wrangler", "A" * 300)
    assert prov.calls == 1
    ai.suggest_function("Zebra Wrangler", "A" * 300 + " changed")
    assert prov.calls == 2


def test_all_providers_failing_does_not_stop_the_pipeline_and_failures_are_not_cached():
    store = MemoryStore()
    ai, prov = assistant(FakeProvider(fail_kind="transient"))
    out, _ = pipeline(corpus(), ai, store)
    assert len(out.jobs) > 100 and out.save_info                                         # run finished and saved
    assert store.ai_cache == []                                                          # nothing cached from failures
    assert any(r[3] == "AI" or r[2] == "AI" for r in store.stats)
    assert prov.calls < 70                                                               # router stopped hammering it
    ai2, p2 = assistant()
    pipeline(corpus(), ai2, store)
    assert p2.calls == 40                                                                # recovered provider is used again (function questions; stored jobs keep earlier experience results)


def test_permanent_provider_failure_stops_further_calls_quickly():
    ai, prov = assistant(FakeProvider(fail_kind="auth"))
    pipeline(corpus(), ai)
    assert prov.calls == 1


def test_per_run_ai_limit_respected():
    ai, prov = assistant(max_calls=10)
    pipeline(corpus(), ai)
    assert prov.calls == 10
    assert ai.usage.skipped_unavailable > 0


def test_no_ai_configured_is_rules_only_and_reports_it():
    out, store = pipeline(corpus(), None)
    assert out.ai_rows and out.ai_rows[0][2] == "AI" and out.ai_rows[0][4] == "off"
    assert all(j.category for j in out.jobs)


def test_statistics_rows_use_measured_counts():
    ai, prov = assistant()
    out, store = pipeline(corpus(), ai)
    summary = out.ai_rows[0]
    assert summary[2:5] == ["AI", "summary", "ok"] and len(summary) == 14
    note = summary[-1]
    assert f"total_ai_calls={prov.calls}" in note and "cache_hits=0" in note and "jobs_requiring_ai=70" in note
    prov_row = out.ai_rows[1]
    assert prov_row[3] == "groq" and f"calls={prov.calls}" in prov_row[-1]
    assert any(r[2] == "AI" for r in store.stats)                                        # written to Source Statistics


def test_dry_run_reads_cache_but_writes_nothing():
    store = MemoryStore()
    ai, prov = assistant()
    out, _ = pipeline(corpus(), ai, store, dry_run=True, dry_run_allow_ai=True)      # AI in a dry run is an explicit opt-in
    assert prov.calls == 70 and store.ai_cache == [] and store.master == {} and store.stats == []


# ---------------- cache unit tests
def test_cache_key_depends_on_content_and_kind():
    assert cache_key("exp", "a") != cache_key("exp", "b") != cache_key("fn", "b")
    assert cache_key("exp", "a") == cache_key("exp", "a")


def test_cache_ttl_cap_and_damaged_rows():
    rows = [{"Key": "old", "Kind": "fn", "Result (JSON)": '{"function": "Sales"}', "Last Used": "2026-01-01", "Hits": "1"},
            {"Key": "new", "Kind": "fn", "Result (JSON)": '{"function": "Sales"}', "Last Used": "2026-11-20", "Hits": "2"},
            {"Key": "bad", "Kind": "fn", "Result (JSON)": "{not json", "Last Used": "2026-11-20", "Hits": "x"},
            {"Kind": "no key"}]
    c = AICache(rows, today="2026-11-24", max_entries=10, ttl_days=45)
    assert len(c) == 1 + 1 and c.get("new") == (True, {"function": "Sales"})
    assert [r[0] for r in c.to_rows()] == ["new"]                                        # expired entry dropped on save
    capped = AICache(today="2026-11-24", max_entries=2)
    for k in "abc":
        capped.put(k, "fn", {"function": "x"})
    assert len(capped.to_rows()) == 2


def test_google_sheets_cache_tab_roundtrip_with_fake_worksheet():
    from integrations import google_sheets as G
    from integrations import sheet_schema as S

    class WS:
        row_count = 100

        def __init__(self): self.values = [S.CACHE_HEADERS]
        def get_all_values(self): return [list(r) for r in self.values]
        def batch_clear(self, rngs): self.values = self.values[:1]
        def add_rows(self, n): self.row_count += n
        def update(self, range_name, values, value_input_option): self.values += [list(v) for v in values]

    store = G.GoogleSheetsStore.__new__(G.GoogleSheetsStore)
    store.ws = {S.TAB_AI_CACHE: WS()}
    store._retry_call = lambda fn, *a, **k: fn(*a, **k)
    c = AICache(today="2026-11-24")
    c.put("k1", "fn", {"function": "Sales"})
    store.save_ai_cache(c.to_rows())
    store.save_ai_cache(c.to_rows())                                                     # idempotent: no duplicate rows
    back = AICache(store.load_ai_cache(), today="2026-11-25")
    assert back.get("k1") == (True, {"function": "Sales"}) and len(back) == 1
    assert S.TAB_AI_CACHE in S.TAB_ORDER
