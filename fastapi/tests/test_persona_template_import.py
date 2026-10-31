"""Deterministic (no AI) import of ListingIQ template documents.

The primary fixture is the owner's own Michael Kang Persona document. Any
attempt to reach an AI provider fails these tests.
"""

import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import fitz
import pytest
import requests

from app import db
from app.personas import imports
from app.personas.models import PersonaDraftExample, PersonaSubmission, PersonaVersion
from app.personas.template_parser import parse_document

API = "/api/v1/personas"
FIXTURE = Path(__file__).parent / "fixtures" / "persona" / "michael-kang.md"
DOCUMENT = FIXTURE.read_text(encoding="utf-8")
KOREAN_OUTREACH = ("안녕하세요 {FirstName} 사장님, SpaceMatch의 마이클 강입니다. 시카고에서 세탁소·비즈니스 매매를 많이 진행하고 "
                   "있습니다. 혹시 은퇴나 매각 생각 있으시면 편하게 상담 한번 드리겠습니다.")
FOLLOW_UP_EMAIL = (
    "Subject: {StreetAddress} — real numbers\n\nHi {FirstName},\n\nThanks for getting back to me. I'll put together recent "
    "sales and active listings near {StreetAddress} so you can see a realistic range, with no guesswork.\n\nWould a quick "
    "10-minute call Thursday or Friday work to walk through it?\n\nMichael Kang | Founder & Team Lead, SpaceMatch Inc. | (773) 344-0738"
)


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("an AI provider must not be called")
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(imports, "extract_document", refuse)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _source_examples():
    """Preferred responses as written in the document, by example number."""
    blocks = re.split(r"\n\*\*\d+\. [^*]+\*\*\n", DOCUMENT.split("## Conversation Examples", 1)[1])[1:]
    responses = []
    for body in blocks:
        inline = re.search(r'- Preferred response: "(.*)"\n', body)
        if inline:
            responses.append(inline.group(1))
        else:
            quoted = body.split("- Preferred response:")[1].split("- Context:")[0]
            responses.append("\n".join(line.strip()[2:] if line.strip().startswith("> ") else "" for line in quoted.strip().splitlines()))
    return responses


def _persona(client, make_user, auth_header, email):
    make_user(email, role="broker")
    headers = auth_header(email)
    created = client.post(API, headers=headers, json={"full_name": "Michael Kang"})
    assert created.status_code == 201, created.text
    return headers, created.json()


def _import(client, headers, persona, name="michael-kang.md", content=DOCUMENT.encode(), media="text/markdown"):
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers, files={"file": (name, content, media)})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    return client.get(f"{API}/{persona['id']}/imports/{uploaded.json()['id']}", headers=headers).json()


