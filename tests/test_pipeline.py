"""The search plan: which keywords x locations a fetch actually runs."""

from dataclasses import replace

from jobs_agent.config import KEYWORDS
from jobs_agent.pipeline import MAX_SEARCHES, search_plan
from jobs_agent.profile import DEFAULT_PROFILE


def test_keywords_are_the_target_titles_strongest_first():
    profile = replace(DEFAULT_PROFILE, target_titles={"clerk": 5, "paralegal": 26})
    plan = search_plan(profile)
    assert plan.keywords == ["paralegal", "clerk"]
    assert plan.locations == DEFAULT_PROFILE.locations
    assert plan.warnings == []


def test_falls_back_to_the_builtin_keywords_with_no_titles():
    assert search_plan(replace(DEFAULT_PROFILE, target_titles={})).keywords == KEYWORDS


def test_the_plan_is_capped_and_says_so():
    titles = {f"title {i}": 100 - i for i in range(30)}
    profile = replace(DEFAULT_PROFILE, target_titles=titles,
                      locations=["London", "Leeds", "Bristol"])
    plan = search_plan(profile)
    assert len(plan.keywords) * len(plan.locations) <= MAX_SEARCHES
    assert plan.keywords[0] == "title 0"   # the strongest titles survive the cut
    assert plan.warnings and "searched" in plan.warnings[0]


def test_explicit_keywords_override_the_profile():
    assert search_plan(DEFAULT_PROFILE, keywords=["x"]).keywords == ["x"]


def test_fetch_passes_the_profiles_job_category_to_the_boards(store, monkeypatch):
    import asyncio

    from jobs_agent import pipeline
    from jobs_agent.profile import save_profile

    save_profile(store, replace(DEFAULT_PROFILE, target_titles={"auditor": 20},
                                job_category="accounting"))
    seen = {}

    def fake_build_sources(job_category=""):
        seen["category"] = job_category
        return [], []

    async def no_jobs(*args, **kwargs):
        return []

    monkeypatch.setattr(pipeline, "build_sources", fake_build_sources)
    monkeypatch.setattr(pipeline, "gather_all", no_jobs)
    asyncio.run(pipeline.fetch_and_store(store))
    assert seen["category"] == "accounting"
