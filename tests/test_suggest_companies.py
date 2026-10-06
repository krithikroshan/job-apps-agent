"""AI company suggestions: the model only proposes; every name and website
is checked before the page sees it."""

import json

import pytest

from jobs_agent.llm import LLMError
from jobs_agent.presets import get_preset
from jobs_agent.profile import DEFAULT_PROFILE
from jobs_agent.suggest import SuggestError, suggest_companies


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def reply(*companies):
    return json.dumps({"companies": list(companies)})


PROFILE = get_preset("paralegal_london").profile


def test_the_prompt_carries_the_profile_cv_and_watched_firms():
    client = FakeClient(reply())
    suggest_companies(client, PROFILE, cv="LLB Law, 2:1. Six months at a high-street firm.",
                      watched=["Irwin Mitchell"])
    prompt = client.calls[0]["messages"][0].content
    assert "graduate paralegal" in prompt
    assert "London" in prompt
    assert "LLB Law" in prompt
    assert "Irwin Mitchell" in prompt
    assert client.calls[0]["json_mode"] is True


def test_suggestions_come_back_cleaned():
    client = FakeClient(reply(
        {"name": "  Clifford   Chance ", "why": "Large paralegal intake.",
         "website": "https://www.cliffordchance.com/home.html"},
        {"name": "Kennedys", "why": "Insurance litigation, hires junior paralegals.",
         "website": "kennedyslaw.com"},
    ))
    out = suggest_companies(client, PROFILE)
    assert out == [
        {"name": "Clifford Chance", "why": "Large paralegal intake.",
         "website": "www.cliffordchance.com"},
        {"name": "Kennedys", "why": "Insurance litigation, hires junior paralegals.",
         "website": "kennedyslaw.com"},
    ]


def test_firms_already_watched_and_duplicates_are_dropped():
    client = FakeClient(reply(
        {"name": "Irwin Mitchell", "why": "x", "website": "irwinmitchell.com"},
        {"name": "DWF", "why": "x", "website": "dwfgroup.com"},
        {"name": "dwf", "why": "again", "website": "dwfgroup.com"},
    ))
    out = suggest_companies(client, PROFILE, watched=["irwin mitchell"])
    assert [s["name"] for s in out] == ["DWF"]


@pytest.mark.parametrize("website", [
    "javascript:alert(1)", "not a domain", "localhost", "http://10.0.0.1/", "a" * 300 + ".com",
    42, None,
])
def test_a_bad_website_is_blanked_not_trusted(website):
    client = FakeClient(reply({"name": "Firm", "why": "x", "website": website}))
    assert suggest_companies(client, PROFILE)[0]["website"] == ""


def test_entries_without_a_usable_name_are_skipped_and_the_list_is_capped():
    entries = [{"name": "", "why": "x"}, {"why": "no name"}, "just a string",
               {"name": "x" * 200, "why": "too long"}]
    entries += [{"name": f"Firm {i}", "why": "y" * 500, "website": f"firm{i}.co.uk"}
                for i in range(20)]
    out = suggest_companies(FakeClient(reply(*entries)), PROFILE)
    assert len(out) == 10
    assert out[0]["name"] == "Firm 0"
    assert len(out[0]["why"]) <= 200


def test_a_reply_that_is_not_json_is_a_friendly_error():
    with pytest.raises(SuggestError, match="Couldn't"):
        suggest_companies(FakeClient("Sure! Here are some firms: ..."), PROFILE)


def test_a_provider_failure_is_a_friendly_error():
    with pytest.raises(SuggestError, match="no key"):
        suggest_companies(FakeClient(LLMError("no key")), PROFILE)


def test_a_profile_with_nothing_to_go_on_asks_for_one_first():
    with pytest.raises(SuggestError, match="target titles"):
        suggest_companies(FakeClient(reply()), DEFAULT_PROFILE, cv="")