def test_michael_kang_template_is_read_locally_with_exact_examples(client, make_user, auth_header):
    headers, persona = _persona(client, make_user, auth_header, "mk-local@example.com")
    job = _import(client, headers, persona)
    assert job["status"] == "completed", job
    assert job["extraction_mode"] == "local"
    assert [job["stages"][name]["status"] for name in imports.STAGES] == ["completed"] * 5
    assert job["stages"]["personality_extraction"]["message"].startswith("Read locally without AI")

    proposal, assessment = job["proposal"], job["assessment"]
    examples = proposal["examples"]
    assert len(examples) == 11
    assert [item["preferred_response"] for item in examples] == _source_examples()
    assert examples[2]["preferred_response"] == KOREAN_OUTREACH and examples[2]["language"] == "ko"
    assert examples[9]["preferred_response"] == FOLLOW_UP_EMAIL and examples[9]["channel"] == "email"
    assert {item["provenance"] for item in examples} == {"illustrative"}
    assert [item["customer_message"] for item in examples[:3]] == ["", "", ""]
    assert examples[3]["customer_message"] == "What do you think it's worth?"
    assert all(imports.OUTREACH_REVIEW_FLAG in item["review_flags"] for item in examples[:3])
    assert any("several channels" in flag for flag in examples[5]["review_flags"])

    draft, style, profile = proposal["draft_patch"], proposal["style_patch"], proposal["profile_patch"]
    assert draft["full_name"] == "Michael Kang" and draft["display_name"] == "Michael Kang"
    assert draft["professional_title"] == "Founder & Team Lead, SpaceMatch Inc. (SpaceMatch Chicago real estate team)"
    assert draft["professional_role"] == "Licensed Illinois real estate broker"
    assert draft["preferred_language"] == "en"
    assert "coin laundries/laundromats" in draft["specialties"]
    assert draft["service_areas"][0] == "Chicago and suburbs (Cook, DuPage, Lake, Kane and nearby counties)"
    assert draft["biography"].startswith("Michael's background is computer science and IT")
    identity = profile["identity"]
    assert identity["licensing_jurisdiction"] == "Illinois"
    assert identity["public_email"] == "michael@spacematchinc.com" and identity["public_phone"] == "(773) 344-0738"
    assert identity["professional_intro"] == "Michael Kang with SpaceMatch Inc. in Chicago."
    assert "license_number" not in identity and "licensing_status" not in identity
    background = profile["background"]
    assert "Korean-speaking owners" in background["target_audiences"]
    assert background["professional_philosophy"].startswith("Treat every property or business as an asset")
    communication = profile["communication"]
    assert style["personality"].startswith("Practical, confident and numbers-first")
    assert "Let's look at the real numbers first." in communication["signature_phrases"]
    assert "We package the whole thing: building, business and income. That's how it gets financed." in communication["signature_phrases"]
    assert "Pressure tactics" in style["avoided_phrases"]
    assert communication["other_languages"] == ["Korean (fluent)"]
    assert "한국어로 편하게 말씀하셔도 됩니다." in communication["language_switching"]
    assert "사장님" in communication["language_switching"]
    assert communication["uncertainty_behavior"].startswith('"I don\'t want to guess.')
    assert communication["questions_per_message"] == 1
    assert set(profile["playbook"]) == {
        "introduction", "trust_building", "qualifying_questions", "valuation_conversations", "pricing_disagreements",
        "objections", "follow_up", "appointments", "sensitive_escalation", "human_handoff",
    }
    assert "divorce, estate or distress situations" in profile["playbook"]["sensitive_escalation"]
    assert profile["playbook"]["human_handoff"].startswith('"Let me have Michael call you.')

    # All self-reported claims keep their status and wording.
    claims = proposal["claims"]
    # The four self-reported claims keep their status; achievement-like
    # statements the document did not label are kept as unverified claims.
    assert [claim["status"] for claim in claims] == ["self_reported"] * 4 + ["unverified"] * 2
    assert {claim["statement"] for claim in claims[4:]} == {
        "He grew from a solo agent into the leader of a 23-agent team.",
        "The team is positioned as bigger than a typical local team and focused on sellers, investors, developers and business owners.",
    }
    text = " ".join(claim["statement"] for claim in claims)
    for fact in ("120 laundromats", "~80% of the listed Chicago coin laundry market", "23 agents",
                 "top-ranked team in Illinois", "35K followers", "refer him to one another"):
        assert fact in text, fact
    assert "Self-reported: " + claims[0]["statement"] in assessment["unsupported_claims"]

    # Missing and ambiguous information becomes a warning or question, never a guess.
    assert "License number is missing; add it, or mark it not applicable with a reason, before submitting" in assessment["completeness_warnings"]
    assert "Add the IDFPR license number and confirm active status before uploading." in assessment["missing_questions"]
    # The setup form has no tone sliders, so the import does not ask for them.
    assert not any("tone sliders" in question for question in assessment["missing_questions"])
    assert "directness" not in style and "warmth" not in style
    details = " ".join(item["detail"] for item in proposal["unmapped_details"])
    for kept in ("마이클 강", "Real Brokerage (Real Broker LLC)", "Coldwell Banker Realty", "(773) 985-7153",
                 "@spacematchchicago", "676 N. Michigan Ave.", "AI transparency", "Also does business brokerage."):
        assert kept in details, kept
    assert proposal["evidence"] and all(item["source_excerpt"] for item in proposal["evidence"])


