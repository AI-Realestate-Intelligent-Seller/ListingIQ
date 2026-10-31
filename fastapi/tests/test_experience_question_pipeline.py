"""Conversation 11: "Tell me about your experience selling homes like mine".

The owner asked about experience without a question mark. Retrieval returned
laundromat/business-brokerage excerpts, the first draft was blocked for a
repeated question, regeneration added unsupported claims, the reviewer filed
self-contradicting issues and the thread was handed to a human.

Fully offline: model calls are scripted per feature, knowledge search runs over
a fake in-memory store, and SMS delivery is captured.
"""

import io
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.personas import imports, template_document
from app.personas.template_parser import parse_document
from app.sms import bobbie, knowledge, policy, service

from test_personal_ai_runtime_e2e import offline  # noqa: F401  (fixture used by name)
from test_seller_objection_review import _michael_style_agent, _reviews, _run
from test_sms_ai_quality_reviewer import FakeKnowledgeStore, _chunk, _disposition, _review

QUESTION = 'Tell me about your experience selling homes like mine'
EARLIER_QUESTION = 'Are you thinking about selling 1 Test St this year?'
HISTORY = [
    ('outbound', f'Hi Owner, Michael Kang with SpaceMatch in Chicago. {EARLIER_QUESTION}'),
    ('inbound', 'Maybe. Who are you exactly?'),
    ('outbound', f"I'm a broker with Claude Test Brokerage in Chicago. {EARLIER_QUESTION}"),
    ('inbound', QUESTION),
]
ANSWER = ('Fair question. I don\u2019t have verified home-sale numbers to share by text, but I\u2019m happy to go '
          'over specific examples with you.')
BUSINESS_ANSWER = ('Fair question. Most of my track record is in business sales, mainly laundromats, which is '
                   'different from selling a home.')
REVIEW_DIR = Path(__file__).resolve().parents[2] / 'docs' / 'persona-review'


def _business_store(user, version_id):
    return FakeKnowledgeStore([
        _chunk('laundromats', 'Owner-confirmed self reported information: Commercial or business-sale experience: '
                              'Sold about 120 laundromats over roughly five years.', user, version_id, score=0.3),
        _chunk('business', 'Owner-kept imported reference information: Services: business brokerage, packaging the '
                           'business, building and income so it gets financed.', user, version_id, score=0.32),
        _chunk('license', 'Owner-confirmed information: Licensed Illinois real estate broker (IDFPR).',
               user, version_id, score=0.4),
    ])


# ---------------------------------------------------------------------------
# 1. Template import compatibility and the new fields
# ---------------------------------------------------------------------------

