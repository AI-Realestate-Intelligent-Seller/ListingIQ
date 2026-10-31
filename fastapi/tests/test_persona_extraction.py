"""Persona document extraction through the real provider-neutral AI path.

Only ``requests.post`` is replaced, with deterministic Anthropic-shaped
responses. Everything between the import worker and the HTTP call (the AI
client, request validation, the Anthropic adapter and its schema conversion)
runs as in production, so a schema the AI layer rejects fails here.
"""

import json

import pytest
import requests

from app import db
from app.ai import client as ai_client
from app.ai.providers.anthropic import provider_schema
from app.ai.service import _validate_schema_definition
from app.personas import imports
from app.personas.models import PersonaDraftExample, PersonaEvent, PersonaSubmission, PersonaVersion
from app.personas.schemas import PersonaExtractionContract, extraction_wire_schema

API = "/api/v1/personas"


@pytest.fixture(autouse=True)
def _ai_interpretation_path(monkeypatch):
    """These tests exercise the optional AI-assisted interpretation path."""
    monkeypatch.setattr(imports, "DEFAULT_EXTRACTION_MODE", imports.AI_MODE)
    # The AI path is off by default for Persona imports; these tests use a fake provider.
    monkeypatch.setenv("PERSONA_AI_IMPORT_ENABLED", "1")
LONG_PHRASE = "We package the whole thing: building, business and income. That's how it gets financed."
KOREAN_RESPONSE = "안녕하세요 {FirstName} 사장님, 상담 한번 드리겠습니다."

DOCUMENT = """# Personal AI Profile — Jordan Example

Claims marked self-reported are the person's own statements. All conversation examples are illustrative.

## Who I Am
- Full name: Jordan Example. Team lead and licensed real estate broker in Illinois. License number: N/A.

## Professional Background
- Specialties: business brokerage, laundromats, commercial property. Service area: Chicago.
- Sold about 120 laundromats (self-reported).

## Communication DNA
- Numbers-first, concise and direct. English and Korean. Use: "{phrase}"

## Conversation Playbook
- Escalate legal, tax and lending questions to a human. Hand off with a short summary.

## Conversation Examples
{examples}
""".format(phrase=LONG_PHRASE, examples="\n".join(f"{index}. Example {index} (illustrative)" for index in range(1, 12))).encode()


def _examples():
    rows = [
        {"scenario": "Cold outreach, homeowner", "customer_message": "", "preferred_response": "Hi {FirstName}, we have buyers in {Neighborhood}. Would you consider selling?"},
        {"scenario": "Cold outreach, business owner", "customer_message": "", "preferred_response": "Hi {FirstName}, ever thought about what {BusinessName} would sell for?"},
        {"scenario": "Korean-speaking owner", "customer_message": "", "preferred_response": KOREAN_RESPONSE, "language": "ko"},
    ]
    rows += [
        {"scenario": f"Reply scenario {index}", "customer_message": f"Customer message {index}", "preferred_response": f"Data-first reply {index}."}
        for index in range(4, 12)
    ]
    return [{
        "channel": "sms", "language": "en", "context": "", "style_explanation": "Short and direct.",
        "provenance": "illustrative", "review_flags": [], **row,
    } for row in rows]


