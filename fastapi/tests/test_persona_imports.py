"""Offline regressions for durable Persona auto-fill jobs."""

import pytest

from app import db
from app.personas import imports
from app.personas.models import PersonaDraftExample, PersonaImportJob, PersonaVersion
from app.personas.schemas import PersonaExtractionContract

API = "/api/v1/personas"


@pytest.fixture(autouse=True)
def _ai_interpretation_path(monkeypatch):
    """These tests exercise the optional AI-assisted interpretation path."""
    monkeypatch.setattr(imports, "DEFAULT_EXTRACTION_MODE", imports.AI_MODE)
    # The AI path is off by default for Persona imports; these tests use a fake provider.
    monkeypatch.setenv("PERSONA_AI_IMPORT_ENABLED", "1")
DOCUMENT = b"""Professional Persona profile for a real estate broker. Biography: I advise owners with calm, direct communication. My tone is warm and professional. Conversation objection example: Customer asks why the valuation is low; I acknowledge the concern and explain comparable sales. Follow-up should add value without urgency."""


def _extraction(example_count=3):
    return PersonaExtractionContract.parse_obj({
        "draft": {
            "biography": "I advise owners with calm, direct communication.",
            "specialties": ["Residential real estate"],
            "service_areas": ["Toronto"],
            "style_instructions": "Answer directly, then explain.",
        },
        "style_config": {"personality": "calm and commercially aware", "warmth": 4},
        "profile_config": {
            "background": {"brand_positioning": "Clear advice without manufactured urgency."},
            "communication": {"negative_reply_behavior": "Respect the answer and do not pressure."},
        },
        "examples": [{
            "scenario": f"Objection {index}", "channel": "sms", "language": "en",
            "customer_message": f"Why is the estimate low? {index}",
            "preferred_response": "I understand the concern. May I show you the comparable sales behind the range?",
            "context": "Valuation discussion", "style_explanation": "Acknowledges before explaining.",
            "provenance": "authentic",
        } for index in range(example_count)],
        "evidence": [{
            "field_path": "draft.biography", "source_excerpt": "I advise owners with calm, direct communication.",
            "confidence": "high", "needs_owner_confirmation": True,
        }],
        "missing_questions": ["What is your licensing jurisdiction?"],
        "contradictions": [], "unsupported_claims": ["No credentials were independently verified."],
    })


def _persona(client, make_user, auth_header, email="import-owner@example.com"):
    user = make_user(email, role="broker")
    headers = auth_header(email)
    created = client.post(API, headers=headers, json={"full_name": "Import Owner"})
    assert created.status_code == 201, created.text
    return user, headers, created.json()