def _docx_body(data: bytes) -> str:
    return imports._docx_text(ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml')))


def test_blank_template_stays_empty_and_new_fields_import():
    for blank in (_docx_body(template_document.docx_template()), template_document.markdown_template()):
        contract, summary = parse_document(blank)
        assert contract is None and summary['unmapped_details'] == 0
    residential, business = (template_document.SECTIONS[1][1][0][1][1], template_document.SECTIONS[1][1][0][1][2])
    answers = {
        'Full name and preferred display name': 'Dana Lee', 'Professional role and title': 'Broker associate.',
        'Jurisdiction': 'Illinois', 'Biography': 'Chicago agent.', 'Languages': 'English (default)',
        residential: 'Sold 12 condos in 2025 (self-reported)', business: 'Sold 3 laundromats (self-reported)',
        'Personality, tone, formality and conversation approach': 'Warm and direct.',
        'SMS and email length (for example 1-3 sentences, one question per message)': '1-3 sentences, one question per message',
        'Signature phrases (Use)': "Let's look at the real numbers first.\nNo pressure.",
        'Avoid (phrases, styles, filler and sales tactics)': 'act now\nguaranteed',
        'Pricing': 'I price from recent comparable sales.',
        'Track record': '[Residential | Self-reported] Sold 12 condos in 2025.',
    }
    contract, _ = parse_document(_docx_body(template_document.docx_template(answers)))
    communication = contract.profile_config['communication']
    assert communication['signature_phrases'] == ["Let's look at the real numbers first.", 'No pressure.']
    assert contract.style_config['avoided_phrases'] == ['act now', 'guaranteed']
    assert communication['questions_per_message'] == 1 and contract.style_config['personality'] == 'Warm and direct.'
    background = contract.profile_config['background']
    assert background['transaction_experience'] == 'Sold 12 condos in 2025 (self-reported)'
    assert background['business_background'] == 'Sold 3 laundromats (self-reported)'
    statements = [claim.statement for claim in contract.claims]
    assert 'Residential experience: Sold 12 condos in 2025 (self-reported)' in statements
    assert 'Commercial or business-sale experience: Sold 3 laundromats (self-reported)' in statements
    details = {(item.section, item.detail) for item in contract.unmapped_details}
    assert ('Selling Approach and Services', 'Pricing: I price from recent comparable sales.') in details
    assert ('Knowledge Base', 'Track record: [Residential | Self-reported] Sold 12 condos in 2025.') in details


# ---------------------------------------------------------------------------
# 2. Clean Michael Kang documents
# ---------------------------------------------------------------------------

def test_clean_michael_persona_separates_knowledge_and_keeps_phrases():
    source = (REVIEW_DIR / 'michael-kang-persona-clean.md').read_text(encoding='utf-8')
    assert not re.search(r'to confirm|TO BE|delete this note|buyers looking|top-ranked|80%|35K', source, re.I)
    contract, _ = parse_document(source)
    assert len(contract.profile_config['communication']['signature_phrases']) == 8
    assert len(contract.style_config['avoided_phrases']) == 8
    # No residential claim is made from business figures.
    assert 'transaction_experience' not in contract.profile_config['background']
    knowledge_lines = [item.detail for item in contract.unmapped_details if item.section == 'Knowledge Base']
    laundromats = [line for line in knowledge_lines if 'laundromat' in line.lower()]
    assert laundromats and all('[Business sales' in line and 'not residential' in line for line in laundromats)
    assert not any(line for line in knowledge_lines if '[Residential' in line and re.search(r'\d', line))
    docx = parse_document(_docx_body((REVIEW_DIR / 'michael-kang-persona-clean.docx').read_bytes()))[0]
    assert docx.examples == contract.examples and docx.unmapped_details == contract.unmapped_details
    assert all(not example.review_flags for example in contract.examples)


# ---------------------------------------------------------------------------
# 3. Experience requests phrased as statements or commands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    QUESTION, 'Experience selling homes like mine.', "Walk me through the homes you've sold",
    "I'd like to know your track record", 'Share your experience with houses in this area',
])
def test_experience_requests_trigger_retrieval_without_question_marks(text):
    assert knowledge.is_experience_question(text)
    assert knowledge.needs_agent_knowledge(text) and knowledge.agent_knowledge_allowed(text)
    query = knowledge.knowledge_query(text)
    assert knowledge.EXPERIENCE_TOPIC_EXPANSION in query and knowledge.SELLER_TOPIC_EXPANSION not in query


def test_statements_without_a_request_still_skip_retrieval():
    for text in ("I had it listed but it didn't sell", 'I want to sell quickly at 675000', 'ok thanks'):
        assert not knowledge.is_experience_question(text)
    assert not knowledge.needs_agent_knowledge("I had it listed but it didn't sell")


def test_experience_search_labels_scope_and_flags_missing_residential(offline, client, make_user, auth_header,
                                                                      monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'scope@example.com')
    monkeypatch.setattr(knowledge, 'vector_store', _business_store(user, version['id']))
    from app.sms.runtime_context import AgentRuntimeContext
    context = AgentRuntimeContext(user_id=user.id, brokerage_id=user.brokerage_id, conversation_id=None,
                                  active_persona_id=None, active_persona_version_id=version['id'],
                                  agent_name='Claude Tester', brokerage_name='Claude Test Brokerage', persona=None)
    result = knowledge.search_agent_knowledge(knowledge.knowledge_query(QUESTION), context, 3, relevance_filter=True)
    # Owner conversations are residential: business-only excerpts never reach the model.
    assert [match['experience_scope'] for match in result['matches']] == ['general']
    assert result['business_excerpts_dropped'] == 2
    assert 'relevance_filter' not in result and result['experience_evidence']['residential_matches'] == 0
    assert 'No approved residential' in result['reason']
    assert 'Never mention business, laundromat or commercial' in result['evidence_note']


# ---------------------------------------------------------------------------
# 4. Reviewer consistency and repair
# ---------------------------------------------------------------------------

def test_self_contradicting_issues_are_advisory_and_explained_ones_block():
    contradicted = bobbie._review_verdict({'valid': False, 'advisory_issues': [], 'issues': [], 'blocking_issues': [
        'unsupported_claim: laundromat claim is supported by excerpt laundromats',
        'ignores_message: the reply answers the experience question directly']})
    assert contradicted['valid'] is True and len(contradicted['advisory_issues']) == 2
    for issue in ("unsupported_claim: 'sold many homes' - excerpts describe laundromat sales, not homes",
                  'unsupported_claim: claims 300 homes sold; no excerpt states it',
                  'ignores_message: talks about financing instead of experience',
                  'ignores_message: answers a different question',
                  'fabricated_valuation: says the home is worth $700k',
                  'false_identity: wrong brokerage', 'guarantee_or_misleading: guarantees a sale',
                  'consent_violation: ignored STOP', 'invented_commitment: says the visit is booked'):
        assert bobbie._review_verdict({'valid': True, 'blocking_issues': [issue], 'advisory_issues': [],
                                       'issues': []})['valid'] is False, issue
    assert 'VERDICT CONSISTENCY' in bobbie.REVIEW_SYSTEM and 'EXPERIENCE QUESTIONS' in bobbie.REVIEW_SYSTEM


