"""The Personal AI demo flow, end to end, with every network path refused.

Covers the Word template, local imports of the Michael Kang document in
Markdown and Word form, the simplified completeness rules, manual setup and
the guarantee that Persona imports never call an AI provider.
"""

import io
import re
import socket
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import fitz
import pytest
import requests

from app import db
from app.ai import client as ai_client
from app.personas import imports, service, template_document
from app.personas.models import PersonaImportJob, PersonaVersion
from app.personas.template_parser import parse_document

API = "/api/v1/personas"
DOCUMENT = (Path(__file__).parent / "fixtures" / "persona" / "michael-kang.md").read_text(encoding="utf-8")
KOREAN_OUTREACH = ("안녕하세요 {FirstName} 사장님, SpaceMatch의 마이클 강입니다. 시카고에서 세탁소·비즈니스 매매를 많이 진행하고 "
                   "있습니다. 혹시 은퇴나 매각 생각 있으시면 편하게 상담 한번 드리겠습니다.")
STAGES = ("document_validation", "personality_extraction", "conversation_analysis", "completeness_assessment", "draft_preparation")


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    """Record and refuse every AI or network attempt made during a test."""
    attempts = []

    def refuse(name):
        def _refuse(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"{name} must not be called during a Persona import")
        return _refuse

    monkeypatch.delenv("PERSONA_AI_IMPORT_ENABLED", raising=False)
    monkeypatch.setattr(ai_client, "completion", refuse("ai.client.completion"))
    monkeypatch.setattr(imports, "completion", refuse("imports.completion"))
    monkeypatch.setattr(imports, "extract_document", refuse("imports.extract_document"))
    monkeypatch.setattr(requests, "post", refuse("requests.post"))
    monkeypatch.setattr(socket.socket, "connect", refuse("socket.connect"))
    # A configured provider must still never be used for Persona imports.
    monkeypatch.setattr(ai_client, "is_configured", lambda: True)
    yield attempts
    assert attempts == []


def _owner(client, make_user, auth_header, email, full_name="Michael Kang"):
    make_user(email, role="broker")
    headers = auth_header(email)
    created = client.post(API, headers=headers, json={"full_name": full_name})
    assert created.status_code == 201, created.text
    return headers, created.json()


def _import(client, headers, persona, name, content, media):
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": (name, content, media)})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    return client.get(f"{API}/{persona['id']}/imports/{uploaded.json()['id']}", headers=headers).json()


def _docx_body(data: bytes) -> str:
    return imports._docx_text(ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")))


def _michael_docx() -> bytes:
    """The Michael Kang document typed into the downloadable Word template."""
    contract, _ = parse_document(DOCUMENT)
    # Michael's examples are typed into copies of the "Additional example" block.
    examples = [{
        "Scenario": item.scenario, "Channel": item.channel.upper(), "Language": "Korean" if item.language == "ko" else "English",
        "Customer's message": item.customer_message, "My exact response": item.preferred_response, "Source": item.provenance,
    } for item in contract.examples]
    answers = {
        "Full name and preferred display name": "Michael Kang", "Professional role and title":
            "Founder & Team Lead, SpaceMatch Inc. Licensed Illinois real estate broker.",
        "Number": "N/A, not provided in the source material", "Jurisdiction": "Illinois",
        "Biography": contract.draft["biography"].replace("\n", " "),
        "Specialties": ", ".join(contract.draft["specialties"][1:]), "Service areas": "Chicago and suburbs",
        "Achievements (add self-reported after each one that is not independently verified)":
            "Has sold about 120 laundromats over roughly five years (self-reported)",
        "Languages": "English (default), Korean (fluent)",
    }
    return template_document.docx_template(answers, additional=examples)


# --- Template ----------------------------------------------------------------

def test_template_downloads_as_a_real_word_document(client, make_user, auth_header):
    make_user("template@example.com", role="agent")
    response = client.get(f"{API}/template", headers=auth_header("template@example.com"))
    assert response.status_code == 200
    assert response.headers["content-type"] == template_document.DOCX_MEDIA_TYPE
    assert 'filename="listingiq-persona-template.docx"' in response.headers["content-disposition"]
    assert response.content[:2] == b"PK" and not response.content.startswith(b"#")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                "word/numbering.xml", "word/_rels/document.xml.rels"} <= names
        for name in names:
            ElementTree.fromstring(archive.read(name))  # every part is well-formed XML
    text = re.sub(r"[ \t]+\n", "\n", _docx_body(response.content) + "\n")
    for heading in ("## Who I Am", "## Professional Background", "## Communication DNA",
                    "## Conversation Playbook", "## Conversation Examples"):
        assert heading in text
    # The same seven questions as the setup form, then a repeatable additional-example block.
    assert template_document.COMMUNICATION_INSTRUCTION in text
    for number, (question, _scenario, _reply) in enumerate(template_document.COMMUNICATION_QUESTIONS, start=1):
        assert f"### {number:02d} — {question}\nResponse:" in text
    assert "### 08 — Add another real example of how you naturally communicate." in text
    assert "### Additional example\nScenario:\nCustomer's message:\nMy exact response:\nChannel:\nLanguage:\nSource:\nContext:" in text

    markdown = client.get(f"{API}/template?format=markdown", headers=auth_header("template@example.com"))
    assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.text.startswith("# ListingIQ Personal AI Profile Template")


