"""API endpoints, called directly — no socket involved.

The approval gate is the point of this file: a posting must not reach
"submitted" without passing through "approved", and that has to be enforced
by the server, not just hidden in the UI.
"""

import pytest
from conftest import make_posting

from jobs_agent.profile import DEFAULT_PROFILE, load_profile
from jobs_agent.web import api


def req(**payload):
    return api.Request(payload=payload)


@pytest.fixture
def staged(store):
    """A posting in the store, with its application row. Returns its key."""
    p = make_posting()
    store.upsert([p])
    return p.key


# -- the approval gate ----------------------------------------------------

def test_cannot_approve_without_a_letter(store, staged):
    res = api.post_status(store, req(key=staged, status="approved"))
    assert res.status == 400
    assert "draft a letter" in res.body["error"]
    assert store.get_application(staged)["status"] == "new"


def test_cannot_submit_without_approving_first(store, staged):
    store.set_letter(staged, "Dear hiring manager,")
    store.set_status(staged, "drafted")

    res = api.post_status(store, req(key=staged, status="submitted"))
    assert res.status == 400
    assert "Approve the application" in res.body["error"]
    assert store.get_application(staged)["status"] == "drafted"


def test_the_full_path_to_submitted_works(store, staged):
    store.set_letter(staged, "Dear hiring manager,")
    assert api.post_status(store, req(key=staged, status="drafted")).status == 200
    assert api.post_status(store, req(key=staged, status="approved")).status == 200
    assert api.post_status(store, req(key=staged, status="submitted")).status == 200
    assert store.get_application(staged)["status"] == "submitted"


def test_unknown_status_is_rejected(store, staged):
    assert api.post_status(store, req(key=staged, status="hired")).status == 400


def test_unknown_posting_is_a_404(store):
    res = api.post_status(store, req(key="nope", status="shortlisted"))
    assert res.status == 404


# -- delete -----------------------------------------------------------------

def test_delete_removes_the_posting(store, staged):
    res = api.post_delete(store, req(key=staged))
    assert res.status == 200
    assert store.get_posting(staged) is None
    assert store.get_application(staged) is None


def test_delete_unknown_posting_is_a_404(store):
    res = api.post_delete(store, req(key="nope"))
    assert res.status == 404


def test_delete_missing_key_is_rejected(store):
    res = api.post_delete(store, req())
    assert res.status == 400


# -- drafting guards ------------------------------------------------------

def test_draft_requires_a_cv_and_a_template(store, staged):
    res = api.post_draft(store, req(key=staged))
    assert res.status == 400
    assert "Profile page" in res.body["error"]


def test_redraft_requires_an_existing_draft(store, staged):
    store.set_document("cv", "Jane Smith, LLB")
    res = api.post_redraft(store, req(key=staged, feedback="make it shorter"))
    assert res.status == 400
    assert "before redrafting" in res.body["error"]


def test_redraft_requires_feedback(store, staged):
    assert api.post_redraft(store, req(key=staged)).status == 400


# -- CV upload ------------------------------------------------------------

def test_cv_upload_rejects_other_extensions(store):
    res = api.post_cv(store, req(filename="cv.txt", data_b64=""))
    assert res.status == 400


def test_cv_upload_rejects_undecodable_base64(store):
    res = api.post_cv(store, req(filename="cv.pdf", data_b64="not base64!!"))
    assert res.status == 400


# -- documents ------------------------------------------------------------

def test_only_editable_documents_are_writable(store):
    api.post_documents(store, req(candidate_name="Jane", cv="malicious override"))
    assert store.get_document("candidate_name") == "Jane"
    assert store.get_document("cv") == ""


# -- scoring profile ------------------------------------------------------

def test_get_profile_returns_editable_text(store):
    body = api.get_profile(store, api.Request()).body
    assert body["target_titles"] == ""
    assert "head of" in body["title_blockers"]


def test_saving_a_profile_persists_it(store):
    res = api.post_profile(store, req(
        target_titles="clerk = 10",
        domain_terms="probate = 5",
        title_blockers="senior",
        experience_blockers="",
    ))
    assert res.status == 200
    saved = load_profile(store)
    assert saved.target_titles == {"clerk": 10}
    assert saved.experience_blockers == []
    # name/location aren't edited here, so they carry over
    assert saved.name == DEFAULT_PROFILE.name


def test_a_bad_line_saves_nothing(store):
    res = api.post_profile(store, req(
        target_titles="clerk = 10",
        domain_terms="probate = five",
    ))
    assert res.status == 400
    assert "Domain terms, line 1" in res.body["error"]
    assert load_profile(store) == DEFAULT_PROFILE


def test_an_empty_title_list_is_refused(store):
    res = api.post_profile(store, req(target_titles="  \n# only a comment\n"))
    assert res.status == 400
    assert "drops everything" in res.body["error"]


def test_reset_restores_the_defaults(store):
    api.post_profile(store, req(target_titles="clerk = 10"))
    assert load_profile(store) != DEFAULT_PROFILE
    assert api.post_profile_reset(store, api.Request()).status == 200
    assert load_profile(store) == DEFAULT_PROFILE


# -- scoring profile chat --------------------------------------------------
#
# chat_turn talks to an AI provider; every test here replaces it with a stub so
# nothing hits the network. The endpoint itself never saves anything — that
# only happens if the caller then POSTs the returned "preview" to
# /api/profile, same as it would a manual edit.

def test_chat_requires_a_message(store):
    res = api.post_profile_chat(store, req(message=""))
    assert res.status == 400


