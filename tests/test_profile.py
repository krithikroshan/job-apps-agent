"""Scoring profile: the text format the UI edits, and the database round-trip."""

import pytest

from jobs_agent.profile import (
    DEFAULT_PROFILE,
    Profile,
    ProfileError,
    format_lines,
    format_locations,
    format_salary_bands,
    format_weights,
    load_profile,
    parse_int,
    parse_lines,
    parse_locations,
    parse_salary_bands,
    parse_weights,
    save_profile,
)


def test_weights_round_trip():
    weights = {"compliance analyst": 30, "paralegal": 26}
    assert parse_weights(format_weights(weights), what="x") == weights


def test_weights_accept_an_equals_sign_in_the_term():
    assert parse_weights("a = b = 5", what="x") == {"a = b": 5}


def test_weights_skip_blank_and_comment_lines():
    assert parse_weights("# a note\n\nparalegal = 26\n", what="x") == {"paralegal": 26}


def test_weights_report_the_offending_line():
    with pytest.raises(ProfileError, match="Titles, line 2"):
        parse_weights("paralegal = 26\nno weight here", what="Titles")

    with pytest.raises(ProfileError, match="line 1"):
        parse_weights("paralegal = lots", what="Titles")


def test_blocker_lines_keep_significant_trailing_spaces():
    assert parse_lines("vp \nlead \n") == ["vp ", "lead "]


def test_blocker_lines_round_trip():
    terms = ["head of", "vp ", "director"]
    assert parse_lines(format_lines(terms)) == terms


def test_terms_are_lowercased_to_match_the_scorer():
    assert parse_weights("Compliance Analyst = 30", what="x") == {"compliance analyst": 30}
    assert parse_lines("Head Of") == ["head of"]


def test_json_round_trip():
    assert Profile.from_json(DEFAULT_PROFILE.to_json()) == DEFAULT_PROFILE


def test_load_returns_the_default_when_nothing_is_stored(store):
    assert load_profile(store) == DEFAULT_PROFILE


def test_save_then_load(store):
    custom = Profile(name="Jane", locations=["Leeds"], target_titles={"clerk": 10})
    save_profile(store, custom)
    assert load_profile(store) == custom


def test_a_corrupt_row_falls_back_to_the_default(store):
    from jobs_agent.storage import DOC_SCORING_PROFILE

    store.set_document(DOC_SCORING_PROFILE, "{not json")
    assert load_profile(store) == DEFAULT_PROFILE


# -- search area ------------------------------------------------------------

def test_profiles_saved_before_locations_existed_still_load():
    """Stored profiles carry a single ``location`` string; it becomes the
    one-entry ``locations`` list rather than failing as an unknown field."""
    legacy = '{"name": "Jane", "location": "Leeds", "target_titles": {"clerk": 10}}'
    p = Profile.from_json(legacy)
    assert p.locations == ["Leeds"]
    assert p.target_titles == {"clerk": 10}


def test_locations_split_on_commas_and_newlines_and_dedupe():
    assert parse_locations("London, Manchester\nlondon\n  ") == ["London", "Manchester"]


def test_locations_round_trip():
    assert parse_locations(format_locations(["London", "Leeds"])) == ["London", "Leeds"]


def test_locations_cannot_be_empty():
    with pytest.raises(ProfileError, match="at least one"):
        parse_locations(" , ")


def test_too_many_locations_are_refused():
    with pytest.raises(ProfileError, match="at most"):
        parse_locations(", ".join(f"Town {i}" for i in range(20)))


def test_parse_int_enforces_its_range():
    assert parse_int("15", what="Radius", lo=0, hi=100) == 15
    with pytest.raises(ProfileError, match="Radius"):
        parse_int("500", what="Radius", lo=0, hi=100)
    with pytest.raises(ProfileError, match="Radius"):
        parse_int("far", what="Radius", lo=0, hi=100)


# -- salary bands -----------------------------------------------------------

def test_salary_bands_accept_pounds_commas_and_k():
    bands = parse_salary_bands("£30,000 = 10\n25k = 5\n0 = -8")
    assert bands == [[30000, 10], [25000, 5], [0, -8]]


def test_salary_bands_are_sorted_highest_first():
    assert parse_salary_bands("0 = -8\n30000 = 10") == [[30000, 10], [0, -8]]


def test_salary_bands_round_trip():
    bands = [[33400, 10], [28000, 5], [0, -8]]
    assert parse_salary_bands(format_salary_bands(bands)) == bands


def test_a_bad_salary_band_names_the_line():
    with pytest.raises(ProfileError, match="Salary bands, line 2"):
        parse_salary_bands("30000 = 10\nlots = 5")


def test_new_fields_survive_a_json_round_trip():
    p = Profile(name="", locations=["Leeds", "UK"], radius_miles=30,
                salary_bands=[[25000, 5]], contract_bonus=0,
                domain_only_threshold=12, preset="accounting_graduate")
    assert Profile.from_json(p.to_json()) == p


@pytest.mark.parametrize("text", [",,, = 5", "9" * 400 + " = 5", "2000000 = 5"])
def test_malformed_or_absurd_salary_amounts_are_profile_errors(text):
    with pytest.raises(ProfileError, match="Salary bands, line 1"):
        parse_salary_bands(text)


def test_salary_band_points_are_bounded():
    with pytest.raises(ProfileError, match="Salary bands, line 1"):
        parse_salary_bands("30k = 99999999999")


def test_a_stored_profile_with_no_locations_gets_the_default():
    p = Profile.from_json('{"name": "", "locations": []}')
    assert p.locations == ["London"]


# -- job category -------------------------------------------------------------

def test_job_category_defaults_to_any():
    assert DEFAULT_PROFILE.job_category == ""


def test_a_profile_saved_before_job_category_still_loads():
    import json

    data = json.loads(DEFAULT_PROFILE.to_json())
    del data["job_category"]
    assert Profile.from_json(json.dumps(data)).job_category == ""


def test_parse_job_category_accepts_known_slugs_and_blank():
    from jobs_agent.profile import JOB_CATEGORIES, parse_job_category

    assert parse_job_category("") == ""
    assert parse_job_category(" Legal ") == "legal"
    for slug in JOB_CATEGORIES:
        assert parse_job_category(slug) == slug


def test_parse_job_category_rejects_an_unknown_slug():
    from jobs_agent.profile import parse_job_category

    with pytest.raises(ProfileError, match="Job category"):
        parse_job_category("astronaut")
