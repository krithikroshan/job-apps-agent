"""Chat-turn JSON-shape validation. The Gemini client is mocked out — these
tests never hit the network."""

import pytest
from google.genai import errors

from jobs_agent.profile import Profile
from jobs_agent.profile_chat.assistant import ChatError, chat_turn

PROFILE = Profile(
    name="", location="London",
    target_titles={"compliance analyst": 30},
    domain_terms={"aml": 10},
    title_blockers=["senior"],
    experience_blockers=["5+ years"],
)


class FakeResponse:
    def __init__(self, text):
        self.text = text


def mock_reply(monkeypatch, text):
    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse(text)

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("jobs_agent.profile_chat.assistant._client", lambda: FakeClient())


def test_a_plain_reply_with_no_proposal(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "What seniority should I avoid?", "proposal": null}')
    result = chat_turn(PROFILE, [], "I want compliance roles")
    assert result == {"reply": "What seniority should I avoid?", "proposal": None}


def test_a_valid_proposal(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "Added it.", '
               '"proposal": {"target_titles": {"compliance analyst": 30, "aml analyst": 28}}}')
    result = chat_turn(PROFILE, [], "also AML analyst")
    assert result["proposal"] == {
        "target_titles": {"compliance analyst": 30, "aml analyst": 28},
    }


def test_a_proposal_covering_all_four_fields(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "Here you go.", "proposal": '
               '{"target_titles": {"paralegal": 26}, "domain_terms": {"litigation": 5}, '
               '"title_blockers": ["director"], "experience_blockers": ["qualified solicitor"]}}')
    result = chat_turn(PROFILE, [], "set it all up")
    assert set(result["proposal"]) == {
        "target_titles", "domain_terms", "title_blockers", "experience_blockers",
    }


def test_history_is_carried_into_the_request(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            seen["contents"] = kwargs["contents"]
            return FakeResponse('{"reply": "ok", "proposal": null}')

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("jobs_agent.profile_chat.assistant._client", lambda: FakeClient())
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    chat_turn(PROFILE, history, "what now")

    assert seen["contents"][0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert seen["contents"][1] == {"role": "model", "parts": [{"text": "hello"}]}
    assert seen["contents"][-1] == {"role": "user", "parts": [{"text": "what now"}]}


def test_malformed_json_is_a_chat_error(monkeypatch):
    mock_reply(monkeypatch, "not json")
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_missing_reply_is_a_chat_error(monkeypatch):
    mock_reply(monkeypatch, '{"proposal": null}')
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_unknown_proposal_field_is_a_chat_error(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "ok", "proposal": {"location": "London"}}')
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_non_integer_weight_is_a_chat_error(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "ok", "proposal": {"target_titles": {"paralegal": "high"}}}')
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_non_list_blocker_is_a_chat_error(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "ok", "proposal": {"title_blockers": "director"}}')
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_empty_proposal_object_becomes_none(monkeypatch):
    mock_reply(monkeypatch, '{"reply": "Tell me more.", "proposal": {}}')
    result = chat_turn(PROFILE, [], "hello")
    assert result["proposal"] is None


def test_missing_api_key_is_a_chat_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def mock_overloaded(monkeypatch):
    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ServerError(503, {"error": {"message": "overloaded"}})

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("jobs_agent.profile_chat.assistant._client", lambda: FakeClient())


def test_a_gemini_503_falls_back_to_openrouter(monkeypatch):
    mock_overloaded(monkeypatch)
    monkeypatch.setattr(
        "jobs_agent.profile_chat.assistant.openrouter.chat_completion",
        lambda messages, **kwargs: '{"reply": "from openrouter", "proposal": null}',
    )
    result = chat_turn(PROFILE, [], "hello")
    assert result == {"reply": "from openrouter", "proposal": None}


def test_a_fenced_openrouter_reply_is_still_parsed(monkeypatch):
    mock_overloaded(monkeypatch)
    fenced = '```json\n{"reply": "from openrouter", "proposal": null}\n```'
    monkeypatch.setattr(
        "jobs_agent.profile_chat.assistant.openrouter.chat_completion",
        lambda messages, **kwargs: fenced,
    )
    result = chat_turn(PROFILE, [], "hello")
    assert result == {"reply": "from openrouter", "proposal": None}


def test_a_non_503_gemini_error_does_not_fall_back(monkeypatch):
    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(400, {"error": {"message": "bad request"}})

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr("jobs_agent.profile_chat.assistant._client", lambda: FakeClient())
    monkeypatch.setattr(
        "jobs_agent.profile_chat.assistant.openrouter.chat_completion",
        lambda messages, **kwargs: pytest.fail("should not have called OpenRouter"),
    )
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")


def test_openrouter_fallback_also_failing_is_a_chat_error(monkeypatch):
    mock_overloaded(monkeypatch)

    def raise_fallback_error(messages, **kwargs):
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    monkeypatch.setattr(
        "jobs_agent.profile_chat.assistant.openrouter.chat_completion", raise_fallback_error,
    )
    with pytest.raises(ChatError):
        chat_turn(PROFILE, [], "hello")
