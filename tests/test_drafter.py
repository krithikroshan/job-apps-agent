"""Letter drafting against a stub AI client."""

import pytest

from jobs_agent.letters import DraftError, draft_letter, redraft_letter
from jobs_agent.llm.base import LLMError

POSTING = dict(title="Graduate Accountant", employer="Acme LLP", location="London",
               description="Audit work with ACA study support.")


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_draft_sends_cv_template_and_posting():
    client = FakeClient("Dear Acme,")
    assert draft_letter(client, **POSTING, cv="MY CV", template="MY VOICE") == "Dear Acme,"
    prompt = client.calls[0]["messages"][0].content
    assert "MY CV" in prompt and "MY VOICE" in prompt and "Acme LLP" in prompt
    assert client.calls[0]["json_mode"] is False


def test_redraft_sends_the_previous_letter_and_feedback():
    client = FakeClient("Dear Acme, (shorter)")
    redraft_letter(client, **POSTING, cv="MY CV", previous_letter="OLD", feedback="shorter")
    prompt = client.calls[0]["messages"][0].content
    assert "OLD" in prompt and "shorter" in prompt


def test_a_provider_failure_is_a_draft_error():
    with pytest.raises(DraftError, match="drafting failed: nope"):
        draft_letter(FakeClient(LLMError("nope")), **POSTING, cv="c", template="t")