def test_a_blank_template_is_not_mistaken_for_content():
    contract, summary = parse_document(_docx_body(template_document.docx_template()))
    assert contract is None and summary["unmapped_details"] == 0


def test_completed_word_template_completes_five_stages_locally(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "docx-owner@example.com")
    media = template_document.DOCX_MEDIA_TYPE
    job = _import(client, headers, persona, "michael-kang.docx", _michael_docx(), media)
    assert job["status"] == "completed" and job["extraction_mode"] == "local", job
    assert all(job["stages"][name]["status"] == "completed" for name in STAGES)
    assert job["stages"]["personality_extraction"]["message"].startswith("Read locally without AI")
    proposal = job["proposal"]
    expected, _ = parse_document(DOCUMENT)
    assert [item["preferred_response"] for item in proposal["examples"]] == [item.preferred_response for item in expected.examples]
    assert proposal["examples"][2]["preferred_response"] == KOREAN_OUTREACH
    assert proposal["profile_patch"]["identity"]["licensing_jurisdiction"] == "Illinois"
    assert "license_number" not in proposal["profile_patch"]["identity"]
    assert proposal["claims"][0]["status"] == "self_reported"


# --- Michael Kang: import, review, submit ------------------------------------

def test_michael_kang_markdown_import_review_and_submission(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "mk-flow@example.com")
    pid = persona["id"]
    job = _import(client, headers, persona, "michael-kang.md", DOCUMENT.encode(), "text/markdown")
    assert job["status"] == "completed" and all(job["stages"][name]["status"] == "completed" for name in STAGES)
    proposal, assessment = job["proposal"], job["assessment"]
    assert len(proposal["examples"]) == 11 and proposal["examples"][2]["preferred_response"] == KOREAN_OUTREACH
    assert len(proposal["claims"]) == 6 and len(proposal["unmapped_details"]) == 9
    # The import review lists exactly what submission will require.
    assert assessment["completeness_warnings"] == [
        "License number is missing; add it, or mark it not applicable with a reason, before submitting",
        "License status is not confirmed",
    ]
    assert job["stages"]["completeness_assessment"]["message"].startswith("2 items to complete before submission")

    applied = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    draft = applied.json()
    assert draft["review_status"] == "draft" and draft["full_name"] == "Michael Kang"
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []

    checked = client.post(f"{API}/{pid}/validate", headers=headers).json()
    assert checked["valid"] is False
    assert sorted(checked["errors"]) == sorted([
        "who_i_am.license_number is required or needs a reasoned N/A",
        "who_i_am.licensing_status is required or needs a reasoned N/A",
        "conversation_examples.at least one owner-approved example is required",
    ])
    blocked = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": draft["revision"]})
    assert blocked.status_code == 422

    # The owner resolves the license honestly (N/A with a reason) through visible fields only.
    profile = draft["profile_config"]
    profile["confirmations"].update({"license_number": "not_applicable", "licensing_status": "not_applicable"})
    profile["na_reasons"].update({"license_number": "Number to be added after IDFPR lookup",
                                  "licensing_status": "Confirming with IDFPR"})
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": draft["revision"], "profile_config": {
        "confirmations": profile["confirmations"], "na_reasons": profile["na_reasons"]}})
    assert saved.status_code == 200, saved.text
    after = saved.json()
    # Partial save: imported values the form does not show are untouched.
    assert after["profile_config"]["playbook"] == draft["profile_config"]["playbook"]
    assert after["profile_config"]["communication"]["signature_phrases"] == draft["profile_config"]["communication"]["signature_phrases"]
    assert len(after["profile_config"]["source_review"]["claims"]) == 6

    # Saving the draft approved every example in the same transaction; no per-example step.
    examples = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert len(examples) == 11 and all(item["owner_approved"] for item in examples)

    current = client.get(f"{API}/{pid}/draft", headers=headers).json()
    assert client.post(f"{API}/{pid}/validate", headers=headers).json()["valid"] is True
    submitted = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": current["revision"]})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    # Submission never publishes; the HOB still decides.
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []


# --- Manual setup --------------------------------------------------------------

