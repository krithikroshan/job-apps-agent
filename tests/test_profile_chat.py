"""Chat-turn JSON-shape validation. The AI client is a stub — these tests
never hit the network. Provider fallback is covered in test_llm_client.py."""

import pytest

from jobs_agent.llm.base import LLMError
from jobs_agent.profile import Profile
from jobs_agent.profile_chat.assistant import ChatError, chat_turn

PROFILE = Profile(
    name="",
    target_titles={"compliance analyst": 30},
    domain_terms={"aml": 10},
    title_blockers=["senior"],
    experience_blockers=["5+ years"],
)


class FakeClient:
    """Replies with ``reply`` (or raises it), recording each call."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def turn(reply, history=(), message="hello"):
    return chat_turn(FakeClient(reply), PROFILE, list(history), message)


def test_a_plain_reply_with_no_proposal():
    result = turn('{"reply": "What seniority should I avoid?", "proposal": null}',
                  message="I want compliance roles")
    assert result == {"reply": "What seniority should I avoid?", "proposal": None}


def test_a_valid_proposal():
    result = turn('{"reply": "Added it.", '
                  '"proposal": {"target_titles": {"compliance analyst": 30, "aml analyst": 28}}}')
    assert result["proposal"] == {
        "target_titles": {"compliance analyst": 30, "aml analyst": 28},
    }


def test_a_proposal_covering_all_four_fields():
    result = turn('{"reply": "Here you go.", "proposal": '
                  '{"target_titles": {"paralegal": 26}, "domain_terms": {"litigation": 5}, '
                  '"title_blockers": ["director"], "experience_blockers": ["qualified solicitor"]}}')
    assert set(result["proposal"]) == {
        "target_titles", "domain_terms", "title_blockers", "experience_blockers",
    }


def test_history_is_carried_into_the_request_as_json_mode():
    client = FakeClient('{"reply": "ok", "proposal": null}')
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
               {"role": "user", "content": "  "}, "junk"]
    chat_turn(client, PROFILE, history, "what now")

    call = client.calls[0]
    assert [(m.role, m.content) for m in call["messages"]] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "what now")]
    assert call["json_mode"] is True
    assert "compliance analyst" in call["system"]


def test_a_fenced_reply_is_still_parsed():
    result = turn('```json\n{"reply": "fenced", "proposal": null}\n```')
    assert result == {"reply": "fenced", "proposal": None}


@pytest.mark.parametrize("reply", [
    "not json",
    '{"proposal": null}',
    '{"reply": "ok", "proposal": {"location": "London"}}',
    '{"reply": "ok", "proposal": {"target_titles": {"paralegal": "high"}}}',
    '{"reply": "ok", "proposal": {"title_blockers": "director"}}',
])
def test_malformed_replies_are_chat_errors(reply):
    with pytest.raises(ChatError):
        turn(reply)


def test_empty_proposal_object_becomes_none():
    assert turn('{"reply": "Tell me more.", "proposal": {}}')["proposal"] is None


def test_a_provider_failure_is_a_chat_error():
    with pytest.raises(ChatError, match="every provider failed"):
        turn(LLMError("every provider failed"))


def test_json_with_chatter_around_it_is_still_parsed():
    result = turn('Sure! Here it is:\n{"reply": "ok", "proposal": null}\nHope that helps.')
    assert result == {"reply": "ok", "proposal": None}
