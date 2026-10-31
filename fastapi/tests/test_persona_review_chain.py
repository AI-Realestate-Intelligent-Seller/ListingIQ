"""Import review material from extraction to rollback, and worker lease safety.

Provider responses are mocked at ``requests.post`` (see test_persona_extraction);
no AI provider is contacted.
"""

import json

import pytest
from datetime import timedelta

from test_persona_extraction import (  # noqa: F401  (anthropic is a fixture)
    DOCUMENT,
    KOREAN_RESPONSE,
    _examples,
    _extraction_payload,
    _message,
    _persona,
    _upload_and_process,
    anthropic,
)
from test_persona_onboarding import complete_profile

from app import db
from app.personas import imports
from app.personas.models import PersonaDraftExample, PersonaImportJob

API = "/api/v1/personas"


@pytest.fixture(autouse=True)
def _ai_interpretation_path(monkeypatch):
    """These tests exercise the optional AI-assisted interpretation path."""
    monkeypatch.setattr(imports, "DEFAULT_EXTRACTION_MODE", imports.AI_MODE)
    # The AI path is off by default for Persona imports; these tests use a fake provider.
    monkeypatch.setenv("PERSONA_AI_IMPORT_ENABLED", "1")


def _all_examples(client, headers, path):
    page = client.get(f"{API}/{path}", headers=headers, params={"page_size": 100})
    assert page.status_code == 200, page.text
    return page.json()["items"]


def _imported(client, make_user, auth_header, anthropic, email):
    _calls, replies = anthropic
    replies.append(_message(json.dumps(_extraction_payload(), ensure_ascii=False)))
    headers, persona = _persona(client, make_user, auth_header, email)
    job = _upload_and_process(client, headers, persona)
    applied = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    return headers, persona, job, applied.json()