def test_manual_setup_with_the_simplified_form_can_be_submitted(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "manual@example.com", full_name="Dana Lee")
    pid = persona["id"]
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={
        "revision": draft["revision"], "professional_title": "Associate Broker", "biography": "Twelve years on the North Side.",
        "specialties": ["Condos"], "service_areas": ["Lakeview"], "preferred_language": "en",
        "profile_config": {"identity": {"license_number": "475.123456", "licensing_jurisdiction": "Illinois", "licensing_status": "Active"},
                           "communication": {"other_languages": ["Korean"]}},
    })
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    assert client.post(f"{API}/{pid}/validate", headers=headers).json()["errors"] == [
        "conversation_examples.at least one conversation example is required",
    ]
    # An introduction has no customer message; the owner writes the opening line.
    example = client.post(f"{API}/{pid}/examples", headers=headers, json={
        "revision": revision, "scenario": "Introducing yourself to a new client", "channel": "sms", "language": "en",
        "customer_message": "", "preferred_response": "Hi, Dana Lee with Lee Homes. Happy to help whenever you're ready.",
        "provenance": "authentic", "owner_approved": True,
    })
    assert example.status_code == 201, example.text
    reloaded = client.get(f"{API}/{pid}/draft", headers=headers).json()
    assert reloaded["profile_config"]["identity"]["license_number"] == "475.123456"
    assert reloaded["profile_config"]["communication"]["other_languages"] == ["Korean"]
    result = client.post(f"{API}/{pid}/validate", headers=headers).json()
    assert result["valid"] is True, result
    submitted = client.post(f"{API}/{pid}/submit", headers=headers, json={"revision": reloaded["revision"]})
    assert submitted.status_code == 200, submitted.text


def test_missing_license_still_blocks_submission_without_a_reason():
    profile = {"identity": {"license_number": "", "licensing_jurisdiction": "Illinois", "licensing_status": "Active"},
               "confirmations": {"license_number": "not_applicable"}, "na_reasons": {}}
    draft = type("Draft", (), dict(
        full_name="Dana", professional_title="Broker", brokerage_name="North", preferred_language="en",
        biography="Bio", specialties=["Condos"], service_areas=["Lakeview"], style_config={}, profile_config=profile, revision=1,
    ))()
    example = type("Example", (), {"channel": "sms", "owner_approved": 1, "review_flags": []})()
    result = service.validation_result(draft, [example])
    assert result["errors"] == ["who_i_am.license_number is required or needs a reasoned N/A"]
    profile["na_reasons"] = {"license_number": "Not licensed; assistant to the broker"}
    assert service.validation_result(draft, [example])["valid"] is True


# --- No AI ---------------------------------------------------------------------

def test_ai_interpretation_is_off_even_with_a_configured_provider(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "no-ai@example.com")
    job = _import(client, headers, persona, "notes.txt",
                  b"My persona: a calm real estate broker. Communication tone: warm. Conversation tips: listen first.", "text/plain")
    assert job["status"] == "failed" and job["error_code"] == imports.TEMPLATE_NOT_RECOGNIZED and job["retryable"] is False
    response = client.post(f"{API}/{persona['id']}/imports/{job['id']}/ai-interpretation", headers=headers)
    assert response.status_code == 409 and "turned off" in response.text
    assert client.get(f"{API}/{persona['id']}/imports/{job['id']}", headers=headers).json()["extraction_mode"] == "local"


def test_a_job_queued_for_ai_is_read_locally_when_ai_is_off(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "queued-ai@example.com")
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("michael-kang.md", DOCUMENT.encode(), "text/markdown")}).json()
    with db.SessionLocal() as session:
        session.get(PersonaImportJob, uploaded["id"]).extraction_mode = imports.AI_MODE
        session.commit()
        assert imports.process_one(session) is True
    job = client.get(f"{API}/{persona['id']}/imports/{uploaded['id']}", headers=headers).json()
    assert job["status"] == "completed" and job["extraction_mode"] == "local"
    assert len(job["proposal"]["examples"]) == 11


def test_pdf_import_is_local_and_flags_example_formatting(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "pdf@example.com")
    pdf = fitz.open()
    lines = DOCUMENT.replace("마이클 강", "Michael").splitlines()
    for start in range(0, len(lines), 55):
        page = pdf.new_page()
        for offset, line in enumerate(lines[start:start + 55]):
            if not any("가" <= ch <= "힣" for ch in line):
                page.insert_text((36, 40 + 13 * offset), line.replace("**", "").replace("> ", ""), fontsize=7)
    job = _import(client, headers, persona, "michael-kang.pdf", pdf.tobytes(), "application/pdf")
    assert job["status"] == "completed", job
    assert all(imports.PDF_REVIEW_FLAG in item["review_flags"] for item in job["proposal"]["examples"])
    assert any("read as plain text" in item for item in job["assessment"]["missing_questions"])


