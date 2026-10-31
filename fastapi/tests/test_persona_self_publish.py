"""Personal Personas are self-managed: the owner publishes directly, without HOB review."""

import socket

import pytest
import requests

from app import db
from app.ai import client as ai_client
from app.models import User
from app.personas import service
from app.personas.models import Persona, PersonaDefault, PersonaEvent, PersonaSubmission, PersonaVersion, PersonaVersionExample

API = "/api/v1/personas"


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("no AI provider or network call is allowed here")
    monkeypatch.setattr(ai_client, "completion", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _complete_persona(client, make_user, auth_header, email, role, brokerage_id="brokerage-1"):
    make_user(email, role=role, brokerage_id=brokerage_id)
    headers = auth_header(email)
    persona = client.post(API, headers=headers, json={"full_name": "Dana Lee"}).json()
    pid = persona["id"]
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={
        "revision": draft["revision"], "professional_title": "Associate Broker", "biography": "Twelve years on the North Side.",
        "specialties": ["Condos"], "service_areas": ["Lakeview"],
        "profile_config": {"identity": {"license_number": "475.123456", "licensing_jurisdiction": "Illinois", "licensing_status": "Active"}},
    }).json()
    examples = client.put(f"{API}/{pid}/examples", headers=headers, json={"revision": saved["revision"], "examples": [
        {"scenario": "Introducing myself to a new client", "channel": "sms", "language": "ko", "customer_message": "",
         "preferred_response": "안녕하세요! Dana here 😊\n\nHappy to help.", "provenance": "illustrative"},
    ]}).json()
    return headers, pid, examples["revision"]


def _publish(client, headers, pid, revision):
    return client.post(f"{API}/{pid}/versions", headers=headers, json={"revision": revision})


@pytest.mark.parametrize("role", ["hob", "broker", "agent"])
def test_every_role_publishes_their_own_persona_without_review(client, make_user, auth_header, role):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, f"self-{role}@example.com", role)
    published = _publish(client, headers, pid, revision)
    assert published.status_code == 201, published.text
    version = published.json()
    assert version["version_number"] == 1 and version["published_by_user_id"] is not None
    persona = client.get(f"{API}/{pid}", headers=headers).json()
    assert persona["active_version_number"] == 1
    sms_default = client.get(f"{API}/defaults/sms", headers=headers).json()
    assert sms_default["persona"]["id"] == pid
    assert sms_default["resolved_version"]["id"] == version["id"]
    assert client.get(f"{API}/defaults/email", headers=headers).json()["persona"] is None
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    assert draft["review_status"] == "published" and draft["revision"] == revision
    # No submission and nothing waiting for anyone.
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []
    examples = client.get(f"{API}/{pid}/versions/1/examples", headers=headers).json()["items"]
    assert examples[0]["preferred_response"] == "안녕하세요! Dana here 😊\n\nHappy to help."
    assert examples[0]["provenance"] == "illustrative" and examples[0]["owner_approved"] is True
    with db.SessionLocal() as session:
        events = [row.event_type for row in session.query(PersonaEvent).filter_by(persona_id=pid)]
    assert "persona.version_published" in events and "persona.version_activated" in events


def test_incomplete_or_stale_drafts_cannot_publish_and_saving_does_not_publish(client, make_user, auth_header):
    make_user("incomplete@example.com", role="agent")
    headers = auth_header("incomplete@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Pat"}).json()
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    refused = _publish(client, headers, persona["id"], draft["revision"])
    assert refused.status_code == 422
    saved = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={"revision": draft["revision"], "biography": "Bio."})
    assert saved.status_code == 200
    assert client.get(f"{API}/{persona['id']}/versions", headers=headers).json() == []

    headers, pid, revision = _complete_persona(client, make_user, auth_header, "stale@example.com", "broker")
    assert _publish(client, headers, pid, revision - 1).status_code == 409
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []


def test_only_the_owner_can_publish_or_activate(client, make_user, auth_header):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, "owner-only@example.com", "agent")
    make_user("same-hob@example.com", role="hob")
    make_user("same-broker@example.com", role="broker")
    make_user("other-hob@example.com", role="hob", brokerage_id="brokerage-2")
    for email in ("same-hob@example.com", "same-broker@example.com", "other-hob@example.com"):
        assert _publish(client, auth_header(email), pid, revision).status_code == 404
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []
    assert _publish(client, headers, pid, revision).status_code == 201
    # Another brokerage's HOB cannot activate it either.
    assert client.post(f"{API}/{pid}/versions/1/activate", headers=auth_header("other-hob@example.com")).status_code == 404


def test_owner_who_left_the_brokerage_cannot_publish(client, make_user, auth_header):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, "mover@example.com", "agent")
    with db.SessionLocal() as session:
        user = session.query(User).filter_by(email="mover@example.com").one()
        user.brokerage_id = "brokerage-2"
        from datetime import datetime
        session.get(Persona, pid).membership_ended_at = datetime.utcnow()
        session.commit()
    assert _publish(client, auth_header("mover@example.com"), pid, revision).status_code == 404
    with db.SessionLocal() as session:
        assert session.query(PersonaVersion).filter_by(persona_id=pid).count() == 0