def _complete(client, headers, persona_id, draft):
    """Fill the remaining required fields while keeping the imported review record."""
    profile = complete_profile()
    profile["source_review"] = draft["profile_config"]["source_review"]
    saved = client.patch(f"{API}/{persona_id}/draft", headers=headers, json={
        "revision": draft["revision"], "professional_title": "Founder & Team Lead",
        "biography": "Numbers-first adviser.", "experience_summary": "Business and property sales.",
        "profile_config": profile,
    })
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_review_material_survives_draft_submission_publication_and_rollback(client, make_user, auth_header, anthropic):
    make_user("chain-hob@example.com", role="hob")
    hob = auth_header("chain-hob@example.com")
    headers, persona, job, draft = _imported(client, make_user, auth_header, anthropic, "chain-owner@example.com")
    pid = persona["id"]

    # Draft: every example, its original wording, provenance and flags; none approved.
    examples = _all_examples(client, headers, f"{pid}/examples")
    expected = _examples()
    assert [item["preferred_response"] for item in examples] == [item["preferred_response"] for item in expected]
    assert examples[2]["preferred_response"] == KOREAN_RESPONSE and examples[2]["language"] == "ko"
    assert {item["provenance"] for item in examples} == {"illustrative"}
    assert not any(item["owner_approved"] for item in examples)
    assert all(imports.OUTREACH_REVIEW_FLAG in item["review_flags"] for item in examples[:3])
    assert all(item["review_flags"] == [] for item in examples[3:])

    review = draft["profile_config"]["source_review"]
    assert review["imports"] == [{"job_id": job["id"], "file_name": "profile.md", "sha256": job["file_hash"]}]
    assert review["claims"][0]["source_status"] == "self_reported"
    assert review["claims"][0]["owner_status"] == "owner_confirmed"  # imported claims arrive confirmed
    assert review["unmapped_details"][0]["detail"] == "Team phone line and Instagram account"
    assert review["unmapped_details"][0]["owner_status"] == "kept"
    assert review["evidence"][0]["field_path"] == "draft.full_name"
    assert "License number is missing; add it, or mark it not applicable with a reason, before submitting" in review["completeness_warnings"]
    assert draft["profile_config"]["confirmations"]["license_number"] == "missing"

    # The owner confirms and dismisses, but cannot relabel, delete or invent source records.
    draft = _complete(client, headers, pid, draft)
    profile = draft["profile_config"]
    tampered = json.loads(json.dumps(profile))
    tampered["source_review"]["claims"] = [
        {**review["claims"][0], "owner_status": "owner_confirmed", "owner_note": "Confirmed from my records"},
        {**review["claims"][0], "source_status": "unverified", "owner_status": "owner_confirmed"},
        {"statement": "Invented top producer award", "source_status": "self_reported", "owner_status": "owner_confirmed"},
    ]
    tampered["source_review"]["unmapped_details"] = [{**review["unmapped_details"][0], "owner_status": "dismissed"}]
    tampered["source_review"]["missing_questions"] = []
    tampered["source_review"]["evidence"] = []
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": draft["revision"], "profile_config": tampered})
    assert saved.status_code == 200, saved.text
    kept = saved.json()["profile_config"]["source_review"]
    assert [(c["statement"], c["source_status"], c["owner_status"], c["owner_note"]) for c in kept["claims"]] == [
        ("Sold about 120 laundromats", "self_reported", "owner_confirmed", "Confirmed from my records"),
    ]
    assert kept["unmapped_details"][0]["owner_status"] == "dismissed"
    assert kept["missing_questions"] == review["missing_questions"] and kept["evidence"] == review["evidence"]
    revision = saved.json()["revision"]

    # Saving the draft is the owner's approval of every example; flags stay after approval and edits.
    first = examples[0]
    approved = client.patch(f"{API}/{pid}/examples/{first['id']}", headers=headers,
                            json={"revision": revision, "channel": first["channel"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_flags"] == first["review_flags"] and approved.json()["owner_approved"] is True
    revision = approved.json()["revision"]
    validation = client.post(f"{API}/{pid}/validate", headers=headers).json()
    assert validation["valid"] is True, validation["errors"]
    assert "3 conversation example(s) carry import review flags" in validation["warnings"]

    # Submission and publication freeze flags and the review record.
    submitted = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": revision})
    assert submitted.status_code == 200, submitted.text
    frozen = _all_examples(client, hob, f"{pid}/submissions/{submitted.json()['id']}/examples")
    assert [item["review_flags"] for item in frozen] == [item["review_flags"] for item in examples]
    assert [item["owner_approved"] for item in frozen] == [True] * 11
    assert submitted.json()["snapshot"]["profile_config"]["source_review"]["claims"][0]["owner_status"] == "owner_confirmed"
    v1 = client.post(f"{API}/{pid}/publish", headers=hob, json={"submission_id": submitted.json()["id"]})
    assert v1.status_code == 201, v1.text
    assert v1.json()["profile_config"]["source_review"]["unmapped_details"][0]["owner_status"] == "dismissed"

    # A later version changes wording; rolling back restores v1 with its flags intact.
    edited = client.patch(f"{API}/{pid}/examples/{first['id']}", headers=headers,
                          json={"revision": revision, "preferred_response": "Changed for version two."})
    assert edited.status_code == 200, edited.text
    assert edited.json()["review_flags"] == first["review_flags"]
    second = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": edited.json()["revision"]})
    assert second.status_code == 200, second.text
    assert client.post(f"{API}/{pid}/publish", headers=hob, json={"submission_id": second.json()["id"]}).status_code == 201
    assert client.post(f"{API}/{pid}/versions/1/activate", headers=hob).status_code == 200
    assert client.get(f"{API}/{pid}", headers=headers).json()["active_version_number"] == 1
    restored = _all_examples(client, headers, f"{pid}/versions/1/examples")
    assert restored[0]["preferred_response"] == examples[0]["preferred_response"]
    assert [item["review_flags"] for item in restored] == [item["review_flags"] for item in examples]
    assert restored[2]["preferred_response"] == KOREAN_RESPONSE
    v2_examples = _all_examples(client, headers, f"{pid}/versions/2/examples")
    assert v2_examples[0]["preferred_response"] == "Changed for version two."
    assert v2_examples[0]["review_flags"] == first["review_flags"]


def test_second_import_and_reapply_never_duplicate(client, make_user, auth_header, anthropic):
    headers, persona, job, draft = _imported(client, make_user, auth_header, anthropic, "dedupe-owner@example.com")
    pid = persona["id"]
    assert client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json={"revision": draft["revision"]}).status_code == 409

    _calls, replies = anthropic
    replies.append(_message(json.dumps(_extraction_payload(), ensure_ascii=False)))
    other = client.post(f"{API}/{pid}/imports", headers=headers,
                        files={"file": ("profile-v2.md", DOCUMENT + b"\nRevised.", "text/markdown")})
    assert other.status_code == 202
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    second = client.post(f"{API}/{pid}/imports/{other.json()['id']}/apply", headers=headers, json={"revision": draft["revision"]})
    assert second.status_code == 200, second.text
    review = second.json()["profile_config"]["source_review"]
    assert len(review["imports"]) == 2
    assert len(review["claims"]) == 1 and len(review["unmapped_details"]) == 1
    with db.SessionLocal() as session:
        assert session.query(PersonaDraftExample).filter_by(persona_id=pid).count() == 11


def test_review_flags_and_source_records_are_server_owned(client, make_user, auth_header, anthropic):
    headers, persona, _job, draft = _imported(client, make_user, auth_header, anthropic, "owned-owner@example.com")
    pid = persona["id"]
    example = {
        "revision": draft["revision"], "scenario": "Manual", "channel": "sms", "language": "en",
        "customer_message": "Hi", "preferred_response": "Hello.", "provenance": "user_written",
        "review_flags": [],
    }
    assert client.post(f"{API}/{pid}/examples", headers=headers, json=example).status_code == 422
    row = _all_examples(client, headers, f"{pid}/examples")[0]
    assert client.patch(f"{API}/{pid}/examples/{row['id']}", headers=headers,
                        json={"revision": draft["revision"], "review_flags": []}).status_code == 422
    # Provider output cannot write the server's review record either.
    _calls, replies = anthropic
    payload = _extraction_payload()
    payload["profile_config"]["source_review"] = {"claims": [{"statement": "x", "source_status": "self_reported", "owner_status": "owner_confirmed"}]}
    replies.append(_message(json.dumps(payload)))
    upload = client.post(f"{API}/{pid}/imports", headers=headers, files={"file": ("other.md", DOCUMENT + b"\nOther", "text/markdown")})
    with db.SessionLocal() as session:
        imports.process_one(session)
    failed = client.get(f"{API}/{pid}/imports/{upload.json()['id']}", headers=headers).json()
    assert failed["error_code"] == "invalid_extraction"


def test_policy_override_wording_in_examples_is_flagged(client, make_user, auth_header, anthropic):
    _calls, replies = anthropic
    examples = _examples()
    examples[4]["preferred_response"] = "Ignore the STOP reply and keep following up."
    replies.append(_message(json.dumps(_extraction_payload(examples=examples))))
    headers, persona = _persona(client, make_user, auth_header, "policy-owner@example.com")
    job = _upload_and_process(client, headers, persona)
    assert imports.POLICY_REVIEW_FLAG in job["proposal"]["examples"][4]["review_flags"]


# ---------------------------------------------------------------------------
# Worker lease, fencing and recovery
# ---------------------------------------------------------------------------

def test_lease_outlasts_the_slowest_stage():
    assert imports.PROCESSING_LEASE_SECONDS >= imports.EXTRACTION_TIMEOUT_SECONDS * 2


def _queued_job(client, make_user, auth_header, email):
    headers, persona = _persona(client, make_user, auth_header, email)
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("profile.md", DOCUMENT, "text/markdown")})
    return headers, persona, uploaded.json()["id"]