# --- Parser safeguards ---------------------------------------------------------

def _with_examples(block: str):
    return parse_document(DOCUMENT.split("## Conversation Examples")[0] + "## Conversation Examples\n\n" + block)[0]


def test_numbered_list_inside_a_reply_stays_in_that_reply():
    contract = _with_examples(
        "**1. Listing plan**\n- Scenario: seller asks · Channel: Email · Language: English\n"
        "- Customer message: \"What happens next?\"\n- Preferred response: Here is the plan:\n"
        "1. We price it with real comps.\n2. We launch it like an asset.\nTalk *soon*.\n- Status: authentic\n"
    )
    assert len(contract.examples) == 1
    assert contract.examples[0].preferred_response == (
        "Here is the plan:\n1. We price it with real comps.\n2. We launch it like an asset.\nTalk soon."
    )


def test_missing_example_metadata_is_flagged_not_assumed():
    contract = _with_examples("**1. Short reply**\n- Customer message: \"Hi\"\n- Preferred response: \"Hello there.\"\n")
    example = contract.examples[0]
    assert example.provenance == "illustrative"
    assert any("Channel was not stated" in flag for flag in example.review_flags)
    assert any("Language was not stated" in flag for flag in example.review_flags)
    assert any("authentic or illustrative" in flag for flag in example.review_flags)


def test_rejected_example_keeps_all_its_text():
    contract = _with_examples("**1. No reply yet**\n- Scenario: test · Channel: SMS\n- Context: owner is engaged\nExtra note line.\n")
    assert contract.examples == []
    kept = " ".join(item.detail for item in contract.unmapped_details)
    assert "owner is engaged" in kept and "Extra note line." in kept
    assert any("has no preferred response" in question for question in contract.missing_questions)


def test_overflowing_details_are_announced_not_dropped():
    extra = "\n".join(f"- Extra detail number {index}: value {index}" for index in range(150))
    contract, _ = parse_document(DOCUMENT + "\n## Office Notes\n" + extra + "\n")
    assert len(contract.unmapped_details) == 100
    assert contract.unmapped_details[-1].section == "Import notice"
    assert "could not be listed here" in contract.unmapped_details[-1].detail
    assert any("too many to list" in question for question in contract.missing_questions)


def test_document_size_cap_is_reported():
    class Job:
        file_name = "long.md"
        document_bytes = (DOCUMENT + "\n" + "x" * (imports.MAX_DOCUMENT_CHARACTERS + 10)).encode()
    text, notes = imports._read_document(Job())
    assert len(text) == imports.MAX_DOCUMENT_CHARACTERS
    assert notes and "were read" in notes[0]


# --- Owner-reviewed apply (auto-filled editable form) ---------------------------

def _reviewed_payload(job, draft, **changes):
    """What the auto-filled form sends: visible fields, all examples, review decisions."""
    proposal = job["proposal"]
    examples = [{
        "source_index": index, "scenario": item["scenario"], "channel": item["channel"], "language": item["language"],
        "customer_message": item["customer_message"], "preferred_response": item["preferred_response"],
        "provenance": item["provenance"], "owner_approved": False,
    } for index, item in enumerate(proposal["examples"])]
    identity = proposal["profile_patch"]["identity"]
    reviewed = {
        "draft": {key: proposal["draft_patch"].get(key, draft[key]) for key in ("full_name", "professional_title", "biography", "specialties", "service_areas", "preferred_language")},
        "profile": {"identity": {key: identity.get(key, "") for key in ("license_number", "licensing_jurisdiction", "licensing_status", "public_email", "public_phone", "professional_intro")},
                    "communication": {"other_languages": proposal["profile_patch"].get("communication", {}).get("other_languages", [])},
                    "confirmations": proposal["profile_patch"].get("confirmations", {}), "na_reasons": {}},
        "examples": examples,
        "source_review": {},
    }
    reviewed.update(changes)
    return {"revision": draft["revision"], "reviewed": reviewed}


def _imported_job(client, make_user, auth_header, email):
    headers, persona = _owner(client, make_user, auth_header, email)
    job = _import(client, headers, persona, "michael-kang.md", DOCUMENT.encode(), "text/markdown")
    assert job["status"] == "completed"
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    return headers, persona, job, draft


