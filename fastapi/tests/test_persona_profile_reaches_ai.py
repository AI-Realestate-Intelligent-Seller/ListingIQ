"""Everything the owner filled in on their Persona (by hand or autofill) reaches the AI."""

import json
from types import SimpleNamespace

from app.sms import bobbie
from app.sms.policy import find_reply_policy_violations
from app.sms.prompt import persona_prompt_context, published_profile_facts
from app.sms.runtime_context import AgentRuntimeContext

PERSONA = {
    'full_name': 'Michael Kang',
    'professional_title': 'Founder & Team Lead, SpaceMatch Inc.',
    'professional_role': 'Head of Brokerage',
    'biography': 'Leads a 23-agent team.',
    'specialties': ['Residential home sales'],
    'service_areas': ['Cook', 'DuPage'],
    'profile_config': {
        'identity': {
            'license_number': 'IL 475163662',
            'licensing_jurisdiction': 'Illinois',
            'licensing_status': 'Active',
            'public_email': 'michael@spacematchinc.com',
            'public_phone': '(773) 344-0738',
            'professional_intro': 'Michael Kang with SpaceMatch Inc. in Chicago.',
        },
        'background': {
            'years_experience': None,
            'differentiators': ['Bilingual Korean/English service'],
            'achievements': ['SpaceMatch Chicago team of 23 agents.'],
            'target_audiences': ['Homeowners thinking about selling'],
            'business_background': 'Sold laundromats.',
            'unique_selling_proposition': '',
        },
        'playbook': {
            'sensitive_escalation': 'Legal, tax or lending questions.',
            'human_handoff': "Let me have Michael call you. What's a good time?",
        },
        'communication': {
            'signature_phrases': ['N/A — style only.'],
        },
        'confirmations': {'license_number': 'owner_confirmed', 'public_email': 'owner_disputed'},
    },
}
AGENT = AgentRuntimeContext(
    user_id=1, brokerage_id='b-1', conversation_id=316, active_persona_id=2, active_persona_version_id=2,
    agent_name='Michael Kang', brokerage_name='Linchpin Global', persona=PERSONA,
)
HISTORY = [
    {'direction': 'outbound', 'text': 'Hi, I am reaching out about 124 Main St.'},
    {'direction': 'inbound', 'text': 'What is your license number?'},
]


def test_profile_facts_include_every_filled_field_but_not_blanks_disputes_or_business():
    facts = published_profile_facts(PERSONA)

    assert facts['professional_title'] == 'Founder & Team Lead, SpaceMatch Inc.'
    assert facts['identity']['license_number'] == 'IL 475163662'
    assert facts['identity']['licensing_status'] == 'Active'
    assert facts['identity']['public_phone'] == '(773) 344-0738'
    assert 'public_email' not in facts['identity']  # the owner disputed it
    assert facts['background']['achievements'] == ['SpaceMatch Chicago team of 23 agents.']
    assert 'years_experience' not in facts['background'] and 'unique_selling_proposition' not in facts['background']
    assert 'business_background' not in facts['background']  # residential conversations only
    assert facts['handoff_guidance']['human_handoff'].startswith('Let me have Michael call you')


def test_reply_prompt_contains_the_license_and_background():
    prompt = persona_prompt_context(AGENT, 'What is your license number?')

    assert 'IL 475163662' in prompt
    assert 'Bilingual Korean/English service' in prompt
    assert 'Sold laundromats' not in prompt


def test_reply_writer_gets_profile_as_approved_facts():
    conversation = SimpleNamespace(contact='+13125550225', lead_context=json.dumps({'available': True}))
    approved = bobbie._approved_runtime_context(conversation, HISTORY, AGENT, {'preload': {'available': False}})

    assert approved['lead']['communication_capabilities']['provide_callback_number'] is True
    facts = bobbie.approved_account_facts(AGENT)
    assert facts['current_brokerage'] == 'Linchpin Global'
    assert facts['published_profile']['identity']['license_number'] == 'IL 475163662'


def test_reviewer_sees_profile_facts_even_without_knowledge_matches(monkeypatch):
    sent = []

    def fake_completion(messages, **kwargs):
        sent.append(json.loads(messages[1]['content']))
        return {'content': json.dumps({'valid': True, 'issues': [], 'advisory_issues': [], 'reason': 'ok',
                                       'rewrite_instruction': ''})}

    monkeypatch.setattr(bobbie, 'completion', fake_completion)
    conversation = SimpleNamespace(contact='+13125550225')
    result = bobbie.review_ai_reply(conversation, HISTORY, 'My Illinois license number is IL 475163662.',
                                    {'next_step': 'answer_only'}, knowledge_evidence=[], agent_context=AGENT)

    assert result['valid'] is True
    payload = sent[0]
    assert payload['approved_facts']['published_profile']['identity']['license_number'] == 'IL 475163662'
    assert 'evidence_rules' in payload and 'approved_knowledge_evidence' not in payload


def test_published_contact_details_pass_but_invented_ones_are_still_blocked():
    allowances = bobbie._policy_allowances(AGENT)

    ok = find_reply_policy_violations('You can reach me directly at (773) 344-0738.', **allowances)
    invented = find_reply_policy_violations('You can reach me at (312) 555-0100.', **allowances)
    disputed_email = find_reply_policy_violations('Email me at michael@spacematchinc.com.', **allowances)

    assert 'invented_phone' not in ok
    assert 'invented_phone' in invented
    assert 'invented_email' in disputed_email  # disputed, so not published for sharing


def test_without_a_persona_nothing_changes():
    bare = AgentRuntimeContext(**{**AGENT.__dict__, 'persona': None, 'brokerage_name': ''})

    assert bobbie.approved_account_facts(bare) == {}
    assert 'invented_phone' in find_reply_policy_violations('Call (773) 344-0738.', **bobbie._policy_allowances(bare))


def test_signature_phrases_and_examples_reach_the_prompt_without_placeholders():
    persona = {**PERSONA, 'profile_config': {**PERSONA['profile_config'], 'communication': {
        'signature_phrases': ["Let's look at the real numbers first.", 'N/A — style only.']}}}
    example = {'id': 9, 'scenario': 'Property value question', 'language': 'en',
               'customer_message': 'What is my home worth?',
               'preferred_response': "I don't want to throw out a guess.", 'context': '', 'style_explanation': ''}
    agent = AgentRuntimeContext(**{**AGENT.__dict__, 'persona': persona, 'examples': (example,)})

    prompt = persona_prompt_context(agent, 'What is my home worth?')

    assert "Let's look at the real numbers first." in prompt
    assert 'N/A' not in prompt
    assert "I don't want to throw out a guess." in prompt
