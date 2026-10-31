"""Legacy POST /api/v1/ai/reply: owner-only, and never returns or stores the raw prompt.

The prompt carries the owner's published Persona, retrieved knowledge and the
conversation, so these tests first prove that private content really is in the
prompt, then prove it never leaves the endpoint or reaches ai_runs. Offline:
in-memory Qdrant, no provider or network calls.
"""

import json

import pytest

from app import db
from app.ai_runtime import REPLY_PLACEHOLDER, ai_runtime
from app.models import AiRun, Conversation, Message, User
from test_personal_ai_runtime_e2e import (  # noqa: F401  (offline, qdrant_store are fixtures)
    _import_owner, _index_all, _review_and_publish, offline, qdrant_store,
)

from app.sms.knowledge import experience_scope  # noqa: E402

URL = "/api/v1/ai/reply"
PRIVATE_MESSAGE = "My divorce settlement forces a sale by March"


@pytest.fixture
def owner_setup(client, make_user, auth_header, qdrant_store, monkeypatch):  # noqa: F811  (imported fixture)
    user, headers, persona, _job, _draft = _import_owner(
        client, make_user, auth_header, "airply-owner@example.com", brokerage="brokerage-1")
    review, version = _review_and_publish(client, headers, persona["id"], confirm=(0, 1))
    assert _index_all() == 1
    # Business-only excerpts never reach an owner conversation, so the marker
    # proving owner knowledge reached the prompt must be a non-business claim.
    statement = next(claim["statement"] for claim in review["claims"][:2]
                     if experience_scope(claim["statement"]) != "business_or_commercial")
    with db.SessionLocal() as session:
        conversation = Conversation(contact="+13125550111", user_id=user.id, name="Owner",
                                    property_address="1 Test St", handled_by="broker")
        session.add(conversation)
        session.commit()
        session.add(Message(conversation_id=conversation.id, direction="inbound", text=PRIVATE_MESSAGE,
                            status="received", event_type="message.received"))
        session.commit()
        conversation_id = conversation.id
    prompts = []
    real_build = ai_runtime.build_prompt

    def capture(*args, **kwargs):
        prompts.append(real_build(*args, **kwargs))
        return prompts[-1]

    monkeypatch.setattr(ai_runtime, "build_prompt", capture)
    return {"user": user, "headers": headers, "version": version, "statement": statement,
            "conversation_id": conversation_id, "prompts": prompts}


def _call(client, headers, conversation_id, next_step="Explain your background"):
    return client.post(URL, headers=headers, params={"conversation_id": conversation_id, "next_step": next_step})


def _assert_no_private_content(text, setup):
    for secret in (setup["statement"], PRIVATE_MESSAGE, "GLOBAL POLICIES", "AUTHORIZED KNOWLEDGE",
                   "ACTIVE PUBLISHED PERSONA", "self_described_background", "natural_response_examples",
                   "owner_user_id", "published_version_id"):
        assert secret not in text, secret


def test_owner_gets_placeholder_while_private_prompt_stays_internal(client, owner_setup):
    response = _call(client, owner_setup["headers"], owner_setup["conversation_id"], owner_setup["statement"])
    assert response.status_code == 200, response.text
    assert response.json() == {"reply": REPLY_PLACEHOLDER}
    # The internal prompt really held the owner's knowledge, Persona and conversation...
    prompt = owner_setup["prompts"][-1]
    assert owner_setup["statement"] in prompt and PRIVATE_MESSAGE in prompt and "ACTIVE PUBLISHED PERSONA" in prompt
    # ...and none of it reached the caller.
    _assert_no_private_content(response.text, owner_setup)


def test_ai_runs_log_is_redacted(client, owner_setup):
    assert _call(client, owner_setup["headers"], owner_setup["conversation_id"]).status_code == 200
    with db.SessionLocal() as session:
        run = session.query(AiRun).one()
    assert run.user_id == owner_setup["user"].id and run.result == REPLY_PLACEHOLDER
    assert run.prompt.startswith("[redacted] chars=")
    assert f"persona_version_id={owner_setup['version']['id']}" in run.prompt
    _assert_no_private_content(run.prompt + run.result, owner_setup)


@pytest.mark.parametrize("role,brokerage", [
    ("agent", "brokerage-1"),   # colleague in the same brokerage
    ("hob", "brokerage-1"),     # Head of Brokerage
    ("broker", "brokerage-1"),
    ("hob", "brokerage-2"),     # another brokerage
])
def test_non_owners_are_denied_without_learning_anything(client, make_user, auth_header, owner_setup, role, brokerage):
    email = f"airply-{role}-{brokerage}@example.com"
    make_user(email, role=role, brokerage_id=brokerage)
    response = _call(client, auth_header(email), owner_setup["conversation_id"], owner_setup["statement"])
    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}
    assert owner_setup["prompts"] == []  # no prompt was even built
    with db.SessionLocal() as session:
        assert session.query(AiRun).count() == 0


def test_missing_conversation_is_indistinguishable_from_someone_elses(client, owner_setup):
    response = _call(client, owner_setup["headers"], 999_999)
    assert response.status_code == 404 and response.json() == {"detail": "Conversation not found"}


def test_brokerage_transfer_does_not_carry_the_old_persona_or_knowledge(client, make_user, auth_header, owner_setup):
    # Control: before the transfer the same neutral query retrieves the owner's knowledge.
    before = _call(client, owner_setup["headers"], owner_setup["conversation_id"], "laundromats market share")
    assert before.status_code == 200 and owner_setup["statement"] in owner_setup["prompts"][-1]
    with db.SessionLocal() as session:
        session.get(User, owner_setup["user"].id).brokerage_id = "brokerage-9"
        session.commit()
    # The retrieval query uses only the conversation and a neutral next step.
    response = _call(client, owner_setup["headers"], owner_setup["conversation_id"], "laundromats market share")
    assert response.status_code == 200 and response.json() == {"reply": REPLY_PLACEHOLDER}
    prompt = owner_setup["prompts"][-1]
    # The Persona published in brokerage-1 is not the active one in brokerage-9,
    # so neither it nor its brokerage-1 knowledge reaches the prompt.
    assert owner_setup["statement"] not in prompt
    assert '"active_published_version_id":null' in prompt
    # The former brokerage's HOB cannot reach the transferred owner's thread either.
    make_user("airply-old-hob@example.com", role="hob", brokerage_id="brokerage-1")
    assert _call(client, auth_header("airply-old-hob@example.com"),
                 owner_setup["conversation_id"]).status_code == 404


def test_resolution_errors_are_generic(client, owner_setup):
    with db.SessionLocal() as session:
        session.get(User, owner_setup["user"].id).brokerage_id = None
        session.commit()
    response = _call(client, owner_setup["headers"], owner_setup["conversation_id"])
    assert response.status_code == 409
    assert response.json() == {"detail": "Personal AI is not available for this conversation"}
    _assert_no_private_content(response.text, owner_setup)


def test_oversized_next_step_is_rejected_without_echoing_it(client, owner_setup):
    marker = "SECRET-" + "x" * 300
    response = _call(client, owner_setup["headers"], owner_setup["conversation_id"], marker)
    assert response.status_code == 422
    assert marker not in response.text
    assert owner_setup["prompts"] == []


def test_response_schema_has_only_the_reply_field():
    from app.main import app
    schema = app.openapi()["components"]["schemas"]["AiReplyOut"]
    assert set(schema["properties"]) == {"reply"}
    assert schema["required"] == ["reply"]
    assert "prompt" not in json.dumps(schema["properties"])