def test_owner_edits_are_what_apply_saves(client, make_user, auth_header):
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "reviewed@example.com")
    pid = persona["id"]
    payload = _reviewed_payload(job, draft)
    reviewed = payload["reviewed"]
    reviewed["draft"]["professional_title"] = "Founder, SpaceMatch Inc."
    reviewed["examples"][3]["preferred_response"] = "Edited by Michael: let me pull the real numbers first."
    reviewed["examples"][3]["owner_approved"] = True
    del reviewed["examples"][7]  # removed from the working proposal
    reviewed["examples"].append({"scenario": "Upset customer", "channel": "sms", "language": "en", "customer_message": "Nobody called me back.",
                                 "preferred_response": "I'm sorry. I'll call you myself within the hour.", "provenance": "authentic", "owner_approved": False})
    detail = job["proposal"]["unmapped_details"][0]
    reviewed["source_review"] = {"unmapped_details": [{**detail, "owner_status": "dismissed"}]}

    # Nothing is written before Apply.
    assert client.get(f"{API}/{pid}/draft", headers=headers).json()["revision"] == draft["revision"]
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 0

    applied = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=payload)
    assert applied.status_code == 200, applied.text
    saved = applied.json()
    assert saved["professional_title"] == "Founder, SpaceMatch Inc." and saved["review_status"] == "draft"
    # Missing stays missing; nothing is marked not applicable for the owner.
    assert saved["profile_config"]["identity"]["license_number"] == ""
    assert "license_number" not in saved["profile_config"]["na_reasons"]
    # Imported information without a visible field is kept.
    assert len(saved["profile_config"]["playbook"]["follow_up"]) > 10
    assert saved["profile_config"]["communication"]["signature_phrases"]
    review = saved["profile_config"]["source_review"]
    assert len(review["claims"]) == 6 and len(review["unmapped_details"]) == 9
    assert [item["owner_status"] for item in review["unmapped_details"]].count("dismissed") == 1

    rows = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    responses = [row["preferred_response"] for row in rows]
    assert len(rows) == 11  # 11 imported - 1 removed + 1 added
    assert "Edited by Michael: let me pull the real numbers first." in responses
    assert not any("Totally understand, {FirstName}" in text for text in responses)
    assert KOREAN_OUTREACH in responses
    # Apply & Save Draft approves every example the owner kept.
    assert all(row["owner_approved"] for row in rows)
    korean = next(row for row in rows if row["preferred_response"] == KOREAN_OUTREACH)
    assert korean["provenance"] == "illustrative" and korean["review_flags"]  # audit metadata kept
    # The extracted original stays on the import job.
    original = client.get(f"{API}/{pid}/imports/{job['id']}", headers=headers).json()["proposal"]["examples"][3]["preferred_response"]
    assert original.startswith("Good question. I don't want to throw out a guess.")

    # Same request again: idempotent, no duplicates. A different request: conflict.
    again = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=payload)
    assert again.status_code == 200 and again.json()["revision"] == saved["revision"]
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 11
    payload["reviewed"]["draft"]["professional_title"] = "Something else"
    assert client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=payload).status_code == 409
    # Nothing was submitted or published.
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []


def test_saved_values_are_not_silently_overwritten_by_an_import(client, make_user, auth_header):
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "conflict@example.com")
    pid = persona["id"]
    # The owner saved a playbook answer before applying; Apply must keep it.
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={
        "revision": draft["revision"], "profile_config": {"playbook": {"follow_up": "My own follow-up rule."}}}).json()
    # The import was prepared for the older revision, so it is refused until retried.
    stale = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=_reviewed_payload(job, draft))
    assert stale.status_code == 409
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 0
    with db.SessionLocal() as session:
        row = session.get(PersonaImportJob, job["id"])
        row.base_revision = saved["revision"]  # as a retry would
        session.commit()
    applied = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=_reviewed_payload(job, saved))
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["profile_config"]["playbook"]["follow_up"] == "My own follow-up rule."
    assert any(note.startswith("Kept your saved follow up") for note in result["profile_config"]["source_review"]["contradictions"])
    assert result["profile_config"]["playbook"]["objections"]  # empty saved values are filled


def test_autofill_apply_and_self_publish_is_active_persistent_and_idempotent(client, make_user, auth_header):
    """Disposable, offline proof of the exact demo path through POST /versions."""
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "publish-demo@example.com")
    pid = persona["id"]
    payload = _reviewed_payload(job, draft)
    # Explicit test-only credentials: supplied by the disposable owner, never inferred or auto-marked N/A.
    payload["reviewed"]["profile"]["identity"]["license_number"] = "TEST-475.000001"
    payload["reviewed"]["profile"]["identity"]["licensing_status"] = "Active"
    payload["reviewed"]["profile"]["confirmations"].update({
        "license_number": "owner_confirmed", "licensing_status": "owner_confirmed",
    })

    applied = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=payload)
    assert applied.status_code == 200, applied.text
    revision = applied.json()["revision"]
    examples = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert examples and all(row["owner_approved"] for row in examples)
    readiness = client.post(f"{API}/{pid}/validate", headers=headers).json()
    assert readiness["valid"] is True and readiness["revision"] == revision and readiness["errors"] == []

    first = client.post(f"{API}/{pid}/versions", headers=headers, json={"revision": revision})
    assert first.status_code == 201, first.text
    version = first.json()
    refreshed = client.get(f"{API}/{pid}", headers=headers).json()
    assert refreshed["active_version_id"] == version["id"] and refreshed["active_version_number"] == 1
    assert client.get(f"{API}/{pid}/draft", headers=headers).json()["review_status"] == "published"

    again = client.post(f"{API}/{pid}/versions", headers=headers, json={"revision": revision})
    assert again.status_code == 200 and again.json()["id"] == version["id"]
    with db.SessionLocal() as session:
        assert session.query(PersonaVersion).filter_by(persona_id=pid).count() == 1


