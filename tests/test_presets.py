"""Career presets: each must be a complete, valid, saveable profile."""

import pytest
from conftest import make_posting

from jobs_agent.presets import PRESETS, get_preset
from jobs_agent.profile import Profile
from jobs_agent.scoring import score


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.id)
def test_every_preset_is_a_usable_profile(preset):
    profile = preset.profile
    assert profile.preset == preset.id
    assert profile.target_titles, "a preset with no titles drops everything"
    assert profile.locations
    assert Profile.from_json(profile.to_json()) == profile
    assert preset.label and preset.description


def test_preset_ids_are_unique():
    ids = [p.id for p in PRESETS]
    assert len(ids) == len(set(ids))


def test_get_preset():
    assert get_preset("accounting_graduate").label
    assert get_preset("no-such-preset") is None


def test_accounting_preset_keeps_graduate_accounting_roles():
    profile = get_preset("accounting_graduate").profile
    for title in ("Graduate Accountant", "Audit Associate - 2027 Intake",
                  "ACCA Trainee Accountant", "Trainee Tax Associate"):
        p = score(make_posting(title=title, description="Study support for ACA."), profile)
        assert p.score > 0, title


def test_accounting_preset_drops_qualified_and_senior_roles():
    profile = get_preset("accounting_graduate").profile
    assert score(make_posting(title="Senior Audit Associate"), profile).score == -1
    assert score(make_posting(title="Finance Manager"), profile).score == -1
    assert score(make_posting(title="Assistant Accountant",
                              description="You must be ACA qualified."), profile).score == -1


def test_accounting_preset_keeps_an_oddly_titled_graduate_scheme():
    profile = get_preset("accounting_graduate").profile
    p = score(make_posting(
        title="Assurance Graduate Programme 2027",
        description="Graduate scheme with full study support towards the ACA "
                    "qualification with the ICAEW. External audit of listed clients.",
    ), profile)
    assert p.score > 0


def test_law_preset_restores_the_original_target_titles():
    profile = get_preset("law_compliance").profile
    assert profile.target_titles["compliance analyst"] == 30
    assert profile.target_titles["paralegal"] == 26


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.id)
def test_every_preset_fits_one_fetch_without_dropping_titles(preset):
    from jobs_agent.pipeline import search_plan

    assert search_plan(preset.profile).warnings == []


def test_presets_narrow_adzuna_to_their_field():
    assert get_preset("accounting_graduate").profile.job_category == "accounting"
    assert get_preset("law_compliance").profile.job_category == "legal"


@pytest.mark.parametrize("description", [
    "A three-year training contract with full support to become ACA qualified.",
    "Over three years you'll study for the ACA and become a fully qualified Chartered Accountant.",
    "Graduates join in September; by the end you'll be newly qualified.",
])
def test_accounting_preset_keeps_graduate_schemes_that_describe_qualifying(description):
    profile = get_preset("accounting_graduate").profile
    p = score(make_posting(title="Audit Graduate Programme 2027", description=description), profile)
    assert p.score > 0, p.score_reasons


@pytest.mark.parametrize("description", [
    "You must be ACA qualified with experience of audit.",
    "Candidates will have at least 3 years' experience in practice.",
    "Minimum of three years' post-qualification experience required.",
    "You must be fully qualified (ACA/ACCA).",
])
def test_accounting_preset_still_drops_roles_that_require_qualification_or_experience(description):
    profile = get_preset("accounting_graduate").profile
    p = score(make_posting(title="Assistant Accountant", description=description), profile)
    assert p.score == -1


def test_paralegal_preset_searches_only_paralegal_roles_in_london():
    profile = get_preset("paralegal_london").profile
    assert profile.locations == ["London"]
    assert profile.job_category == "legal"
    assert all("paralegal" in t for t in profile.target_titles)
    assert score(make_posting(title="Legal Assistant"), profile).score == -1
    assert score(make_posting(title="Compliance Analyst"), profile).score == -1


@pytest.mark.parametrize("title,description", [
    ("Paralegal", "Ideal for a recent law graduate (LLB or GDL)."),
    ("Graduate Paralegal - Litigation", "No experience required; full training given."),
    ("Junior Paralegal", "At least 6 months' experience in a law firm is preferred."),
    ("Paralegal", "You will have 1 year's experience in a legal environment."),
    ("Document Review Paralegal (Contract)", "Six month contract; counts towards SQE QWE."),
])
def test_paralegal_preset_keeps_graduate_and_junior_paralegal_roles(title, description):
    profile = get_preset("paralegal_london").profile
    p = score(make_posting(title=title, description=description), profile)
    assert p.score > 0, p.score_reasons


@pytest.mark.parametrize("title,description", [
    ("Senior Paralegal", ""),
    ("Paralegal Team Leader", ""),
    ("Experienced Paralegal", ""),
    ("Paralegal", "You will have at least 2 years' experience as a paralegal."),
    ("Paralegal", "Minimum of three years' experience in commercial property."),
    ("Paralegal", "Suitable for a qualified solicitor seeking a career change."),
])
def test_paralegal_preset_drops_senior_and_experienced_roles(title, description):
    profile = get_preset("paralegal_london").profile
    assert score(make_posting(title=title, description=description), profile).score == -1