def _fragments(line: str) -> list[str]:
    body = re.sub(r"^\s*(?:>\s?)?(?:[-*•]|\d+\.)?\s*", "", line)
    body = re.sub(r"\*\*|(?<![\w*])\*|\*(?![\w*])", "", body)
    # Inline "Label: value" segments (example metadata) are checked by value.
    body = " , ".join(re.sub(r"^[^:\"“]{1,100}:\s*", "", segment.strip()) for segment in body.split("·"))
    pieces = []
    for piece in re.split(r"[,;]|\s—\s|\.\s|\band\b|\bplus\b", body):
        piece = piece.strip(" .\"“”()'")
        if len(re.findall(r"\w+", piece)) >= 2:
            pieces.append(piece)
    return pieces


def test_no_document_content_is_silently_dropped(client, make_user, auth_header):
    """Every substantive fragment of the document appears somewhere the owner will see it."""
    contract, _summary = parse_document(DOCUMENT)
    seen = _norm(json.dumps(contract.dict(), ensure_ascii=False).replace("\\n", " ").replace('\\"', '"'))
    template_labels = {"Full name and preferred display name", "Professional role and title"}
    missing = []
    for line in DOCUMENT.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        for fragment in _fragments(line):
            if fragment in template_labels:
                continue
            if _norm(fragment) not in seen:
                missing.append(fragment)
    assert missing == []


def test_apply_keeps_review_material_and_nothing_is_approved(client, make_user, auth_header):
    headers, persona = _persona(client, make_user, auth_header, "mk-apply@example.com")
    job = _import(client, headers, persona)
    applied = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    draft = applied.json()
    assert draft["review_status"] == "draft"
    review = draft["profile_config"]["source_review"]
    assert len(review["claims"]) == 6 and [c["source_status"] for c in review["claims"]].count("self_reported") == 4
    assert len(review["unmapped_details"]) == len(job["proposal"]["unmapped_details"])
    assert len(review["evidence"]) == len(job["proposal"]["evidence"])
    with db.SessionLocal() as session:
        rows = session.query(PersonaDraftExample).filter_by(persona_id=persona["id"]).order_by(PersonaDraftExample.position).all()
        assert len(rows) == 11 and not any(row.owner_approved for row in rows)
        assert [row.preferred_response for row in rows] == _source_examples()
        assert [bool(row.review_flags) for row in rows][:3] == [True, True, True]
        assert session.query(PersonaSubmission).filter_by(persona_id=persona["id"]).count() == 0
        assert session.query(PersonaVersion).filter_by(persona_id=persona["id"]).count() == 0


