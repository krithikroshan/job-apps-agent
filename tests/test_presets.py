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