def _extraction_payload(**overrides):
    payload = {
        "draft": {
            "full_name": "Jordan Example",
            "professional_title": "Founder & Team Lead",
            "specialties": ["Business brokerage", "Laundromats"],
            "service_areas": ["Chicago"],
            "preferred_language": "en",
        },
        "style_config": {"personality": "practical, confident and numbers-first", "directness": 5,
                         "preferred_phrases": ["Let's look at the real numbers first.", LONG_PHRASE]},
        "profile_config": {
            "identity": {"licensing_jurisdiction": "Illinois", "licensing_status": "Licensed broker"},
            "background": {"achievements": ["Sold about 120 laundromats"], "transaction_claim_status": "self_declared"},
            "communication": {"other_languages": ["Korean"], "multilingual_preferences": "Switch to Korean when the customer writes in Korean."},
            "playbook": {"sensitive_escalation": "Legal, tax and lending questions go to a human.",
                         "human_handoff": "Ask for a good time and pass a short summary."},
        },
        "examples": _examples(),
        "evidence": [{"field_path": "draft.full_name", "source_excerpt": "Full name: Jordan Example.", "confidence": "high", "needs_owner_confirmation": False}],
        "claims": [{"statement": "Sold about 120 laundromats", "status": "self_reported",
                    "field_path": "profile_config.background.achievements", "source_excerpt": "Sold about 120 laundromats (self-reported)."}],
        "unmapped_details": [{"section": "Who I Am", "detail": "Team phone line and Instagram account"}],
        "missing_questions": ["What is your license number?"],
        "contradictions": [],
        "unsupported_claims": [],
    }
    payload.update(overrides)
    return payload


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}
        self.headers = {"request-id": "req_mock_123"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _message(text, stop_reason="end_turn"):
    return _Response(200, {
        "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "stop_reason": stop_reason,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 10},
    })


@pytest.fixture
def anthropic(monkeypatch):
    """Configure Anthropic offline and record every outgoing request."""
    monkeypatch.setitem(ai_client.settings["ai"], "provider", "anthropic")
    monkeypatch.setitem(ai_client.settings["ai"], "model", "claude-haiku-5-5")
    monkeypatch.setitem(ai_client.settings["ai"], "anthropic_api_key", "offline-key")
    calls = []
    replies = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "post", post)
    return calls, replies


def _persona(client, make_user, auth_header, email):
    make_user(email, role="broker")
    headers = auth_header(email)
    created = client.post(API, headers=headers, json={"full_name": "Jordan Example"})
    assert created.status_code == 201, created.text
    return headers, created.json()


def _upload_and_process(client, headers, persona):
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("profile.md", DOCUMENT, "text/markdown")})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    return client.get(f"{API}/{persona['id']}/imports/{uploaded.json()['id']}", headers=headers).json()


def _closed_objects(schema, path="$"):
    problems = []
    if any(key in schema for key in ("$ref", "definitions", "anyOf", "default", "pattern")):
        problems.append(path)
    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        problems.append(path)
    for name, child in schema.get("properties", {}).items():
        problems += _closed_objects(child, f"{path}.{name}")
    if "items" in schema:
        problems += _closed_objects(schema["items"], f"{path}[]")
    return problems


def test_wire_schema_passes_ai_layer_and_structured_output_rules():
    # The original failure: the Pydantic schema has "definitions"/"$ref",
    # which the AI service rejects before any provider is called.
    with pytest.raises(Exception, match="definitions"):
        _validate_schema_definition(PersonaExtractionContract.schema())
    schema = extraction_wire_schema()
    _validate_schema_definition(schema)
    assert _closed_objects(schema) == []
    assert _closed_objects(provider_schema(schema)) == []
    # Every category in the source document has somewhere to go.
    profile = schema["properties"]["profile_config"]["properties"]
    assert {"identity", "background", "communication", "playbook"} <= set(profile)
    assert {"license_number", "licensing_jurisdiction", "licensing_status"} <= set(profile["identity"]["properties"])
    assert {"achievements", "transaction_claim_status", "business_background"} <= set(profile["background"]["properties"])
    assert {"signature_phrases", "unnatural_expressions", "other_languages", "language_switching"} <= set(profile["communication"]["properties"])
    assert {"sensitive_escalation", "human_handoff"} <= set(profile["playbook"]["properties"])
    assert {"claims", "unmapped_details", "examples"} <= set(schema["properties"])
    example = schema["properties"]["examples"]["items"]["properties"]
    assert example["provenance"]["enum"] == ["authentic", "user_written", "illustrative"]
    assert "review_flags" in example