def _docx(markdown: str) -> bytes:
    """A Word document with real heading styles, nested list numbering and plain paragraphs."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraphs = []
    for line in markdown.splitlines():
        heading = re.match(r"^(#+)\s+(.*)$", line)
        bullet = re.match(r"^(\s*)(?:-|\d+\.)\s+(.*)$", line)
        quote = re.match(r"^\s*>\s?(.*)$", line)
        text = (heading or bullet or quote).groups()[-1] if (heading or bullet or quote) else line
        text = text.replace("**", "")
        if heading:
            props = f'<w:pPr><w:pStyle w:val="Heading{len(heading.group(1))}"/></w:pPr>'
        elif bullet:
            level = len(bullet.group(1)) // 2
            props = f'<w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        else:
            props = ""
        if quote:
            text = "> " + text
        paragraphs.append(f'<w:p>{props}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
    body = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{"".join(paragraphs)}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def test_docx_template_keeps_structure_and_korean(client, make_user, auth_header):
    headers, persona = _persona(client, make_user, auth_header, "mk-docx@example.com")
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    job = _import(client, headers, persona, "michael-kang.docx", _docx(DOCUMENT), media)
    assert job["status"] == "completed", job
    markdown_contract, _ = parse_document(DOCUMENT)
    examples = job["proposal"]["examples"]
    assert len(examples) == 11
    assert [item["preferred_response"] for item in examples] == _source_examples()
    assert examples[2]["preferred_response"] == KOREAN_OUTREACH
    assert job["proposal"]["draft_patch"] == markdown_contract.draft
    assert job["proposal"]["profile_patch"]["playbook"] == markdown_contract.profile_config["playbook"]
    assert len(job["proposal"]["claims"]) == 6


def test_pdf_template_without_indentation_is_read(client, make_user, auth_header):
    english = re.sub(r"[ᄀ-ᇿ㄰-㆏가-힯]+", "Korean", DOCUMENT)
    pdf = fitz.open()
    lines = english.splitlines()
    for start in range(0, len(lines), 55):
        page = pdf.new_page()
        y = 40
        for line in lines[start:start + 55]:
            page.insert_text((36, y), line.replace("**", "").replace("> ", ""), fontsize=7)
            y += 13
    headers, persona = _persona(client, make_user, auth_header, "mk-pdf@example.com")
    job = _import(client, headers, persona, "michael-kang.pdf", pdf.tobytes(), "application/pdf")
    assert job["status"] == "completed", job
    proposal = job["proposal"]
    assert len(proposal["examples"]) == 11
    assert proposal["draft_patch"]["full_name"] == "Michael Kang"
    assert proposal["profile_patch"]["identity"]["licensing_jurisdiction"] == "Illinois"
    assert len(proposal["profile_patch"]["playbook"]) == 10
    assert [claim["status"] for claim in proposal["claims"]].count("self_reported") == 4


def test_unrecognized_document_offers_explicit_ai_path(client, make_user, auth_header, monkeypatch):
    headers, persona = _persona(client, make_user, auth_header, "free-form@example.com")
    free_form = (b"My persona: I am a real estate broker with a calm communication style. I like short SMS follow-up "
                 b"messages and I explain valuation clearly. My tone is warm. Conversation tips: listen first.")
    job = _import(client, headers, persona, "notes.txt", free_form, "text/plain")
    assert job["status"] == "failed" and job["error_code"] == imports.TEMPLATE_NOT_RECOGNIZED
    assert job["retryable"] is False and job["extraction_mode"] == "local"

    outsider = "ai-outsider@example.com"
    make_user(outsider, role="agent")
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/ai-interpretation", headers=auth_header(outsider)).status_code == 404

    from app.ai import client as ai_client
    # Off by default: refused even when a provider is configured.
    monkeypatch.setattr(ai_client, "is_configured", lambda: True)
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/ai-interpretation", headers=headers).status_code == 409

    # Only an operator setting turns the optional path on.
    monkeypatch.setenv("PERSONA_AI_IMPORT_ENABLED", "1")
    monkeypatch.setattr(ai_client, "is_configured", lambda: False)
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/ai-interpretation", headers=headers).status_code == 409

    monkeypatch.setattr(ai_client, "is_configured", lambda: True)
    contract, _ = parse_document(DOCUMENT)
    calls = []
    monkeypatch.setattr(imports, "extract_document", lambda text: calls.append(text) or contract)
    requested = client.post(f"{API}/{persona['id']}/imports/{job['id']}/ai-interpretation", headers=headers)
    assert requested.status_code == 202, requested.text
    assert requested.json()["extraction_mode"] == "ai" and requested.json()["status"] == "queued"
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    done = client.get(f"{API}/{persona['id']}/imports/{job['id']}", headers=headers).json()
    assert done["status"] == "completed" and len(calls) == 1
    assert done["stages"]["personality_extraction"]["message"] == "AI-assisted interpretation completed at the owner's request"


def test_retrying_a_local_job_never_calls_ai(client, make_user, auth_header):
    headers, persona = _persona(client, make_user, auth_header, "mk-retry@example.com")
    job = _import(client, headers, persona)
    with db.SessionLocal() as session:
        row = session.get(imports.PersonaImportJob, job["id"])
        row.status, row.retryable, row.error_code = "failed", 1, "processing_interrupted"
        session.commit()
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/retry", headers=headers).status_code == 202
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    again = client.get(f"{API}/{persona['id']}/imports/{job['id']}", headers=headers).json()
    assert again["status"] == "completed" and again["extraction_mode"] == "local"
    assert len(again["proposal"]["examples"]) == 11


def test_every_evidence_excerpt_quotes_the_document():
    contract, _ = parse_document(DOCUMENT)
    plain = _norm(re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", DOCUMENT.replace("**", "")))
    for item in contract.evidence:
        # Multi-line excerpts join source lines; each line is quoted as written.
        for line in item.source_excerpt.splitlines() or [item.source_excerpt]:
            for part in re.split(r'(?<=[.!?"”)])\s+(?=[A-Z"“(])', line):
                assert _norm(part) in plain, (item.field_path, part)