def test_repeated_and_concurrent_publication_never_duplicates(client, make_user, auth_header, monkeypatch):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, "double@example.com", "broker")
    first = _publish(client, headers, pid, revision)
    again = _publish(client, headers, pid, revision)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]

    # A second request racing for the same version number is stopped by the
    # database; nothing partial is left behind.
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    changed = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": draft["revision"], "biography": "Updated."}).json()
    monkeypatch.setattr(service, "_next_version_number", lambda _session, _persona_id: 1)
    raced = _publish(client, headers, pid, changed["revision"])
    assert raced.status_code == 409
    with db.SessionLocal() as session:
        assert session.query(PersonaVersion).filter_by(persona_id=pid).count() == 1
        assert session.query(PersonaVersionExample).join(PersonaVersion).filter(PersonaVersion.persona_id == pid).count() == 1
        assert session.query(PersonaDefault).filter_by(user_id=first.json()["published_by_user_id"], channel="sms", persona_id=pid).count() == 1


def test_new_versions_rollback_and_immutable_history(client, make_user, auth_header):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, "history@example.com", "hob")
    first = _publish(client, headers, pid, revision)
    assert first.status_code == 201
    assert client.get(f"{API}/defaults/sms", headers=headers).json()["resolved_version"]["id"] == first.json()["id"]
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    edited = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": draft["revision"], "biography": "A newer biography."}).json()
    # Editing the draft never changes the published version.
    assert client.get(f"{API}/{pid}/versions/1", headers=headers).json()["biography"] == "Twelve years on the North Side."
    second = _publish(client, headers, pid, edited["revision"])
    assert second.status_code == 201 and second.json()["version_number"] == 2
    assert client.get(f"{API}/{pid}", headers=headers).json()["active_version_number"] == 2
    assert client.get(f"{API}/defaults/sms", headers=headers).json()["resolved_version"]["id"] == second.json()["id"]
    # The owner restores version 1 and can use it by default.
    restored = client.post(f"{API}/{pid}/versions/1/activate", headers=headers)
    assert restored.status_code == 200, restored.text
    assert client.get(f"{API}/{pid}", headers=headers).json()["active_version_number"] == 1
    assert client.get(f"{API}/defaults/sms", headers=headers).json()["resolved_version"]["id"] == first.json()["id"]
    assert [row["version_number"] for row in client.get(f"{API}/{pid}/versions", headers=headers).json()] == [2, 1]


def test_publishing_and_restoring_never_changes_another_users_sms_default(client, make_user, auth_header):
    a_headers, a_pid, a_revision = _complete_persona(client, make_user, auth_header, "default-a@example.com", "agent")
    b_headers, b_pid, b_revision = _complete_persona(client, make_user, auth_header, "default-b@example.com", "broker")
    a_v1 = _publish(client, a_headers, a_pid, a_revision).json()
    b_v1 = _publish(client, b_headers, b_pid, b_revision).json()

    a_draft = client.get(f"{API}/{a_pid}/draft", headers=a_headers).json()
    a_changed = client.patch(f"{API}/{a_pid}/draft", headers=a_headers, json={"revision": a_draft["revision"], "biography": "Updated owner A."}).json()
    a_v2 = _publish(client, a_headers, a_pid, a_changed["revision"]).json()
    assert client.get(f"{API}/defaults/sms", headers=a_headers).json()["resolved_version"]["id"] == a_v2["id"]
    assert client.get(f"{API}/defaults/sms", headers=b_headers).json()["resolved_version"]["id"] == b_v1["id"]

    assert client.post(f"{API}/{a_pid}/versions/1/activate", headers=a_headers).status_code == 200
    assert client.get(f"{API}/defaults/sms", headers=a_headers).json()["resolved_version"]["id"] == a_v1["id"]
    assert client.get(f"{API}/defaults/sms", headers=b_headers).json()["resolved_version"]["id"] == b_v1["id"]


def test_existing_submissions_stay_intact_and_do_not_block_publication(client, make_user, auth_header):
    headers, pid, revision = _complete_persona(client, make_user, auth_header, "legacy@example.com", "agent")
    pending = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": revision})
    assert pending.status_code == 200, pending.text
    published = _publish(client, headers, pid, revision)
    assert published.status_code == 201, published.text
    rows = client.get(f"{API}/{pid}/submissions", headers=headers).json()
    # The old request is kept as it was and is not silently published.
    assert [(row["id"], row["status"], row["published_version_id"]) for row in rows] == [(pending.json()["id"], "submitted", None)]
    with db.SessionLocal() as session:
        assert session.query(PersonaSubmission).filter_by(persona_id=pid).count() == 1
