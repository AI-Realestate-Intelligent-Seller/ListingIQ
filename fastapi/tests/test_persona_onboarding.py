"""Focused offline regressions for Milestone 2 onboarding and examples."""

API = "/api/v1/personas"


def complete_profile():
    return {
        "schema_version": 2,
        "completeness_enforced": True,
        "identity": {
            "license_number": "SELF-DECLARED-123",
            "licensing_jurisdiction": "Ontario",
            "licensing_status": "Active (owner confirmed)",
            "public_email": "broker@example.com",
            "public_phone": "+14165550100",
            "professional_intro": "I help owners make clear, well-timed decisions.",
            "brand_identity": "Independent advisory practice",
        },
        "background": {
            "years_experience": 12,
            "previous_experience": "Business operations and real estate advisory.",
            "target_audiences": ["Property owners", "Investors"],
            "industry_expertise": ["Residential", "Business brokerage"],
            "differentiators": ["Operator perspective"],
            "unique_selling_proposition": "Practical advice grounded in owner outcomes.",
            "achievements": ["Owner-declared transaction experience"],
            "transaction_experience": "Self-declared experience across residential transactions.",
            "transaction_claim_status": "self_declared",
            "professional_philosophy": "Position the asset before promoting it.",
            "brand_positioning": "Calm, direct, and commercially aware.",
            "business_background": "Previously operated a service business.",
        },
        "communication": {
            "professional_casual_behavior": "Professional first, conversational after rapport.",
            "confidence": 4, "warmth": 4, "directness": 4, "formality": 3,
            "empathy": 4, "energy": 3, "preferred_sms_length": "brief",
            "preferred_email_length": "balanced", "paragraph_style": "Short paragraphs.",
            "questions_per_message": 1, "emoji_usage": "Rarely; never in sensitive replies.",
            "industry_terminology": "Use plain language before technical terms.",
            "other_languages": [], "multilingual_preferences": "Ask before switching languages.",
            "language_switching": "Switch only at the customer's request.",
            "cultural_preferences": "Avoid assumptions; mirror respectful forms of address.",
            "signature_phrases": ["Happy to walk through the options"],
            "unnatural_expressions": ["Just circling back"],
            "brand_statements": ["Clarity before urgency"],
            "hesitant_response": "Acknowledge the hesitation and ask one low-pressure question.",
            "concept_explanation": "Start with the consequence, then explain the term.",
            "uncertainty_behavior": "State what is known and what needs confirmation.",
            "negative_reply_behavior": "Respect the answer and leave a clear opt-out path.",
            "next_step_behavior": "Offer one specific, optional next step.",
            "human_involvement": "Escalate legal, emotional, or negotiation-sensitive issues.",
        },
        "playbook": {
            "introduction": "Name, brokerage, and why the conversation is relevant.",
            "trust_building": "Be specific, transparent, and avoid manufactured urgency.",
            "qualifying_questions": "Ask one question at a time and explain why it matters.",
            "valuation_conversations": "Separate evidence, assumptions, and opinion.",
            "buyer_vs_seller": "Buyers need criteria; sellers need timing and outcome context.",
            "investors_and_owners": "Use operating and return language only when appropriate.",
            "pricing_disagreements": "Show evidence and preserve the owner's decision authority.",
            "objections": "Acknowledge, clarify, answer, and confirm whether it helped.",
            "follow_up": "Reference the last useful detail and add new value.",
            "appointments": "Propose a purpose and two optional times.",
            "sensitive_escalation": "Pause automation and involve an authorized person.",
            "human_handoff": "Name the person and explain what context will be shared.",
        },
        "confirmations": {},
        "na_reasons": {},
    }


