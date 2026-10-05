"""What the model says about a posting is untrusted data: it's checked,
clamped, and trimmed before anything is stored or shown."""

import json

import pytest

from jobs_agent.analysis.schema import AnalysisError, parse_batch, validate

GOOD = {
    "id": 1,
    "seniority": "graduate",
    "min_years": 0,
    "graduate_scheme": True,
    "qualifications": ["2:1 degree", "ACA in progress"],
    "study_support": True,
    "visa": "offered",
    "visa_evidence": "We can sponsor Skilled Worker visas.",
    "deadline": "2026-11-30",
    "fit": 82,
    "fit_reasons": ["Audit internship matches the role"],
    "gaps": ["No Excel certification"],
    "red_flags": [],
    "summary": "Graduate audit scheme with ACA support in London.",
}


def test_a_good_analysis_passes_through():
    out = validate(GOOD)
    assert out["fit"] == 82
    assert out["visa"] == "offered"
    assert out["deadline"] == "2026-11-30"
    assert "id" not in out


@pytest.mark.parametrize("field,bad,expected", [
    ("fit", 140, 100),
    ("fit", -5, 0),
    ("fit", "high", None),
    ("min_years", -2, None),
    ("min_years", 99, None),
    ("seniority", "wizard", "unknown"),
    ("visa", "maybe", "not_mentioned"),
    ("deadline", "next Friday", None),
    ("deadline", "2026-02-31", None),
    ("graduate_scheme", "yes", False),
])
def test_out_of_range_or_wrong_type_fields_are_corrected(field, bad, expected):
    assert validate({**GOOD, field: bad})[field] == expected


def test_lists_are_capped_and_strings_trimmed():
    out = validate({**GOOD, "red_flags": ["x" * 500] + ["y"] * 10, "fit_reasons": "not a list"})
    assert len(out["red_flags"]) == 3
    assert len(out["red_flags"][0]) <= 160
    assert out["fit_reasons"] == []


def test_visa_evidence_is_dropped_when_nothing_was_mentioned():
    out = validate({**GOOD, "visa": "not_mentioned", "visa_evidence": "made up"})
    assert out["visa_evidence"] is None


def test_a_non_object_is_an_error():
    with pytest.raises(AnalysisError):
        validate(["not", "an", "object"])


def test_parse_batch_maps_ids_back_and_skips_unknown_ones():
    text = ('```json\n{"analyses": [' + ",".join([
        '{"id": 1, "fit": 70, "seniority": "entry"}',
        '{"id": 2, "fit": 40}',
        '{"id": 9, "fit": 99}',
        '"junk"',
    ]) + ']}\n```')
    out = parse_batch(text, {1: "key-a", 2: "key-b"})
    assert set(out) == {"key-a", "key-b"}
    assert out["key-a"]["seniority"] == "entry"


def test_parse_batch_rejects_unparseable_replies():
    with pytest.raises(AnalysisError):
        parse_batch("sorry, I can't help with that", {1: "k"})


def test_parse_batch_accepts_ids_written_as_strings():
    out = parse_batch('{"analyses": [{"id": "1", "fit": 70}, {"id": "two", "fit": 1}]}',
                      {1: "key-a", 2: "key-b"})
    assert set(out) == {"key-a"}


def test_parse_batch_accepts_a_bare_list():
    out = parse_batch('[{"id": 1, "fit": 70}]', {1: "key-a"})
    assert set(out) == {"key-a"}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e999"])
def test_unrepresentable_numbers_drop_the_entry_not_the_batch(bad):
    out = parse_batch('{"analyses": [{"id": 1, "fit": %s}, {"id": 2, "fit": 40}]}' % bad,
                      {1: "key-a", 2: "key-b"})
    assert out["key-b"]["fit"] == 40
    assert out.get("key-a", {}).get("fit") in (None, 0, 100)


@pytest.mark.parametrize("given", ["posting-2-f43f195455ca", "POSTING 2", "2", 2])
def test_ids_echoed_back_in_the_prompts_own_forms_are_understood(given):
    out = parse_batch(json.dumps({"analyses": [{"id": given, "fit": 70}]}), {2: "key-b"})
    assert set(out) == {"key-b"}