def test_five_stage_pipeline_through_real_ai_client(client, make_user, auth_header, anthropic):
    calls, replies = anthropic
    replies.append(_message(json.dumps(_extraction_payload(), ensure_ascii=False)))
    headers, persona = _persona(client, make_user, auth_header, "extract-owner@example.com")

    job = _upload_and_process(client, headers, persona)

    assert job["status"] == "completed", job
    assert [job["stages"][name]["status"] for name in imports.STAGES] == ["completed"] * 5
    assert job["stages"]["conversation_analysis"]["message"] == "Found 11 conversation examples (11 illustrative; 3 flagged for owner review)"
    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == imports.EXTRACTION_MAX_OUTPUT_TOKENS
    assert calls[0]["timeout"] == imports.EXTRACTION_TIMEOUT_SECONDS
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert _closed_objects(body["output_config"]["format"]["schema"]) == []
    # The whole document reaches the provider, Korean included.
    assert DOCUMENT.decode() in body["messages"][-1]["content"]

    proposal, assessment = job["proposal"], job["assessment"]
    examples = proposal["examples"]
    assert len(examples) == 11
    assert {item["provenance"] for item in examples} == {"illustrative"}
    assert [item["customer_message"] for item in examples[:3]] == ["", "", ""]
    assert all(imports.OUTREACH_REVIEW_FLAG in item["review_flags"] for item in examples[:3])
    assert all(not item["review_flags"] for item in examples[3:])
    assert examples[2]["preferred_response"] == KOREAN_RESPONSE and examples[2]["language"] == "ko"
    # Long signature phrase moved to the longer list, not cut or dropped.
    assert LONG_PHRASE not in proposal["style_patch"]["preferred_phrases"]
    assert LONG_PHRASE in proposal["profile_patch"]["communication"]["signature_phrases"]
    # Missing license is a warning and an unconfirmed field, not a crash.
    assert "License number is missing; add it, or mark it not applicable with a reason, before submitting" in assessment["completeness_warnings"]
    assert assessment["missing_questions"][0] == assessment["completeness_warnings"][0]
    assert proposal["profile_patch"]["confirmations"]["license_number"] == "missing"
    # Self-reported claims keep their status.
    assert assessment["claims"][0]["status"] == "self_reported"
    assert "Self-reported: Sold about 120 laundromats" in assessment["unsupported_claims"]
    assert proposal["profile_patch"]["confirmations"]["business_background"] == "self_declared"
    assert [item["example_index"] for item in assessment["example_review_flags"]] == [0, 1, 2]
    assert assessment["unmapped_details"] == proposal["unmapped_details"] != []

    applied = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    draft = applied.json()
    assert draft["review_status"] == "draft"
    assert draft["profile_config"]["identity"]["license_number"] == ""
    assert draft["profile_config"]["communication"]["other_languages"] == ["Korean"]
    with db.SessionLocal() as session:
        rows = session.query(PersonaDraftExample).filter_by(persona_id=persona["id"]).order_by(PersonaDraftExample.position).all()
        assert len(rows) == 11
        assert {row.provenance for row in rows} == {"illustrative"}
        assert all(row.owner_approved == 0 for row in rows)
        assert rows[2].preferred_response == KOREAN_RESPONSE
        # Apply & Review never submits, approves, publishes or activates.
        assert session.query(PersonaSubmission).filter_by(persona_id=persona["id"]).count() == 0
        assert session.query(PersonaVersion).filter_by(persona_id=persona["id"]).count() == 0
    fetched = client.get(f"{API}/{persona['id']}", headers=headers).json()
    assert fetched["active_version_id"] is None


def _http_error(status, error_type):
    return _Response(status, {"type": "error", "error": {"type": error_type, "message": "offline"}})