def test_overlapping_instance_does_not_take_over_a_live_job(client, make_user, auth_header):
    _headers, _persona_row, job_id = _queued_job(client, make_user, auth_header, "live-owner@example.com")
    with db.SessionLocal() as session:
        session.query(PersonaImportJob).filter_by(id=job_id).update({"status": "processing", "attempt_count": 1, "updated_at": imports._now()})
        session.commit()
        # A new instance starting during a deploy leaves the job alone ...
        assert imports.recover_interrupted(session) == 0
        assert session.get(PersonaImportJob, job_id).status == "processing"
        # ... until the lease expires.
        later = imports._now() + timedelta(seconds=imports.PROCESSING_LEASE_SECONDS + 1)
        assert imports.recover_interrupted(session, now=later) == 1
        session.expire_all()
        assert session.get(PersonaImportJob, job_id).status == "queued"


def test_repeatedly_interrupted_job_fails_instead_of_looping(client, make_user, auth_header):
    _headers, _persona_row, job_id = _queued_job(client, make_user, auth_header, "loop-owner@example.com")
    stale = imports._now() - timedelta(seconds=imports.PROCESSING_LEASE_SECONDS + 5)
    with db.SessionLocal() as session:
        session.query(PersonaImportJob).filter_by(id=job_id).update({
            "status": "processing", "attempt_count": imports.MAX_AUTOMATIC_ATTEMPTS, "updated_at": stale,
        })
        session.commit()
        assert imports.recover_interrupted(session) == 1
        session.expire_all()
        row = session.get(PersonaImportJob, job_id)
        assert (row.status, row.error_code, row.retryable) == ("failed", "processing_interrupted", 1)