def test_repeated_question_is_removed_without_regenerating_the_answer():
    history = [{'direction': direction, 'text': text} for direction, text in HISTORY]
    trimmed = policy.remove_repeated_questions(f'{ANSWER} {EARLIER_QUESTION}', history)
    assert trimmed == ANSWER
    assert policy.find_conversation_repetition(f'{ANSWER} {EARLIER_QUESTION}', history) == 'repeated_question'
    assert policy.find_conversation_repetition(trimmed, history) == ''


# ---------------------------------------------------------------------------
# Replay of conversation 11
# ---------------------------------------------------------------------------

def test_replay_conversation_11_answers_without_handoff(offline, client, make_user, auth_header, monkeypatch,
                                                         caplog):
    user, version = _michael_style_agent(client, make_user, auth_header, 'conv11@example.com')
    caplog.set_level(logging.INFO)
    contradictory = _review(blocking=['unsupported_claim: business-sale claim is supported by excerpt laundromats',
                                      'ignores_message: reply answers the experience question'])
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery', lead_status='want_more_info')],
        'sms.reply': [f'{ANSWER} {EARLIER_QUESTION}'],
        'sms.review': [contradictory],
    }, _business_store(user, version['id']), history=HISTORY)
    # The repeated question was removed locally: no repair or regeneration call.
    assert [call['feature'] for call in calls] == ['sms.disposition', 'sms.reply', 'sms.review']
    assert sent == [(ANSWER, 'ai.reply')]
    assert state == {'handled_by': 'bobbie', 'ai_enabled': True}
    reply_context = calls[1]['messages'][1]['content']
    assert '"experience_scope": "business_or_commercial"' not in reply_context
    assert 'Sold about 120 laundromats' not in reply_context
    assert 'Never mention business, laundromat, commercial' in calls[1]['messages'][0]['content']
    evidence = _reviews(calls)[0]['approved_knowledge_evidence'][0]
    assert evidence['experience_evidence']['residential_matches'] == 0
    assert not any('review_exhausted' in record.getMessage() for record in caplog.records)


def test_experience_claim_rejections_send_a_transparent_answer_not_a_handoff(offline, client, make_user,
                                                                            auth_header, monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'conv11b@example.com')
    # Passes the deterministic policy; only the reviewer sees the claim has no residential support.
    overstated = 'I know exactly how to sell homes like yours.'
    hard = _review(blocking=["unsupported_claim: 'sell homes like yours' - no residential excerpt supports it"])
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery', lead_status='want_more_info')],
        'sms.reply': [overstated] * 3,
        'sms.review': [hard] * 3,
    }, _business_store(user, version['id']), history=HISTORY)
    assert sent == [(policy.EXPERIENCE_FALLBACK, 'ai.reply')]
    assert state == {'handled_by': 'bobbie', 'ai_enabled': True}
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS
    assert not re.search(r'\b(?:visit|stop by|price|worth)\b', policy.EXPERIENCE_FALLBACK, re.I)


# ---------------------------------------------------------------------------
# 5. Hard blocks and handoff are preserved
# ---------------------------------------------------------------------------

def test_hard_blocks_on_experience_questions_still_hand_off(offline, client, make_user, auth_header, monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'conv11c@example.com')
    hard = _review(blocking=['false_identity: claims to be with RE/MAX'])
    sent, _calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery', lead_status='want_more_info')],
        'sms.reply': ["I'm with RE/MAX and have sold homes like yours."] * 3,
        'sms.review': [hard] * 3,
    }, _business_store(user, version['id']), history=HISTORY)
    assert sent == [] and state == {'handled_by': 'broker', 'ai_enabled': False}


def test_claim_only_fallback_needs_an_experience_question():
    history = [{'direction': direction, 'text': text} for direction, text in HISTORY]
    issues = ['unsupported_claim: invents a list of ready buyers']
    assert service._experience_answer_after_rejection(QUESTION, history, issues, 'Michael Kang') == \
        policy.EXPERIENCE_FALLBACK
    assert service._experience_answer_after_rejection('Do you have buyers?', history, issues, 'Michael Kang') == ''
    assert service._experience_answer_after_rejection(
        QUESTION, history, issues + ['guarantee_or_misleading: guarantees a sale'], 'Michael Kang') == ''
    assert policy.find_reply_policy_violations(policy.EXPERIENCE_FALLBACK, history=history,
                                               agent_name='Michael Kang') == []