@pytest.mark.parametrize("reply,code", [
    (_http_error(401, "authentication_error"), "ai_not_configured"),
    (_http_error(429, "rate_limit_error"), "ai_rate_limited"),
    (_http_error(529, "overloaded_error"), "ai_unavailable"),
    (requests.Timeout("offline"), "ai_timeout"),
    (requests.ConnectionError("offline"), "ai_unavailable"),
    (_message('{"draft": {', stop_reason="max_tokens"), "ai_incomplete_output"),
    (_message("not json"), "invalid_extraction"),
    (_message(json.dumps({"examples": [{"scenario": "x"}]})), "invalid_extraction"),
])
def test_provider_failures_report_specific_retryable_categories(client, make_user, auth_header, anthropic, reply, code):
    _calls, replies = anthropic
    replies.append(reply)
    headers, persona = _persona(client, make_user, auth_header, f"fail-{code}-{id(reply)}@example.com")
    job = _upload_and_process(client, headers, persona)
    assert job["status"] == "failed"
    assert job["error_code"] == code and job["retryable"] is True
    assert job["stages"]["document_validation"]["status"] == "completed"
    assert job["stages"]["personality_extraction"]["status"] == "failed"
    assert job["proposal"] is None and job["assessment"] is None
    # Failure messages never echo provider output or document text.
    assert "offline" not in job["error_message"] and "Jordan" not in job["error_message"]


def test_missing_credentials_fail_without_calling_provider(client, make_user, auth_header, anthropic, monkeypatch):
    calls, _replies = anthropic
    monkeypatch.setitem(ai_client.settings["ai"], "anthropic_api_key", "")
    headers, persona = _persona(client, make_user, auth_header, "no-key@example.com")
    job = _upload_and_process(client, headers, persona)
    assert job["error_code"] == "ai_not_configured"
    assert calls == []


def test_retry_after_failure_is_idempotent(client, make_user, auth_header, anthropic):
    calls, replies = anthropic
    replies.extend([requests.Timeout("offline"), _message(json.dumps(_extraction_payload(), ensure_ascii=False))])
    headers, persona = _persona(client, make_user, auth_header, "retry-owner@example.com")
    failed = _upload_and_process(client, headers, persona)
    assert failed["error_code"] == "ai_timeout"

    # Uploading the same file again returns the existing job, never a second one.
    again = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                        files={"file": ("profile.md", DOCUMENT, "text/markdown")})
    assert again.json()["id"] == failed["id"]
    assert client.post(f"{API}/{persona['id']}/imports/{failed['id']}/retry", headers=headers).status_code == 202
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    job = client.get(f"{API}/{persona['id']}/imports/{failed['id']}", headers=headers).json()
    assert job["status"] == "completed" and job["attempt_count"] == 2 and len(calls) == 2
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/retry", headers=headers).status_code == 409

    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 1}).status_code == 200
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 2}).status_code == 409
    assert client.post(f"{API}/{persona['id']}/imports/{job['id']}/retry", headers=headers).status_code == 409
    listed = client.get(f"{API}/{persona['id']}/imports", headers=headers).json()
    assert len(listed) == 1
    with db.SessionLocal() as session:
        assert session.query(PersonaDraftExample).filter_by(persona_id=persona["id"]).count() == 11
        events = session.query(PersonaEvent).filter_by(persona_id=persona["id"], event_type="persona.import_applied").count()
        assert events == 1


def test_authentic_label_from_a_document_is_never_trusted(client, make_user, auth_header, anthropic):
    _calls, replies = anthropic
    examples = _examples()
    examples[5]["provenance"] = "authentic"
    replies.append(_message(json.dumps(_extraction_payload(examples=examples), ensure_ascii=False)))
    headers, persona = _persona(client, make_user, auth_header, "authentic@example.com")
    job = _upload_and_process(client, headers, persona)
    flagged = job["proposal"]["examples"][5]
    assert flagged["provenance"] == "user_written"
    assert imports.AUTHENTIC_REVIEW_FLAG in flagged["review_flags"]
    assert "authentic" not in {item["provenance"] for item in job["proposal"]["examples"]}


