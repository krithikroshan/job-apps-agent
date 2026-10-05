"""The queue with AI analysis joined in: the combined Match score, and the
filters that read the analysis."""

from conftest import make_posting

from jobs_agent.storage.store import AIFilters, match_score


def stage(store, **kw):
    p = make_posting(**kw)
    store.upsert([p])
    return p.key


def analyse(store, key, **result):
    base = {"seniority": "entry", "min_years": 0, "graduate_scheme": False,
            "qualifications": [], "study_support": False, "visa": "not_mentioned",
            "visa_evidence": None, "deadline": None, "fit": 50, "fit_reasons": [],
            "gaps": [], "red_flags": [], "summary": ""}
    store.save_analysis(key, "ctx", "done", {**base, **result}, "test-model", None)


def keys(rows):
    return [r["key"] for r in rows]


def test_match_score_blends_keywords_and_fit():
    assert match_score(80, None) == 100          # keyword only, capped and rescaled
    assert match_score(40, None) == 50
    assert match_score(40, 90) == round(0.4 * 50 + 0.6 * 90)
    assert match_score(-1, 90) == 0 or match_score(-1, 90) >= 0


def test_the_queue_orders_by_match_and_returns_it(store):
    low_kw_high_fit = stage(store, title="Audit Trainee", source_id="1", score=30,
                            description="aaa " * 30)
    high_kw_low_fit = stage(store, title="Tax Trainee", source_id="2", score=60,
                            description="bbb " * 30)
    analyse(store, low_kw_high_fit, fit=95)
    analyse(store, high_kw_low_fit, fit=10)
    rows = list(store.queue())
    assert keys(rows) == [low_kw_high_fit, high_kw_low_fit]
    assert rows[0]["match"] == match_score(30, 95)
    assert rows[0]["analysis"]["fit"] == 95
    assert rows[1]["analysis"]["fit"] == 10


def test_min_match_filters_on_the_combined_score(store):
    k = stage(store, score=40)
    analyse(store, k, fit=10)
    assert list(store.queue(min_score=40)) == []
    assert keys(store.queue(min_score=20)) == [k]


def _two(store):
    a = stage(store, title="Graduate Auditor", source_id="1", description="one " * 30)
    b = stage(store, title="Accounts Assistant", source_id="2", description="two " * 30)
    c = stage(store, title="Junior Accountant", source_id="3", description="three " * 30)
    return a, b, c   # c stays unanalysed


def test_visa_filters(store):
    a, b, c = _two(store)
    analyse(store, a, visa="offered", visa_evidence="We sponsor visas.")
    analyse(store, b, visa="not_offered", visa_evidence="No sponsorship.")
    assert keys(store.queue(ai=AIFilters(visa="offered"))) == [a]
    # Hiding "no sponsorship" keeps unanalysed and not-mentioned postings.
    assert set(keys(store.queue(ai=AIFilters(visa="hide_not_offered")))) == {a, c}


def test_level_and_years_filters(store):
    a, b, c = _two(store)
    analyse(store, a, seniority="graduate", min_years=0)
    analyse(store, b, seniority="mid", min_years=3)
    assert set(keys(store.queue(ai=AIFilters(level="entry")))) == {a, c}
    assert set(keys(store.queue(ai=AIFilters(max_years=1)))) == {a, c}


def test_scheme_support_and_red_flag_filters(store):
    a, b, c = _two(store)
    analyse(store, a, graduate_scheme=True, study_support=True)
    analyse(store, b, red_flags=["Commission only"])
    assert keys(store.queue(ai=AIFilters(graduate_scheme=True))) == [a]
    assert keys(store.queue(ai=AIFilters(study_support=True))) == [a]
    assert set(keys(store.queue(ai=AIFilters(hide_red_flags=True)))) == {a, c}


def test_analyses_are_per_user(store):
    from jobs_agent.storage import Store

    k = stage(store)
    analyse(store, k, fit=90)
    other = Store(user_id="00000000-0000-0000-0000-00000000beef", schema=store.schema)
    try:
        assert other.get_analysis(k) is None
    finally:
        other.close()


def test_deleting_a_posting_deletes_its_analysis(store):
    k = stage(store)
    analyse(store, k)
    assert store.delete_posting(k)
    assert store.get_analysis(k) is None


def test_a_json_null_red_flags_value_does_not_break_the_queue(store):
    k = stage(store)
    store.save_analysis(k, "ctx", "done", {"fit": 50, "red_flags": None}, "m", None)
    assert keys(store.queue(ai=AIFilters(hide_red_flags=True))) == [k]