def test_an_invalid_reviewed_example_writes_nothing(client, make_user, auth_header):
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "atomic@example.com")
    payload = _reviewed_payload(job, draft)
    payload["reviewed"]["examples"][0]["preferred_response"] = "   "
    refused = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json=payload)
    assert refused.status_code == 422
    assert client.get(f"{API}/{persona['id']}/draft", headers=headers).json()["revision"] == draft["revision"]
    assert client.get(f"{API}/{persona['id']}/examples", headers=headers).json()["total"] == 0
    assert client.get(f"{API}/{persona['id']}/imports/{job['id']}", headers=headers).json()["status"] == "completed"


# --- Downloaded Word template: the setup form's questions round trip ----------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _paragraph_text(paragraph) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{W}t"))


def _set_answer(paragraph, value: str):
    run = ElementTree.SubElement(paragraph, f"{W}r")
    text = ElementTree.SubElement(run, f"{W}t")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = value


def _plain_paragraph(value: str):
    paragraph = ElementTree.Element(f"{W}p")
    _set_answer(paragraph, value)
    return paragraph


def _fill_downloaded(docx: bytes, responses: dict[int, str], additional: list[dict[str, str]]) -> bytes:
    """Fill the downloaded file the way a person would in Word.

    Answers are typed after each label (Enter starts a new paragraph), and the
    blank Additional example block is copied once per extra example. The
    original blank block is left in place, as people often do.
    """
    import copy
    ElementTree.register_namespace("w", W[1:-1])
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = ElementTree.fromstring(parts["word/document.xml"])
    body = root.find(f"{W}body")

    def answer(label_paragraph, value):
        first, *rest = value.split("\n")
        _set_answer(label_paragraph, first)
        position = list(body).index(label_paragraph)
        for offset, line in enumerate(rest, start=1):
            body.insert(position + offset, _plain_paragraph(line))

    children = list(body)
    for number, value in responses.items():
        heading = next(p for p in children if _paragraph_text(p).startswith(f"{number:02d} — "))
        label = children[children.index(heading) + 1]
        assert _paragraph_text(label).startswith("Response:")
        answer(label, value)
    children = list(body)
    start = next(i for i, p in enumerate(children) if _paragraph_text(p) == "Additional example")
    block = children[start:start + 1 + len(template_document.ADDITIONAL_FIELDS)]
    position = start
    for values in additional:
        copies = [copy.deepcopy(paragraph) for paragraph in block]
        for paragraph in copies:
            body.insert(position, paragraph)
            position += 1
        for paragraph in copies[1:]:
            name = _paragraph_text(paragraph).split(":", 1)[0]
            if values.get(name):
                answer(paragraph, values[name])
        position = list(body).index(copies[-1]) + 1
    parts["word/document.xml"] = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


ANSWERS = {
    1: "Hi! Dana Lee here — Lee Homes, North Side. 😊",
    2: "Great question… let me pull 3 recent sales first:\n1. Same block\n2. Same size\nThen I'll text you a range, OK?",
    3: "No rush at all. Want me to check back in a month?",
    4: "Totally fair. Here's what that fee covers — pricing, marketing & a buyer who can close.",
    5: "Hey Sam, just bumping this up :) still thinking about selling?",
    6: "I'm so sorry!! I'll call you myself in 10 minutes.",
    7: "안녕하세요 사장님! 내일 오후 2시 괜찮으세요?",
}
EXTRA = [
    {"Scenario": "Price drop alert", "Customer's message": "Any news?", "My exact response": "Yes — 2 new listings near you.",
     "Channel": "SMS", "Language": "English", "Source": "authentic"},
    {"Scenario": "Email recap after a showing", "My exact response": "Hi Sam,\n\nThanks for today. Notes attached.\n\nDana",
     "Channel": "Email", "Language": "English", "Source": "illustrative", "Context": "after a showing"},
    {"Scenario": "Spanish-speaking buyer", "Customer's message": "¿Sigue disponible?", "My exact response": "¡Sí! ¿Le gustaría verla mañana?",
     "Channel": "SMS", "Language": "Spanish", "Source": "illustrative"},
]
SCENARIOS = [scenario for _question, scenario, _reply in template_document.COMMUNICATION_QUESTIONS]