@pytest.mark.parametrize("name", ["profile.txt", "completed-template.md"])
def test_valid_upload_duplicate_progress_and_explicit_apply(client, make_user, auth_header, monkeypatch, name):
    _user, headers, persona = _persona(client, make_user, auth_header, f"{name}@example.com")
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": (name, DOCUMENT, "text/plain")})
    assert uploaded.status_code == 202, uploaded.text
    job_id = uploaded.json()["id"]
    duplicate = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": (name, DOCUMENT, "text/plain")})
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == job_id

    monkeypatch.setattr(imports, "extract_document", lambda _text: _extraction(35))
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True

    completed = client.get(f"{API}/{persona['id']}/imports/{job_id}", headers=headers)
    assert completed.status_code == 200
    payload = completed.json()
    assert payload["status"] == "completed"
    assert [payload["stages"][name]["status"] for name in imports.STAGES] == ["completed"] * 5
    assert payload["assessment"]["uncertain_fields"] == ["draft.biography"]

    applied = client.post(f"{API}/{persona['id']}/imports/{job_id}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    assert applied.json()["biography"].startswith("I advise owners")
    assert applied.json()["profile_config"]["background"]["brand_positioning"].startswith("Clear advice")
    assert client.post(f"{API}/{persona['id']}/imports/{job_id}/apply", headers=headers, json={"revision": 2}).status_code == 409
    with db.SessionLocal() as session:
        assert session.query(PersonaDraftExample).filter_by(persona_id=persona["id"]).count() == 35
        assert session.query(PersonaDraftExample).filter_by(persona_id=persona["id"], owner_approved=1).count() == 0
        assert session.query(PersonaVersion).filter_by(persona_id=persona["id"]).count() == 0


def test_validation_failure_retry_resume_and_owner_isolation(client, make_user, auth_header, monkeypatch):
    _owner, headers, persona = _persona(client, make_user, auth_header)
    outsider = make_user("import-outsider@example.com", role="agent", brokerage_id="other-brokerage")
    outsider_headers = auth_header(outsider.email)
    assert client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": ("empty.txt", b"", "text/plain")}).status_code == 422
    assert client.get(f"{API}/{persona['id']}/imports", headers=outsider_headers).status_code == 404

    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": ("profile.txt", DOCUMENT, "text/plain")}).json()
    monkeypatch.setattr(imports, "extract_document", lambda _text: (_ for _ in ()).throw(imports.ImportFailure("ai_unavailable", "Temporarily unavailable", retryable=True)))
    with db.SessionLocal() as session:
        imports.process_one(session)
    failed = client.get(f"{API}/{persona['id']}/imports/{uploaded['id']}", headers=headers).json()
    assert failed["status"] == "failed" and failed["retryable"] is True
    assert failed["stages"]["personality_extraction"]["status"] == "failed"

    retried = client.post(f"{API}/{persona['id']}/imports/{uploaded['id']}/retry", headers=headers)
    assert retried.status_code == 202
    monkeypatch.setattr(imports, "extract_document", lambda _text: _extraction())
    with db.SessionLocal() as session:
        imports.process_one(session)
    resumed = client.get(f"{API}/{persona['id']}/imports/{uploaded['id']}", headers=headers).json()
    assert resumed["status"] == "completed" and resumed["attempt_count"] == 2
    assert client.get(f"{API}/{persona['id']}/imports/{uploaded['id']}", headers=outsider_headers).status_code == 404


@pytest.mark.parametrize("name,content,code", [
    ("broken.docx", b"not a zip document with persona communication", "corrupt_file"),
    ("irrelevant.txt", b"This is a grocery shopping list with apples bananas bread milk and household supplies for the weekend.", "irrelevant_document"),
    ("tiny.txt", b"persona", "unreadable_file"),
])
def test_corrupt_irrelevant_and_unreadable_jobs_fail_without_provider(client, make_user, auth_header, monkeypatch, name, content, code):
    _user, headers, persona = _persona(client, make_user, auth_header, f"{code}@example.com")
    monkeypatch.setattr(imports, "extract_document", lambda _text: pytest.fail("provider boundary must not run"))
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": (name, content, "application/octet-stream")})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        imports.process_one(session)
    failed = client.get(f"{API}/{persona['id']}/imports/{uploaded.json()['id']}", headers=headers).json()
    assert failed["status"] == "failed" and failed["error_code"] == code and failed["retryable"] is False


def test_stale_apply_preserves_existing_draft(client, make_user, auth_header, monkeypatch):
    _user, headers, persona = _persona(client, make_user, auth_header, "stale-import@example.com")
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": ("profile.txt", DOCUMENT, "text/plain")}).json()
    monkeypatch.setattr(imports, "extract_document", lambda _text: _extraction())
    with db.SessionLocal() as session:
        imports.process_one(session)
    changed = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={"revision": 1, "biography": "Newer manual biography."})
    assert changed.status_code == 200
    rejected = client.post(f"{API}/{persona['id']}/imports/{uploaded['id']}/apply", headers=headers, json={"revision": 2})
    assert rejected.status_code == 409
    assert client.get(f"{API}/{persona['id']}/draft", headers=headers).json()["biography"] == "Newer manual biography."
    with db.SessionLocal() as session:
        assert session.get(PersonaImportJob, uploaded["id"]).status == "completed"
