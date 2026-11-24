from tests.helpers import mk, run


def score(desc, title="Business Development Executive", **kw):
    out, _ = run([mk("1", title=title, desc=desc, **kw)])
    return out.jobs[0].match_score, out.jobs[0].match_explanation


def test_score_is_bounded_and_explained():
    s, why = score("Experience: 1-2 years. CTC 12 LPA. Lead generation, CRM, client outreach, negotiation, excel, sql, reporting.")
    assert 0 <= s <= 100 and why


def test_better_pay_scores_higher_all_else_equal():
    low, _ = score("Experience: 1-2 years. CTC 8 LPA.")
    high, _ = score("Experience: 1-2 years. CTC 14 LPA.")
    assert high > low


def test_lower_experience_scores_higher_all_else_equal():
    junior, _ = score("Experience: 0-1 years. CTC 10 LPA.")
    senior, _ = score("Experience: 2-3 years. CTC 10 LPA.")
    assert junior > senior


def test_relevant_title_beats_generic_title():
    rel, _ = score("Experience: 1-2 years. CTC 10 LPA.", title="Marketing Executive")
    gen, _ = score("Experience: 1-2 years. CTC 10 LPA.", title="Executive")
    assert rel >= gen


def test_high_score_never_overrides_failed_rule():
    out, _ = run([mk("1", desc="5+ years experience. CTC 40 LPA. Lead generation, CRM, client outreach, negotiation, excel, sql.")])
    assert out.jobs[0].category == "Excluded"
