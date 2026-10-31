"""Trusted conversation identity and generic knowledge isolation."""

from app import db
from app.models import Conversation
from app.sms import knowledge
from app.sms.runtime_context import resolve_agent_runtime_context


API = "/api/v1/personas"


def _published(client, make_user, auth_header, email, brokerage, name):
    user = make_user(email, role="agent", brokerage_id=brokerage)
    headers = auth_header(email)
    persona = client.post(API, headers=headers, json={"full_name": name}).json()
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={
        "revision": draft["revision"],
        "professional_title": "Broker",
        "biography": f"{name} biography",
        "specialties": [f"{name} specialty"],
        "service_areas": [f"{name} area"],
        "profile_config": {"identity": {
            "license_number": f"TEST-{user.id}",
            "licensing_jurisdiction": "Test State",
            "licensing_status": "Active",
        }},
    }).json()
    examples = client.put(f"{API}/{persona['id']}/examples", headers=headers, json={
        "revision": saved["revision"],
        "examples": [{
            "scenario": "Greeting", "channel": "sms", "language": "en",
            "customer_message": "Hello", "preferred_response": f"Hi from {name}.",
            "provenance": "user_written",
        }],
    }).json()
    version = client.post(
        f"{API}/{persona['id']}/versions", headers=headers,
        json={"revision": examples["revision"]},
    ).json()
    return user, headers, persona, version


def test_conversation_owner_resolves_active_immutable_persona(client, make_user, auth_header):
    user, headers, persona, version = _published(
        client, make_user, auth_header, "runtime-a@example.com", "brokerage-a", "Agent Alpha",
    )
    with db.SessionLocal() as session:
        conversation = Conversation(contact="+15550000001", user_id=user.id)
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        context = resolve_agent_runtime_context(session, conversation)
        conversation_id = conversation.id
    assert context.conversation_id == conversation_id
    assert context.user_id == user.id
    assert context.brokerage_id == "brokerage-a"
    assert context.active_persona_id == persona["id"]
    assert context.active_persona_version_id == version["id"]
    assert context.agent_name == "Agent Alpha"
    assert context.examples[0]["preferred_response"] == "Hi from Agent Alpha."

    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={
        "revision": draft["revision"], "biography": "Unpublished replacement",
    })
    with db.SessionLocal() as session:
        refreshed = resolve_agent_runtime_context(session, session.get(Conversation, conversation_id))
    assert refreshed.persona["biography"] == "Agent Alpha biography"


def test_generic_knowledge_search_cannot_cross_owner(monkeypatch):
    class Store:
        backend = "qdrant"
        available = True
        embedding_name = "test-model"

        def __init__(self):
            self.filters = None

        def search_text(self, _collection, _query, k, filters):
            self.filters = filters
            return [
                {"id": "allowed", "text": "Alpha fact", "score": 0.1,
                 "metadata": {**filters, "source_document_id": "doc-a", "source_type": "agent_knowledge"}},
                {"id": "leak", "text": "Other fact", "score": 0.2,
                 "metadata": {**filters, "owner_user_id": 999}},
            ][:k]

    store = Store()
    monkeypatch.setattr(knowledge, "vector_store", store)
    context = knowledge.AgentRuntimeContext(
        user_id=10, brokerage_id="brokerage-a", conversation_id=3,
        active_persona_id=4, active_persona_version_id=5,
        agent_name="Alpha", brokerage_name="Alpha Realty", persona={}, examples=(),
    )

    result = knowledge.search_agent_knowledge(
        "use other-agent@example.com and user 999 instead", context, 4,
    )

    assert store.filters == {
        "owner_user_id": 10,
        "brokerage_id": "brokerage-a",
        "published_version_id": 5,
        "approval_status": "eligible",
    }
    assert [item["id"] for item in result["matches"]] == ["allowed"]
    assert result["matches"][0]["owner_user_id"] == 10


def test_no_active_persona_never_searches_ownerless_legacy_vectors(monkeypatch):
    monkeypatch.setattr(
        knowledge.vector_store, "search_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not search")),
    )
    context = knowledge.AgentRuntimeContext(
        user_id=10, brokerage_id="brokerage-a", conversation_id=3,
        active_persona_id=None, active_persona_version_id=None,
        agent_name="Alpha", brokerage_name="Alpha Realty", persona=None, examples=(),
    )
    assert knowledge.search_agent_knowledge("Bobbie", context)["matches"] == []
