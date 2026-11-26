"""AI function suggestions are only believed when the posting text supports them. Offline: fake providers only."""
from config.profile import FUNCTIONS
from integrations.ai_router import AIRouter
from processing.ai_assist import AIAssistant
from processing.scoring import function_evidence
from tests.ai_fakes import FakeProvider
from tests.test_ai_usage_reduction import LONG, assistant, mk, pipeline, NAMES


def job(i, body, title="Zebra Wrangler"):
    return mk(f"v{i}", f"{title} {i}", NAMES[100 + i], "Pune, India", f"Experience: 1-2 years. {body} {LONG}")


def run(body, answer="Operations"):
    ai, prov = assistant(FakeProvider(answer=lambda p: {"function": answer}))
    out, _ = pipeline([job(1, body)], ai)
    return out, ai, prov


def test_evidence_counts_distinct_terms_and_ignores_unknown_labels():
    assert len(function_evidence("Operations", "x", "operations and process improvement")) == 2
    assert function_evidence("Not A Function", "x", "operations") == []
    assert function_evidence("Operations", "x", "nothing relevant here") == []


def test_supported_suggestion_is_accepted():
    out, ai, _ = run("Daily operations and process improvement work.")
    assert out.jobs and out.jobs[0].function == "Operations" and len(ai.usage.fn_accepted) == 1


def test_unsupported_suggestion_is_rejected_and_rules_decide():
    out, ai, prov = run("We like zebras and stripes.")
    assert prov.calls >= 1 and not out.jobs                      # irrelevant by the rules; the AI label did not rescue it
    assert len(ai.usage.fn_rejected) == 1 and not ai.usage.fn_accepted


def test_weak_suggestion_is_marked_unverified_with_lowest_score_and_a_flag():
    out, ai, _ = run("Some operations exposure.")
    j = out.jobs[0]
    assert j.function == "Operations (unverified)" and len(ai.usage.fn_uncertain) == 1
    assert "unverified" in j.verification_flags.lower()


def test_unsupported_never_scores_higher_than_supported():
    strong, _, _ = run("Daily operations and process improvement work.")
    weak, _, _ = run("Some operations exposure.")
    assert weak.jobs[0].match_score < strong.jobs[0].match_score


def test_all_providers_failing_falls_back_to_python_rules():
    ai, _ = assistant(FakeProvider(fail_kind="permanent"))
    out, _ = pipeline([job(2, "x", title="Operations Executive")], ai)
    assert out.jobs and out.jobs[0].function == "Operations"     # title rule works with no AI at all