def test_downloaded_template_round_trip_fills_the_editable_form(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "round-trip@example.com", full_name="Dana Lee")
    pid = persona["id"]
    downloaded = client.get(f"{API}/template", headers=headers)
    assert downloaded.status_code == 200 and downloaded.headers["content-type"] == template_document.DOCX_MEDIA_TYPE

    # A blank download is not a completed Persona.
    blank = _import(client, headers, persona, "blank.docx", downloaded.content, template_document.DOCX_MEDIA_TYPE)
    assert blank["status"] == "failed" and blank["error_code"] == imports.TEMPLATE_NOT_RECOGNIZED

    answers = {"Full name and preferred display name": "Dana Lee", "Professional role and title": "Associate Broker, Lee Homes",
               "Number": "475.123456", "Jurisdiction": "Illinois", "Status": "Active",
               "Biography": "Twelve years on the North Side.", "Specialties": "condos, two-flats", "Service areas": "Lakeview, Uptown"}
    # Identity answers come from the same generator; the conversation answers are typed into the downloaded file.
    with_identity = template_document.docx_template(answers)
    filled = _fill_downloaded(with_identity, ANSWERS, EXTRA)
    assert _docx_body(downloaded.content) == _docx_body(template_document.authoritative_docx())
    job = _import(client, headers, persona, "dana.docx", filled, template_document.DOCX_MEDIA_TYPE)
    assert job["status"] == "completed", job
    assert all(job["stages"][name]["status"] == "completed" for name in STAGES)
    examples = job["proposal"]["examples"]
    # 7 answered questions + 3 additional examples; the blank leftover block is not imported.
    assert len(examples) == 10
    assert [item["scenario"] for item in examples] == SCENARIOS + [item["Scenario"] for item in EXTRA]
    assert [item["preferred_response"] for item in examples] == [ANSWERS[n] for n in range(1, 8)] + [item["My exact response"] for item in EXTRA]
    assert not any(item["preferred_response"] in ("", "Response:") or "How do you" in item["preferred_response"] for item in examples)
    by_scenario = {item["scenario"]: item for item in examples}
    assert by_scenario["Suggesting an appointment"]["language"] == "ko"
    assert any("Confirm the language" in flag for flag in by_scenario["Suggesting an appointment"]["review_flags"])
    assert any("Channel was not stated" in flag for flag in by_scenario["Upset customer"]["review_flags"])
    # Replies to a customer are not mistaken for outreach; introductions and follow-ups are.
    assert not any("First-touch" in flag for flag in by_scenario["Upset customer"]["review_flags"])
    assert any("First-touch" in flag for flag in by_scenario["Introducing myself to a new client"]["review_flags"])
    assert by_scenario["Price drop alert"]["customer_message"] == "Any news?"
    assert by_scenario["Price drop alert"]["provenance"] == "user_written"  # "authentic" waits for the owner
    assert by_scenario["Email recap after a showing"]["channel"] == "email"
    assert by_scenario["Spanish-speaking buyer"]["language"] == "es"
    assert job["proposal"]["unmapped_details"] == []

    # Apply what the owner reviewed in the auto-filled form, including an edit.
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    payload = _reviewed_payload(job, draft)
    payload["reviewed"]["examples"][2]["preferred_response"] = "No rush at all! Want me to check back next month?"
    applied = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json=payload)
    assert applied.status_code == 200, applied.text
    rows = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert len(rows) == 10 and all(row["owner_approved"] for row in rows)
    saved = {row["scenario"]: row["preferred_response"] for row in rows}
    assert saved["Interested but hesitant"] == "No rush at all! Want me to check back next month?"
    assert saved["Property value question"] == ANSWERS[2]
    assert saved["Suggesting an appointment"] == ANSWERS[7]
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []


def test_template_questions_match_the_setup_form():
    """The Word template and the How I Communicate step ask the same questions under the same scenario names."""
    form = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "personas" / "components" / "persona-view.tsx"
    if not form.exists():
        pytest.skip("frontend source not available")
    source = form.read_text(encoding="utf-8")
    for question, scenario, _reply in template_document.COMMUNICATION_QUESTIONS:
        assert f'scenario: "{scenario}", question: "{question}"' in source
    assert template_document.ADDITIONAL_QUESTION in source



# --- Owner approval comes from the owner's save --------------------------------