def _schema_counts(schema):
    optional = unions = 0
    for child in list((schema.get("properties") or {}).values()) + ([schema["items"]] if "items" in schema else []):
        child_optional, child_unions = _schema_counts(child)
        optional += child_optional
        unions += child_unions
    if isinstance(schema.get("properties"), dict):
        optional += len(set(schema["properties"]) - set(schema.get("required", [])))
    if "anyOf" in schema or isinstance(schema.get("type"), list):
        unions += 1
    return optional, unions


def test_provider_schema_stays_within_documented_structured_output_limits():
    # Anthropic rejects a request (HTTP 400) whose output schema has more than
    # 24 optional properties or more than 16 union-typed properties.
    schema = provider_schema(extraction_wire_schema())
    optional, unions = _schema_counts(schema)
    assert optional == 0 and unions == 0
    # Same style as the schemas that already compile in production: every
    # enum is typed and small, and nothing uses null.
    enums = []

    def collect(node):
        if "enum" in node:
            enums.append(node)
        for child in list((node.get("properties") or {}).values()) + ([node["items"]] if "items" in node else []):
            collect(child)

    collect(schema)
    assert enums and all("type" in node and None not in node["enum"] and len(node["enum"]) <= 10 for node in enums)
    assert "null" not in json.dumps(schema)


def _all_required_reply_with_placeholders():
    """What a schema-constrained provider returns: every key, unstated ones empty."""
    def blank(schema):
        if schema.get("type") == "object":
            return {name: blank(child) for name, child in schema["properties"].items()}
        if schema.get("type") == "array":
            return []
        if schema.get("type") == "string":
            return ""
        return None

    schema = extraction_wire_schema()
    payload = _extraction_payload()
    for section in ("draft", "style_config", "profile_config"):
        filled = blank(schema["properties"][section])
        for key, value in payload[section].items():
            if isinstance(value, dict):
                filled[key].update(value)
            else:
                filled[key] = value
        payload[section] = filled
    return payload


def test_unstated_placeholders_never_overwrite_existing_draft(client, make_user, auth_header, anthropic):
    _calls, replies = anthropic
    payload = _all_required_reply_with_placeholders()
    assert payload["draft"]["biography"] == "" and payload["style_config"]["formality"] == ""
    replies.append(_message(json.dumps(payload, ensure_ascii=False)))
    headers, persona = _persona(client, make_user, auth_header, "placeholder-owner@example.com")
    kept = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={
        "revision": 1, "biography": "Written by the owner.", "style_config": {"formality": 2},
    })
    assert kept.status_code == 200, kept.text
    job = _upload_and_process(client, headers, persona)
    assert job["status"] == "completed", job["error_code"]
    assert "biography" not in job["proposal"]["draft_patch"]
    assert "formality" not in job["proposal"]["style_patch"]
    applied = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 2})
    assert applied.status_code == 200, applied.text
    assert applied.json()["biography"] == "Written by the owner."
    assert applied.json()["style_config"]["formality"] == 2
    assert applied.json()["full_name"] == "Jordan Example"


def test_provider_rejection_logs_safe_request_metadata(client, make_user, auth_header, anthropic, caplog):
    _calls, replies = anthropic
    rejected = _Response(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                          "message": "output_config.format.schema: Schema is too complex for compilation"}})
    rejected.headers = {"request-id": "req_schema_check"}
    replies.append(rejected)
    headers, persona = _persona(client, make_user, auth_header, "rejected-owner@example.com")
    with caplog.at_level("WARNING", logger="app.personas.imports"):
        job = _upload_and_process(client, headers, persona)
    assert job["error_code"] == "ai_request_rejected"
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "req_schema_check" in logged and "invalid_request_error" in logged and "400" in logged
    assert "Schema is too complex for compilation" in logged
    assert "Jordan" not in logged
    # The owner-visible message carries only the reference, not provider text.
    assert job["error_message"].endswith("(provider reference req_schema_check)")
    assert "complex" not in job["error_message"]