# ---------------------------------------------------------------------------
# The owner-edited Word template (authoritative download)
# ---------------------------------------------------------------------------

def _fill(text: str, values: dict) -> str:
    """Type answers into the extracted template text, as an agent or AI would in Word."""
    lines, out = text.splitlines(), []
    for line in lines:
        stripped = line.strip()
        if stripped in ('-', ''):
            continue  # empty list placeholders under Use / Avoid
        label = re.sub(r'^-\s*', '', stripped).rstrip(':').rstrip()
        value = values.get(label)
        if isinstance(value, list):
            indent = len(line) - len(line.lstrip()) + 2
            out += [line.rstrip()] + [' ' * indent + f'- {item}' for item in value]
        elif value is not None:
            out.append(f'{line.rstrip()} {value}')
        else:
            out.append(line)
    return '\n'.join(out)


def test_owner_edited_docx_is_served_and_imports_every_section(client, make_user, auth_header):
    make_user('edited-template@example.com', role='agent')
    served = client.get('/api/v1/personas/template', headers=auth_header('edited-template@example.com')).content
    assert served == template_document.AUTHORITATIVE_DOCX.read_bytes()
    text = _docx_body(served)
    assert parse_document(text)[0] is None and parse_document(text)[1]['unmapped_details'] == 0
    use = ["Let's look at the real numbers first.", 'No pressure. Just want you to know where you stand.']
    filled = _fill(text, {
        'Full name and preferred display name': 'Dana Lee', 'Professional role and title': 'Broker associate.',
        'Jurisdiction': 'Illinois', 'Brokerage': 'Acme Realty', 'Biography': 'Chicago agent.',
        'Residential experience (years, homes sold, property types; only what you can support)':
            'Sold 12 condos in 2025 (self-reported)',
        'Commercial or business-sale experience (kept separate from residential)': 'Sold 3 laundromats (self-reported)',
        'Personality, tone, formality and conversation approach': 'Warm and direct.',
        'SMS and email length (for example 1-3 sentences, one question per message)':
            '1-3 sentences, one question per message',
        'Languages': 'English (default)',
        'Signature phrases (Use)': use, 'Avoid (phrases, styles, filler and sales tactics)': ['act now', 'guaranteed'],
        'Uncertainty': "I don't guess.", 'Hand off to a human': 'Let me have Dana call you.',
        'Response': "I don't want to guess. Let me pull recent sales first.",
        'Pricing': ['[Process | Dana | Self-reported] I price from recent comparable sales.'],
        'Track record': ['[Residential | Dana | Self-reported] Sold 12 condos in 2025.'],
        # Style placed under Knowledge Base: merged into the phrases, never kept as knowledge.
        'Signature Phrases': [use[0], "Happy to help either way."],
    })
    contract, summary = parse_document(filled)
    assert summary['sections_recognized'] == ['identity', 'background', 'communication', 'playbook', 'examples']
    communication = contract.profile_config['communication']
    assert communication['signature_phrases'] == use + ['Happy to help either way.']
    assert contract.style_config['avoided_phrases'] == ['act now', 'guaranteed']
    assert communication['questions_per_message'] == 1 and communication['uncertainty_behavior'] == "I don't guess."
    background = contract.profile_config['background']
    assert background['transaction_experience'] == 'Sold 12 condos in 2025 (self-reported)'
    assert background['business_background'] == 'Sold 3 laundromats (self-reported)'
    assert contract.profile_config['playbook']['human_handoff'] == 'Let me have Dana call you.'
    assert len(contract.examples) == 7
    details = [(item.section, item.detail) for item in contract.unmapped_details]
    assert ('Selling Approach and Services', 'Pricing: [Process | Dana | Self-reported] I price from recent '
            'comparable sales.') in details
    assert ('Knowledge Base', 'Track record: [Residential | Dana | Self-reported] Sold 12 condos in 2025.') in details
    assert not any('Signature' in detail or 'real numbers' in detail for _section, detail in details)


def test_business_experience_in_a_reply_is_rewritten_before_review(offline, client, make_user, auth_header,
                                                                    monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'residential@example.com')
    sent, calls, _state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery', lead_status='want_more_info')],
        'sms.reply': [BUSINESS_ANSWER],
        'sms.reply.policy_repair': [ANSWER],
        'sms.review': [_review()],
    }, _business_store(user, version['id']), history=HISTORY)
    assert 'business_experience' in policy.find_reply_policy_violations(BUSINESS_ANSWER)
    assert [call['feature'] for call in calls] == [
        'sms.disposition', 'sms.reply', 'sms.reply.policy_repair', 'sms.review']
    repair = calls[2]['messages'][-1]['content']
    assert 'business_experience' in repair and 'residential home' in repair
    assert sent == [(ANSWER, 'ai.reply')]