def test_complete_onboarding_examples_are_frozen_and_versioned(client, make_user, auth_header):
    owner = make_user("persona-owner@example.com", role="broker")
    make_user("persona-hob@example.com", role="hob")
    owner_headers = auth_header(owner.email)
    hob_headers = auth_header("persona-hob@example.com")
    created = client.post(API, headers=owner_headers, json={"full_name": "Michael Kang"}).json()
    persona_id = created["id"]

    draft = client.patch(
        f"{API}/{persona_id}/draft",
        headers=owner_headers,
        json={
            "revision": 1,
            "professional_title": "Broker and advisor",
            "biography": "A real estate professional focused on clear owner communication.",
            "experience_summary": "Advises owners, investors, and business operators.",
            "specialties": ["Residential real estate"],
            "service_areas": ["Toronto"],
            "style_instructions": "Be concise, calm, and answer the question first.",
            "profile_config": complete_profile(),
        },
    ).json()
    missing_examples = client.post(f"{API}/{persona_id}/validate", headers=owner_headers)
    assert missing_examples.status_code == 200
    assert missing_examples.json()["valid"] is False
    assert "conversation_examples" in " ".join(missing_examples.json()["errors"])

    example = client.post(
        f"{API}/{persona_id}/examples",
        headers=owner_headers,
        json={
            "revision": draft["revision"], "scenario": "Price objection", "channel": "sms",
            "language": "en", "customer_message": "That estimate feels low.",
            "preferred_response": "I understand. Would it help if I showed you the comparable sales and where the range comes from?",
            "context": "Early valuation conversation", "style_explanation": "Acknowledges before explaining.",
            "provenance": "user_written", "owner_approved": True,
        },
    )
    assert example.status_code == 201, example.text
    revision = example.json()["revision"]
    assert client.post(f"{API}/{persona_id}/validate", headers=owner_headers).json()["valid"] is True

    submitted = client.post(f"{API}/{persona_id}/submit", headers=owner_headers, json={"revision": revision})
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["id"]
    assert client.get(f"{API}/{persona_id}/draft", headers=hob_headers).status_code == 404
    frozen = client.get(f"{API}/{persona_id}/submissions/{submission_id}/examples", headers=hob_headers).json()
    assert frozen["total"] == 1
    assert frozen["items"][0]["preferred_response"].startswith("I understand")

    changed = client.patch(
        f"{API}/{persona_id}/examples/{example.json()['id']}", headers=owner_headers,
        json={"revision": revision, "preferred_response": "This later draft must not alter review material."},
    )
    assert changed.status_code == 200, changed.text
    still_frozen = client.get(f"{API}/{persona_id}/submissions/{submission_id}/examples", headers=hob_headers).json()
    assert still_frozen["items"][0]["preferred_response"].startswith("I understand")

    published = client.post(f"{API}/{persona_id}/publish", headers=hob_headers, json={"submission_id": submission_id})
    assert published.status_code == 201, published.text
    version_examples = client.get(f"{API}/{persona_id}/versions/1/examples", headers=owner_headers).json()
    assert version_examples["items"][0]["preferred_response"].startswith("I understand")


def test_example_mutations_use_revision_and_owner_isolation(client, make_user, auth_header):
    owner = make_user("example-owner@example.com", role="agent")
    outsider = make_user("example-outsider@example.com", role="agent")
    owner_headers = auth_header(owner.email)
    outsider_headers = auth_header(outsider.email)
    persona_id = client.post(API, headers=owner_headers, json={"full_name": "Bobbie Fisher"}).json()["id"]
    payload = {
        "revision": 1, "scenario": "Follow-up", "channel": "email", "language": "en",
        "customer_message": "Can you send more detail?", "preferred_response": "Absolutely. Here is the useful context.",
        "context": "", "style_explanation": "", "provenance": "illustrative", "owner_approved": False,
    }
    created = client.post(f"{API}/{persona_id}/examples", headers=owner_headers, json=payload)
    assert created.status_code == 201
    assert client.post(f"{API}/{persona_id}/examples", headers=owner_headers, json=payload).status_code == 409
    assert client.get(f"{API}/{persona_id}/examples", headers=outsider_headers).status_code == 404
    assert client.patch(
        f"{API}/{persona_id}/examples/{created.json()['id']}", headers=outsider_headers,
        json={"revision": 2, "owner_approved": True},
    ).status_code == 404