def test_owner_saves_approve_examples_without_a_separate_step(client, make_user, auth_header):
    headers, persona = _owner(client, make_user, auth_header, "save-approves@example.com", full_name="Dana Lee")
    pid = persona["id"]
    draft = client.get(f"{API}/{pid}/draft", headers=headers).json()
    created = client.post(f"{API}/{pid}/examples", headers=headers, json={
        "revision": draft["revision"], "scenario": "Upset customer", "channel": "sms", "language": "ko",
        "customer_message": "", "preferred_response": "정말 죄송합니다!! 제가 바로 전화드릴게요 :)", "provenance": "illustrative",
        "owner_approved": False,
    })
    assert created.status_code == 201 and created.json()["owner_approved"] is True
    assert created.json()["preferred_response"] == "정말 죄송합니다!! 제가 바로 전화드릴게요 :)"
    edited = client.patch(f"{API}/{pid}/examples/{created.json()['id']}", headers=headers, json={
        "revision": created.json()["revision"], "preferred_response": "Line one.\n\nLine two — with a dash."})
    assert edited.status_code == 200 and edited.json()["owner_approved"] is True
    assert edited.json()["preferred_response"] == "Line one.\n\nLine two — with a dash."
    assert edited.json()["provenance"] == "illustrative"


def test_save_draft_approves_imported_examples_atomically(client, make_user, auth_header):
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "legacy-approve@example.com")
    pid = persona["id"]
    # A proposal that was never applied approves nothing.
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 0
    legacy = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json={"revision": draft["revision"]}).json()
    rows = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert len(rows) == 11 and not any(row["owner_approved"] for row in rows)

    # A stale save fails and approves nothing.
    stale = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": legacy["revision"] - 1, "full_name": "Michael Kang"})
    assert stale.status_code == 409
    assert not any(row["owner_approved"] for row in client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"])

    # Saving the form, even with no field changes, approves the examples and moves the revision.
    saved = client.patch(f"{API}/{pid}/draft", headers=headers, json={"revision": legacy["revision"], "full_name": legacy["full_name"]})
    assert saved.status_code == 200 and saved.json()["revision"] == legacy["revision"] + 1
    rows = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert len(rows) == 11 and all(row["owner_approved"] for row in rows)
    assert {row["provenance"] for row in rows} == {"illustrative"} and sum(bool(row["review_flags"]) for row in rows) == 4
    # Nothing was submitted or published.
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []
    assert client.get(f"{API}/{pid}/versions", headers=headers).json() == []


def test_how_i_communicate_saves_all_examples_in_one_step(client, make_user, auth_header):
    headers, persona, job, draft = _imported_job(client, make_user, auth_header, "batch@example.com")
    pid = persona["id"]
    legacy = client.post(f"{API}/{pid}/imports/{job['id']}/apply", headers=headers, json={"revision": draft["revision"]}).json()
    rows = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    korean = next(row for row in rows if row["preferred_response"] == KOREAN_OUTREACH)
    items = [{key: row[key] for key in ("id", "scenario", "channel", "language", "customer_message", "preferred_response")} for row in rows]
    items[3]["preferred_response"] = "Edited — keep it short.\n\nSecond paragraph 😊"
    del items[7]
    items.append({"scenario": "Upset customer", "channel": "sms", "language": "ko", "customer_message": "",
                  "preferred_response": "정말 죄송합니다! 바로 전화드릴게요."})
    items.append(dict(items[-1]))  # an exact repeat is stored once

    stale = client.put(f"{API}/{pid}/examples", headers=headers, json={"revision": legacy["revision"] - 1, "examples": items})
    assert stale.status_code == 409
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 11

    saved = client.put(f"{API}/{pid}/examples", headers=headers, json={"revision": legacy["revision"], "examples": items})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == legacy["revision"] + 1 and body["total"] == 11
    stored = client.get(f"{API}/{pid}/examples?page_size=100", headers=headers).json()["items"]
    assert len(stored) == 11 and all(row["owner_approved"] for row in stored)
    responses = [row["preferred_response"] for row in stored]
    assert "Edited — keep it short.\n\nSecond paragraph 😊" in responses
    assert rows[7]["preferred_response"] not in responses
    kept_korean = next(row for row in stored if row["id"] == korean["id"])
    assert kept_korean["preferred_response"] == KOREAN_OUTREACH and kept_korean["review_flags"] == korean["review_flags"]
    assert kept_korean["provenance"] == "illustrative"
    added = next(row for row in stored if row["preferred_response"] == "정말 죄송합니다! 바로 전화드릴게요.")
    assert added["provenance"] == "user_written" and added["review_flags"] == []

    foreign = client.put(f"{API}/{pid}/examples", headers=headers, json={"revision": body["revision"], "examples": [{**items[0], "id": 999999}]})
    assert foreign.status_code == 404
    assert client.get(f"{API}/{pid}/examples", headers=headers).json()["total"] == 11
    assert client.get(f"{API}/{pid}/submissions", headers=headers).json() == []