def test_a_turn_with_no_proposal_returns_the_reply_only(store, monkeypatch):
    monkeypatch.setattr("jobs_agent.web.api.chat_turn",
                        lambda client, profile, history, message: {
                            "reply": "What seniority should I exclude?",
                            "proposal": None,
                        })
    res = api.post_profile_chat(store, req(message="I want compliance roles"))
    assert res.status == 200
    assert res.body == {
        "reply": "What seniority should I exclude?",
        "proposal": None,
        "preview": None,
    }


def test_a_valid_proposal_returns_a_merged_preview(store, monkeypatch):
    monkeypatch.setattr("jobs_agent.web.api.chat_turn",
                        lambda client, profile, history, message: {
                            "reply": "Added it.",
                            "proposal": {"target_titles": {"aml analyst": 28}},
                        })
    res = api.post_profile_chat(store, req(message="also AML analyst"))
    assert res.status == 200
    assert res.body["proposal"] == {"target_titles": {"aml analyst": 28}}
    assert res.body["preview"]["target_titles"] == "aml analyst = 28"
    # nothing is saved by the chat endpoint itself
    assert load_profile(store) == DEFAULT_PROFILE


def test_an_invalid_proposal_degrades_to_a_reply(store, monkeypatch):
    monkeypatch.setattr("jobs_agent.web.api.chat_turn",
                        lambda client, profile, history, message: {
                            "reply": "Here you go.",
                            "proposal": {"target_titles": "not a mapping"},
                        })
    res = api.post_profile_chat(store, req(message="hello"))
    assert res.status == 200
    assert res.body["proposal"] is None
    assert res.body["preview"] is None
    assert "couldn't apply that" in res.body["reply"]


def test_pending_proposal_is_merged_into_the_draft_passed_to_the_model(store, monkeypatch):
    seen = {}

    def fake_chat_turn(client, profile, history, message):
        seen["draft"] = profile
        return {"reply": "ok", "proposal": None}

    monkeypatch.setattr("jobs_agent.web.api.chat_turn", fake_chat_turn)
    api.post_profile_chat(store, req(
        message="make it 30 instead",
        pending_proposal={"target_titles": {"aml analyst": 28}},
    ))
    assert seen["draft"].target_titles == {"aml analyst": 28}
    # the base profile (still saved) is untouched
    assert load_profile(store) == DEFAULT_PROFILE


def test_a_chat_error_is_returned_as_a_400(store, monkeypatch):
    from jobs_agent.profile_chat import ChatError

    def raise_it(client, profile, history, message):
        raise ChatError("GEMINI_API_KEY is not set")

    monkeypatch.setattr("jobs_agent.web.api.chat_turn", raise_it)
    res = api.post_profile_chat(store, req(message="hello"))
    assert res.status == 400
    assert "GEMINI_API_KEY" in res.body["error"]



# -- presets and search settings ---------------------------------------------

def test_presets_are_listed_as_editable_text(store):
    body = api.get_presets(store, api.Request()).body
    ids = [p["id"] for p in body]
    assert "accounting_graduate" in ids and "law_compliance" in ids
    accounting = next(p for p in body if p["id"] == "accounting_graduate")
    assert "graduate accountant" in accounting["profile"]["target_titles"]
    assert accounting["profile"]["preset"] == "accounting_graduate"


def test_get_profile_includes_the_search_settings(store):
    body = api.get_profile(store, api.Request()).body
    assert body["locations"] == "London"
    assert body["radius_miles"] == "15"
    assert "=" in body["salary_bands"]


def test_saving_search_settings_persists_them(store):
    res = api.post_profile(store, req(
        target_titles="graduate accountant = 30",
        locations="London, Manchester",
        radius_miles="25",
        salary_bands="30000 = 10\n0 = -5",
        contract_bonus="0",
        domain_only_threshold="12",
        preset="accounting_graduate",
    ))
    assert res.status == 200, res.body
    saved = load_profile(store)
    assert saved.locations == ["London", "Manchester"]
    assert saved.radius_miles == 25
    assert saved.salary_bands == [[30000, 10], [0, -5]]
    assert saved.contract_bonus == 0
    assert saved.domain_only_threshold == 12
    assert saved.preset == "accounting_graduate"


@pytest.mark.parametrize("field,value", [
    ("radius_miles", "500"),
    ("radius_miles", "far"),
    ("locations", ""),
    ("domain_only_threshold", "99"),
])
def test_bad_search_settings_save_nothing(store, field, value):
    res = api.post_profile(store, req(target_titles="clerk = 10", **{field: value}))
    assert res.status == 400
    assert load_profile(store) == DEFAULT_PROFILE


@pytest.mark.parametrize("field,value", [
    ("locations", ["London"]),
    ("salary_bands", ["30000 = 5"]),
    ("radius_miles", None),
])
def test_non_string_search_settings_are_a_400(store, field, value):
    res = api.post_profile(store, req(target_titles="clerk = 10", **{field: value}))
    assert res.status == 400


def test_an_unknown_preset_from_an_old_save_is_dropped_not_fatal(store):
    """``preset`` is display-only; a removed preset id must not make the
    profile unsaveable."""
    res = api.post_profile(store, req(target_titles="clerk = 10", preset="retired-preset"))
    assert res.status == 200
    assert load_profile(store).preset == ""


def test_job_category_round_trips_through_the_profile_text(store):
    assert api.get_profile(store, api.Request()).body["job_category"] == ""
    res = api.post_profile(store, req(target_titles="clerk = 10", job_category="legal"))
    assert res.status == 200, res.body
    assert load_profile(store).job_category == "legal"
    assert api.get_profile(store, api.Request()).body["job_category"] == "legal"


@pytest.mark.parametrize("value", ["astronaut", ["legal"]])
def test_a_bad_job_category_saves_nothing(store, value):
    res = api.post_profile(store, req(target_titles="clerk = 10", job_category=value))
    assert res.status == 400
    assert load_profile(store) == DEFAULT_PROFILE