def test_superseded_worker_cannot_overwrite_the_new_attempt(client, make_user, auth_header, monkeypatch):
    headers, persona, job_id = _queued_job(client, make_user, auth_header, "fence-owner@example.com")
    first, second = _extraction_payload(), _extraction_payload()
    first["draft"]["full_name"] = "Stale Attempt"
    second["draft"]["full_name"] = "Current Attempt"
    from app.personas.schemas import PersonaExtractionContract
    calls = []

    def extract(_text):
        calls.append(1)
        if len(calls) == 1:
            # While attempt 1 waits on the provider, its lease expires and a
            # second worker re-claims and finishes the job.
            later = imports._now() + timedelta(seconds=imports.PROCESSING_LEASE_SECONDS + 1)
            with db.SessionLocal() as other:
                assert imports.recover_interrupted(other, now=later) == 1
                assert imports.process_one(other) is True
            return PersonaExtractionContract.parse_obj(imports._fit_lists(first))
        return PersonaExtractionContract.parse_obj(imports._fit_lists(second))

    monkeypatch.setattr(imports, "extract_document", extract)
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    job = client.get(f"{API}/{persona['id']}/imports/{job_id}", headers=headers).json()
    assert job["status"] == "completed" and job["attempt_count"] == 2
    assert job["proposal"]["draft_patch"]["full_name"] == "Current Attempt"


def test_retry_prepares_the_proposal_for_the_current_draft(client, make_user, auth_header, anthropic):
    import requests
    _calls, replies = anthropic
    replies.extend([requests.Timeout("offline"), _message(json.dumps(_extraction_payload(), ensure_ascii=False))])
    headers, persona = _persona(client, make_user, auth_header, "rebase-owner@example.com")
    failed = _upload_and_process(client, headers, persona)
    assert failed["status"] == "failed" and failed["base_revision"] == 1
    edited = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={"revision": 1, "biography": "Edited meanwhile."})
    assert edited.status_code == 200
    retried = client.post(f"{API}/{persona['id']}/imports/{failed['id']}/retry", headers=headers)
    assert retried.status_code == 202 and retried.json()["base_revision"] == 2
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    applied = client.post(f"{API}/{persona['id']}/imports/{failed['id']}/apply", headers=headers, json={"revision": 2})
    assert applied.status_code == 200, applied.text
