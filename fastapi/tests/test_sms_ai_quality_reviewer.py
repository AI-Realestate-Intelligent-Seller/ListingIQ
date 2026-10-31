"""SMS AI quality: no arbitrary length limits, a commercially sensible reviewer,
seller-intent handling ($675,000), dynamic Persona examples and safety rules.

Fully offline: model calls are scripted per feature, knowledge search runs the
real owner-scoped filter over a fake in-memory store, and SMS delivery is
captured. No provider, vector database or SMS gateway is reached.
"""

import json
import logging

import pytest

from app import db
from app.models import Conversation, Message
from app.personas.models import PersonaVersionExample
from app.sms import bobbie, knowledge, policy, service
from app.sms.classifier import classify_lead_message, seller_signals
from app.sms.prompt import build_agent_prompt, persona_prompt_diagnostics, relevant_examples
from app.sms.runtime_context import resolve_agent_runtime_context, runtime_context_is_current

from test_personal_ai_runtime_e2e import (  # noqa: F401  (fixtures are used by name)
    API,
    _conversation,
    _publish_manual,
    _republish,
    offline,
    scripted,
    three_agents,
)

SELLER_MESSAGE = 'I just want to sell it quickly at best price like 675000$'
SELLER_REPLY = ("Got it. You're hoping to sell quickly and are targeting around $675,000. We'd need to review the "
                'property and recent comparable sales before assessing that number. Would you prefer a quick call '
                'or an in-person visit?')
PRICING_EXAMPLE = {
    'scenario': 'Seller asks about pricing and selling fast', 'channel': 'sms', 'language': 'en',
    'customer_message': 'What price could I sell my house for, and how quickly?',
    'preferred_response': 'Great question! Pricing depends on condition and nearby sales. Happy to come by.',
    'provenance': 'user_written',
}
GREETING_EXAMPLE = {
    'scenario': 'Greeting', 'channel': 'sms', 'language': 'en', 'customer_message': 'Hello',
    'preferred_response': 'Hi there, thanks for getting back to me!', 'provenance': 'user_written',
}


def _disposition(next_step='answer_only', stage='discovery', action='continue', calendar='none',
                 lead_status='interested'):
    return json.dumps({
        'action': action, 'intent': 'test', 'outcome': 'positive', 'lead_status': lead_status, 'confidence': 0.9,
        'conversation_stage': stage, 'next_step': next_step, 'qualification_focus': '',
        'calendar': {'state': calendar, 'should_fetch_availability': False, 'requested_time_text': '',
                     'reason': ''},
        'reason': 'test'})


def _review(blocking=(), advisory=(), valid=None, issues=None):
    return json.dumps({
        'valid': (not blocking) if valid is None else valid, 'blocking_issues': list(blocking),
        'advisory_issues': list(advisory), 'issues': list(blocking) if issues is None else list(issues),
        'rewrite_instruction': 'Fix it.' if blocking else '', 'reason': 'test'})


VALID = _review()


def _publish_with_examples(client, make_user, auth_header, email, brokerage, name, examples,
                           brokerage_name='Same Realty'):
    user, headers, persona, _version = _publish_manual(client, make_user, auth_header, email, brokerage, name,
                                                       brokerage_name=brokerage_name)
    draft = client.get(f'{API}/{persona["id"]}/draft', headers=headers).json()
    saved = client.put(f'{API}/{persona["id"]}/examples', headers=headers,
                       json={'revision': draft['revision'], 'examples': examples})
    assert saved.status_code == 200, saved.text
    version = client.post(f'{API}/{persona["id"]}/versions', headers=headers,
                          json={'revision': saved.json()['revision']})
    assert version.status_code in (200, 201), version.text
    return user, headers, persona, version.json()


class FakeKnowledgeStore:
    """In-memory store that applies the same metadata filters as Qdrant/Chroma."""

    backend = 'fake'
    available = True
    embedding_name = 'fake-embedding'

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def search_text(self, collection, text, k=5, filters=None):
        self.calls.append({'query': text, 'k': k, 'filters': dict(filters or {})})
        hits = [chunk for chunk in self.chunks
                if all(chunk['metadata'].get(key) == value for key, value in (filters or {}).items())]
        return hits[:k]


def _chunk(chunk_id, text, user, version_id, score=0.6, **overrides):
    metadata = {'owner_user_id': user.id, 'brokerage_id': user.brokerage_id, 'published_version_id': version_id,
                'approval_status': 'eligible', 'source_type': 'agent_knowledge', **overrides}
    return {'id': chunk_id, 'text': text, 'score': score, 'metadata': metadata}


def _seller_store(user, version_id):
    return FakeKnowledgeStore([
        _chunk('bio-award', 'Owner-confirmed information: Named top producer in 2019 and 2020.', user, version_id),
        _chunk('bio-hometown', 'Owner-confirmed information: Grew up on the North Shore and coaches soccer.',
               user, version_id),
        _chunk('pricing', 'Owner-confirmed information: My pricing approach starts with an in-person walkthrough '
                          'and a comparative market analysis before recommending a list price.', user, version_id),
        _chunk('bio-sold', 'Owner-confirmed information: Sold more than 300 homes.', user, version_id),
        _chunk('other-owner', 'Owner-confirmed information: My pricing strategy is aggressive.', user, version_id,
               owner_user_id=user.id + 999),
    ])


def _run_pipeline(monkeypatch, user_id, latest, script, history=None, store=None):
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or Message(id=1))
    if store is not None:
        monkeypatch.setattr(knowledge, 'vector_store', store)
    else:
        monkeypatch.setattr(knowledge, 'search_agent_knowledge', lambda *a, **k: {'matches': []})
    calls = scripted(monkeypatch, script)
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, history=history or [
            ('outbound', 'Hi, I am reaching out about 1 Test St. Would you be open to a brief conversation?'),
            ('inbound', latest)])
        conversation_id = conversation.id
    service.process_ai_reply(conversation_id, latest)
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        state = {'handled_by': conversation.handled_by, 'ai_enabled': conversation.ai_enabled,
                 'lead_status': conversation.lead_status}
    return sent, calls, state


def _reviews(calls):
    return [json.loads(call['messages'][1]['content']) for call in calls if call['feature'] == 'sms.review']


def _first_reply_request(calls):
    return next(call for call in calls if call['feature'] == 'sms.reply')


# ---------------------------------------------------------------------------
# 1. No arbitrary character limits
# ---------------------------------------------------------------------------

def _sentence_text(length):
    base = ('Thanks for the details about the property and your timing, that really helps me understand your '
            'goals. ')
    text = (base * (length // len(base) + 1))[:length - 1].rstrip() + '.'
    return text


@pytest.mark.parametrize('length', [180, 240, 300, 450, 600])
def test_policy_never_rejects_a_reply_for_length_alone(length):
    text = _sentence_text(length)
    assert abs(len(text) - length) <= 2
    assert policy.find_reply_policy_violations(text, phone_source_known=False) == []


def test_only_the_provider_ceiling_is_a_length_block():
    assert policy.find_reply_policy_violations('a' * policy.SMS_PROVIDER_MAX_CHARS) == []
    assert policy.find_reply_policy_violations('a' * (policy.SMS_PROVIDER_MAX_CHARS + 1)) == ['over_provider_limit']


def test_segment_estimate_is_reported_not_enforced():
    assert policy.estimate_sms_segments('a' * 160) == {'encoding': 'GSM-7', 'characters': 160, 'segments': 1}
    assert policy.estimate_sms_segments('a' * 161)['segments'] == 2
    assert policy.estimate_sms_segments('a' * 600)['segments'] == 4
    # A curly apostrophe forces UCS-2, which has smaller segments.
    assert policy.estimate_sms_segments('I’m ' + 'a' * 80) == {'encoding': 'UCS-2', 'characters': 84, 'segments': 2}


def test_cost_estimate_appears_only_when_a_rate_is_configured(monkeypatch):
    monkeypatch.delenv('SMS_COST_PER_SEGMENT_USD', raising=False)
    assert 'estimated_cost_usd' not in policy.estimate_sms_segments('hello')
    monkeypatch.setenv('SMS_COST_PER_SEGMENT_USD', '0.004')
    assert policy.estimate_sms_segments('a' * 300)['estimated_cost_usd'] == 0.008


def test_prompts_carry_no_fixed_character_targets(three_agents):
    with db.SessionLocal() as session:
        conversation = _conversation(session, three_agents['third'][0].id)
        context = resolve_agent_runtime_context(session, conversation)
        prompt = build_agent_prompt(conversation, context, '', 'Hello')
    for text in (prompt, bobbie.REVIEW_SYSTEM, bobbie.review_evidence_rules(context)):
        assert '220' not in text and '240' not in text
    assert 'Usually 1-3 concise sentences' in prompt
    assert 'Length alone is never a reason to reject' in bobbie.review_evidence_rules(context)


@pytest.mark.parametrize('length', [180, 240, 300, 450, 600])
def test_generated_reply_is_never_truncated_and_reviewer_soft_length_note_passes(three_agents, monkeypatch, length):
    text = _sentence_text(length)
    monkeypatch.setattr(knowledge, 'search_agent_knowledge', lambda *a, **k: {'matches': []})
    calls = scripted(monkeypatch, {
        'sms.reply': [text],
        'sms.review': [_review(advisory=['longer than one SMS segment'], valid=False, issues=['too_long'])],
    })
    user = three_agents['third'][0]
    history = [{'direction': 'inbound', 'text': 'Tell me how you would approach selling my home?'}]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id)
        context = resolve_agent_runtime_context(session, conversation)
        draft = bobbie.generate_ai_reply(conversation, history, '', '', {'next_step': 'answer_only'},
                                         agent_context=context)
        final = bobbie.review_and_repair_ai_reply(conversation, history, draft, {'next_step': 'answer_only'},
                                                  agent_context=context)
    assert draft == text and final == text
    assert [call['feature'] for call in calls] == ['sms.reply', 'sms.review']


# ---------------------------------------------------------------------------
# 2. Reviewer: hard blocks reject, soft feedback never does
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('parsed, valid', [
    # Soft feedback only: passes even though the model said valid=false.
    ({'valid': False, 'blocking_issues': [], 'advisory_issues': ['style'], 'issues': ['wording']}, True),
    # A hard block listed only under `issues` still rejects.
    ({'valid': False, 'blocking_issues': [], 'advisory_issues': [],
      'issues': ['fabricated_valuation: says the home is worth $675,000']}, False),
    # valid=true with a hard block is treated as a rejection (fail closed).
    ({'valid': True, 'blocking_issues': ['consent_violation: ignored STOP'], 'advisory_issues': [],
      'issues': []}, False),
    # Style feedback filed as blocking is downgraded; an unknown label stays blocking.
    ({'valid': False, 'blocking_issues': ['length: longer than one segment', 'tone: a bit formal'],
      'advisory_issues': [], 'issues': []}, True),
    ({'valid': False, 'blocking_issues': ['claims a buyer is waiting'], 'advisory_issues': [], 'issues': []}, False),
    # Legacy response shape keeps its original meaning.
    ({'valid': False, 'issues': ['pressure']}, False),
    ({'valid': True, 'issues': []}, True),
])
def test_review_verdict_hard_blocks_only(parsed, valid):
    assert bobbie._review_verdict(parsed)['valid'] is valid


def test_reviewer_prompt_lists_hard_blocks_and_soft_feedback():
    for category in bobbie.HARD_BLOCK_CATEGORIES:
        assert category in bobbie.REVIEW_SYSTEM
    assert 'SOFT FEEDBACK' in bobbie.REVIEW_SYSTEM and 'not a script checker' in bobbie.REVIEW_SYSTEM
    assert 'a quick call, an in-person visit, or a choice between them are all acceptable' in bobbie.REVIEW_SYSTEM


def test_meeting_permission_is_one_rule_for_generator_policy_and_reviewer():
    assert policy.meeting_offer_permitted('offer_call', 'ok')
    assert policy.meeting_offer_permitted('answer_and_qualify', SELLER_MESSAGE)
    assert policy.meeting_offer_permitted('answer_and_qualify', 'What is my house worth?')
    assert not policy.meeting_offer_permitted('answer_and_qualify', 'Tell me about your team')
    assert not policy.meeting_offer_permitted('offer_call', SELLER_MESSAGE, 'call_declined')
    assert not policy.meeting_offer_permitted('close', SELLER_MESSAGE)
    history = [{'direction': 'inbound', 'text': SELLER_MESSAGE}]
    assert policy.find_reply_policy_violations(SELLER_REPLY, history=history, next_step='answer_and_qualify') == []
    declined = policy.find_reply_policy_violations(SELLER_REPLY, history=history, next_step='answer_and_qualify',
                                                   calendar_state='call_declined')
    assert 'premature_meeting_request' in declined


# ---------------------------------------------------------------------------
# 3. $675,000 seller intent
# ---------------------------------------------------------------------------

def test_seller_intent_and_target_price_are_classified():
    signals = seller_signals(SELLER_MESSAGE)
    assert signals == {'selling_intent': True, 'urgency': 'high', 'target_price': 675000,
                       'target_price_text': '$675,000', 'strong': True}
    assert classify_lead_message(SELLER_MESSAGE) == {'lead_status': 'ready_to_sell'}
    assert seller_signals("I don't want to sell right now")['selling_intent'] is False
    assert seller_signals('Will you guarantee $675,000?')['selling_intent'] is False


def test_fallback_disposition_offers_a_meeting_for_a_ready_seller():
    result = bobbie.fallback_disposition(SELLER_MESSAGE, RuntimeError('down'))
    assert result['action'] == 'continue' and result['next_step'] == 'offer_call'
    assert result['lead_status'] == 'ready_to_sell' and result['calendar']['state'] == 'none'


def test_seller_query_targets_advice_and_drops_unrelated_biography(three_agents, monkeypatch):
    user, _headers, _persona, version = three_agents['third']
    store = _seller_store(user, version['id'])
    monkeypatch.setattr(knowledge, 'vector_store', store)
    with db.SessionLocal() as session:
        context = resolve_agent_runtime_context(session, _conversation(session, user.id))
    query = knowledge.knowledge_query(SELLER_MESSAGE)
    assert query.startswith(SELLER_MESSAGE) and 'pricing approach' in query and 'appointment' in query
    result = knowledge.search_agent_knowledge(query, context, 3, relevance_filter=True)
    assert [match['id'] for match in result['matches']] == ['pricing']
    assert result['relevance_filter']['dropped'] == 3
    assert store.calls[0]['filters'] == {'owner_user_id': user.id, 'brokerage_id': user.brokerage_id,
                                         'published_version_id': version['id'], 'approval_status': 'eligible'}
    # Identity and track-record questions are searched as written: biography is relevant there.
    assert knowledge.knowledge_query('Who are you?') == 'Who are you?'
    track_record = knowledge.search_agent_knowledge('How many homes have you sold?', context, 6,
                                                    relevance_filter=True)
    assert 'relevance_filter' not in track_record and 'bio-sold' in [m['id'] for m in track_record['matches']]
    # Other callers (the /ai/reply route) are not filtered.
    assert 'relevance_filter' not in knowledge.search_agent_knowledge(query, context, 3)


def test_no_relevant_knowledge_still_allows_a_safe_reply(three_agents, monkeypatch):
    user, _headers, _persona, version = three_agents['third']
    store = FakeKnowledgeStore([_chunk('bio', 'Owner-confirmed information: Coaches soccer.', user, version['id'])])
    monkeypatch.setattr(knowledge, 'vector_store', store)
    with db.SessionLocal() as session:
        context = resolve_agent_runtime_context(session, _conversation(session, user.id))
    result = knowledge.search_agent_knowledge(knowledge.knowledge_query(SELLER_MESSAGE), context, 3,
                                              relevance_filter=True)
    assert result['matches'] == [] and 'without agent-specific claims' in result['reason']


def test_675k_message_end_to_end(client, make_user, auth_header, monkeypatch, caplog):
    user, _headers, _persona, version = _publish_with_examples(
        client, make_user, auth_header, 'seller-agent@example.com', 'b-seller', 'Dana Rivera',
        [GREETING_EXAMPLE, PRICING_EXAMPLE], brokerage_name='Rivera Homes')
    store = _seller_store(user, version['id'])
    caplog.set_level(logging.INFO, logger='app.sms.bobbie')
    sent, calls, state = _run_pipeline(monkeypatch, user.id, SELLER_MESSAGE, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call', lead_status='ready_to_sell')],
        'sms.reply': [SELLER_REPLY],
        'sms.review': [_review(advisory=['could mention the timeline'])],
    }, store=store)

    # Reply sent as written; the AI keeps the conversation.
    assert sent == [(SELLER_REPLY, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']
    assert state['lead_status'] == 'ready_to_sell'

    # A seller stating a price is answered without agent-knowledge search (no preload, no tool).
    assert store.calls == []
    request = _first_reply_request(calls)
    assert [tool['function']['name'] for tool in request['options']['tools']] == ['get_lead_details']
    runtime = json.loads(request['messages'][1]['content'].split('Approved runtime context (facts only): ', 1)[1])
    assert runtime['agent_knowledge']['available'] is False
    assert runtime['owner_stated']['stated_price'] == '$675,000' and runtime['owner_stated']['urgency'] == 'high'

    # The reviewer saw the final candidate and that a meeting offer is permitted.
    review = _reviews(calls)
    assert len(review) == 1 and review[0]['proposed_agent_reply'] == SELLER_REPLY
    assert review[0]['disposition']['meeting_offer_permitted'] is True
    assert 'approved_knowledge_evidence' not in review[0]

    # Disposition control tells the writer a call or visit is allowed.
    control = ' '.join(m['content'] for m in request['messages'] if m['role'] == 'system')
    assert 'Meeting offer permitted in this reply: yes' in control

    # Dynamic Persona: the approved pricing example ranks first in the live prompt.
    prompt = request['messages'][0]['content']
    assert prompt.index(PRICING_EXAMPLE['preferred_response']) < prompt.index(GREETING_EXAMPLE['preferred_response'])
    assert f'"active_published_version_id":{version["id"]}' in prompt

    # Privacy-safe diagnostic: version and counts, never example text.
    persona_logs = [record.getMessage() for record in caplog.records if '[ai-persona]' in record.getMessage()]
    assert persona_logs and f'persona_version_id={version["id"]}' in persona_logs[0]
    assert 'examples_included=2' in persona_logs[0]
    assert PRICING_EXAMPLE['preferred_response'] not in persona_logs[0]


def test_675k_with_cautious_disposition_still_allows_a_meeting_offer(three_agents, monkeypatch):
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id, SELLER_MESSAGE, {
        'sms.disposition': [_disposition('answer_and_qualify')],
        'sms.reply': [SELLER_REPLY],
        'sms.review': [VALID],
    })
    assert sent == [(SELLER_REPLY, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert _reviews(calls)[0]['disposition']['meeting_offer_permitted'] is True
    assert not any(call['feature'] == 'sms.reply.policy_repair' for call in calls)


def test_675k_fallback_acknowledges_goal_and_price():
    text = policy.safe_grounded_fallback(SELLER_MESSAGE, [], [], {'next_step': 'offer_call'}, 'Dana Rivera')
    assert '$675,000' in text and 'sell quickly' in text and 'quick visit' in text
    assert 'When would be a convenient time?' in text


# ---------------------------------------------------------------------------
# 5. Regression scenarios A-L
# ---------------------------------------------------------------------------

def test_a_who_are_you_gets_a_natural_introduction(three_agents, monkeypatch):
    intro = "I'm Dana Rivera with Rivera Homes. I help owners in the area sell their homes. Thanks for replying!"
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'Who are you?', {
        'sms.disposition': [_disposition('answer_only')], 'sms.reply': [intro], 'sms.review': [VALID]})
    assert sent == [(intro, 'ai.reply')] and state['handled_by'] == 'bobbie'
    prompt = _first_reply_request(calls)['messages'][0]['content']
    assert 'Speak in first person as Dana Rivera' in prompt and 'Rivera Homes' in prompt


def test_b_are_you_an_ai_requires_truthful_disclosure(three_agents, monkeypatch):
    disclosed = ('Good question. These replies are automated (AI) on Dana Rivera’s behalf, '
                 'and I can still help with questions about your property.')
    sent, calls, _state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'Are you an AI?', {
        'sms.disposition': [_disposition('answer_only')],
        'sms.reply': ["Hi, it's Dana here!"],
        'sms.reply.policy_repair': [disclosed],
        'sms.review': [VALID]})
    assert sent == [(disclosed, 'ai.reply')]
    repair = next(call for call in calls if call['feature'] == 'sms.reply.policy_repair')
    assert 'missing_ai_disclosure' in repair['messages'][-1]['content']


def test_d_guarantee_question_never_gets_a_guarantee(three_agents, monkeypatch):
    honest = ('I can’t guarantee $675,000; buyers and the market set the final number. I’d want to see the property '
              'and recent comparable sales first. Would a quick call or a visit work better?')
    assert policy.find_reply_policy_violations('Yes, I guarantee you will get $675,000.') == ['unsupported_guarantee']
    assert 'unsupported_guarantee' not in policy.find_reply_policy_violations(honest)
    sent, calls, _state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'Will you guarantee $675,000?', {
        'sms.disposition': [_disposition('answer_only')],
        'sms.reply': ['Absolutely, I guarantee $675,000 for your home.'],
        'sms.reply.policy_repair': [honest],
        'sms.review': [VALID]})
    assert sent == [(honest, 'ai.reply')]
    fallback = policy.safe_grounded_fallback('Will you guarantee $675,000?', ['unsupported_guarantee'], [],
                                             {'next_step': 'answer_only'})
    assert fallback.startswith('I can’t guarantee a sale price')


def test_e_negotiation_request_is_answered_then_escalated_to_a_human(three_agents, monkeypatch):
    reply = ('That’s something I handle directly with you rather than by automated text. '
             'Someone from my team will text you shortly.')
    assert policy.escalation_reason('Can you negotiate the sale for me?') == 'negotiation'
    assert policy.escalation_reason('Who are you?') == ''
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id,
                                       'Can you negotiate the sale for me?', {
                                           'sms.disposition': [_disposition('answer_only')],
                                           'sms.reply': [reply], 'sms.review': [VALID]})
    assert sent == [(reply, 'ai.reply')]
    control = ' '.join(m['content'] for m in _first_reply_request(calls)['messages'] if m['role'] == 'system')
    assert 'Do not negotiate' in control
    assert state['handled_by'] == 'broker' and not state['ai_enabled']


def test_f_long_valid_reply_is_sent_without_length_rejection(three_agents, monkeypatch):
    long_reply = _sentence_text(320)
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id,
                                       'How would you approach selling my place?', {
                                           'sms.disposition': [_disposition('answer_only')],
                                           'sms.reply': [long_reply],
                                           'sms.review': [_review(advisory=['longer than one segment'],
                                                                  valid=False, issues=['too_long'])]})
    assert sent == [(long_reply, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert len(_reviews(calls)) == 1


def test_g_same_brokerage_agents_never_share_examples_or_knowledge(client, make_user, auth_header, monkeypatch):
    first = _publish_with_examples(client, make_user, auth_header, 'a1@example.com', 'b-shared', 'Avery Stone', [
        {**PRICING_EXAMPLE, 'preferred_response': 'AVERY ONLY: pricing starts with a walkthrough.'}])
    second = _publish_with_examples(client, make_user, auth_header, 'a2@example.com', 'b-shared', 'Blake Moss', [
        {**PRICING_EXAMPLE, 'preferred_response': 'BLAKE ONLY: let me check comps for you.'}])
    assert first[0].brokerage_id == second[0].brokerage_id
    store = FakeKnowledgeStore([
        _chunk('avery-pricing', 'Owner-confirmed information: Avery pricing approach.', first[0], first[3]['id']),
        _chunk('blake-pricing', 'Owner-confirmed information: Blake pricing approach.', second[0], second[3]['id']),
    ])
    monkeypatch.setattr(knowledge, 'vector_store', store)
    with db.SessionLocal() as session:
        for (user, *_rest), own, other in ((first, 'AVERY ONLY', 'BLAKE ONLY'), (second, 'BLAKE ONLY', 'AVERY ONLY')):
            conversation = _conversation(session, user.id, contact=f'+1555000{user.id:04d}')
            context = resolve_agent_runtime_context(session, conversation)
            prompt = build_agent_prompt(conversation, context, '', SELLER_MESSAGE)
            assert own in prompt and other not in prompt
            matches = knowledge.search_agent_knowledge(knowledge.knowledge_query(SELLER_MESSAGE), context)['matches']
            assert matches and all(match['owner_user_id'] == user.id for match in matches)


def test_h_active_persona_version_change_updates_live_examples(client, make_user, auth_header):
    user, headers, persona, v1 = _publish_with_examples(client, make_user, auth_header, 'ver@example.com', 'b-ver',
                                                        'Casey Lane', [GREETING_EXAMPLE])
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id)
        before = resolve_agent_runtime_context(session, conversation)
        assert persona_prompt_diagnostics(before, SELLER_MESSAGE)['persona_version_id'] == v1['id']
        draft = client.get(f'{API}/{persona["id"]}/draft', headers=headers).json()
        saved = client.put(f'{API}/{persona["id"]}/examples', headers=headers,
                           json={'revision': draft['revision'], 'examples': [GREETING_EXAMPLE, PRICING_EXAMPLE]})
        v2 = client.post(f'{API}/{persona["id"]}/versions', headers=headers,
                         json={'revision': saved.json()['revision']}).json()
        assert not runtime_context_is_current(session, conversation, before)
        after = resolve_agent_runtime_context(session, conversation)
        info = persona_prompt_diagnostics(after, SELLER_MESSAGE)
        assert info['persona_version_id'] == v2['id'] != v1['id'] and info['examples_available'] == 2
        assert PRICING_EXAMPLE['preferred_response'] in build_agent_prompt(conversation, after, '', SELLER_MESSAGE)
        assert PRICING_EXAMPLE['preferred_response'] not in build_agent_prompt(conversation, before, '', SELLER_MESSAGE)


def test_i_only_owner_approved_published_sms_examples_reach_the_prompt(client, make_user, auth_header):
    user, headers, persona, version = _publish_with_examples(client, make_user, auth_header, 'appr@example.com',
                                                             'b-appr', 'Jordan Pike', [GREETING_EXAMPLE])
    with db.SessionLocal() as session:
        session.add_all([
            PersonaVersionExample(version_id=version['id'], position=50, scenario='Unapproved', channel='sms',
                                  language='en', customer_message='sell price?', preferred_response='UNAPPROVED TEXT',
                                  context='', style_explanation='', provenance='imported', owner_approved=0),
            PersonaVersionExample(version_id=version['id'], position=51, scenario='Email only', channel='email',
                                  language='en', customer_message='sell price?', preferred_response='EMAIL TEXT',
                                  context='', style_explanation='', provenance='user_written', owner_approved=1),
        ])
        session.commit()
    # An unpublished draft example must not reach the live prompt either.
    draft = client.get(f'{API}/{persona["id"]}/draft', headers=headers).json()
    client.put(f'{API}/{persona["id"]}/examples', headers=headers, json={
        'revision': draft['revision'], 'examples': [{**PRICING_EXAMPLE, 'preferred_response': 'DRAFT ONLY TEXT'}]})
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id)
        context = resolve_agent_runtime_context(session, conversation)
        prompt = build_agent_prompt(conversation, context, '', SELLER_MESSAGE)
    assert GREETING_EXAMPLE['preferred_response'] in prompt
    for leaked in ('UNAPPROVED TEXT', 'EMAIL TEXT', 'DRAFT ONLY TEXT'):
        assert leaked not in prompt
    assert [example['preferred_response'] for example in context.examples] == [GREETING_EXAMPLE['preferred_response']]


def test_examples_are_ranked_by_relevance_to_the_latest_message(three_agents):
    with db.SessionLocal() as session:
        context = resolve_agent_runtime_context(session, _conversation(session, three_agents['third'][0].id))
    from dataclasses import replace
    context = replace(context, examples=(dict(GREETING_EXAMPLE, id=1), dict(PRICING_EXAMPLE, id=2)))
    assert [item['id'] for item in relevant_examples(context, SELLER_MESSAGE)] == [2, 1]
    assert [item['id'] for item in relevant_examples(context, '')] == [1, 2]


def test_j_final_policy_repaired_and_regenerated_candidates_are_reviewed(three_agents, monkeypatch):
    repaired = 'I can’t guarantee a number, but I’d be glad to look at the property with you.'
    regenerated = 'Happy to help. Pricing depends on the home and recent sales, so I’d start with a quick look.'
    sent, calls, _state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'Will you guarantee $675,000?', {
        'sms.disposition': [_disposition('answer_only')],
        'sms.reply': ['I guarantee $675,000.', regenerated],
        'sms.reply.policy_repair': [repaired],
        'sms.review': [_review(blocking=['guarantee_or_misleading: implies the price is assured']), VALID]})
    assert [item['proposed_agent_reply'] for item in _reviews(calls)] == [repaired, regenerated]
    assert sent == [(regenerated, 'ai.reply')]


def test_k_stop_is_honored_before_any_model_call(three_agents, monkeypatch):
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'STOP', {})
    assert calls == []
    assert sent == [('Understood — I’ll remove you from my outreach list. Take care.', 'ai.reply')]
    assert state['handled_by'] == 'broker' and not state['ai_enabled'] and state['lead_status'] == 'dnc'
    assert bobbie._review_verdict({'valid': True, 'blocking_issues': ['consent_violation: kept messaging'],
                                   'advisory_issues': [], 'issues': []})['valid'] is False


def test_l_minor_reviewer_preferences_never_cause_a_silent_handoff(three_agents, monkeypatch):
    draft = 'Thanks for asking. I focus on helping owners understand their options before they decide anything.'
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id, 'What do you do exactly?', {
        'sms.disposition': [_disposition('answer_only')],
        'sms.reply': [draft],
        'sms.review': [_review(advisory=['could add a closing question', 'sign-off differs from Persona'],
                               valid=False, issues=['style'])]})
    assert sent == [(draft, 'ai.reply')]
    assert len(_reviews(calls)) == 1
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']


def test_l_hard_blocks_still_fail_closed_after_bounded_regeneration(three_agents, monkeypatch):
    hard = _review(blocking=['fabricated_valuation: states the home is worth $700k'])
    sent, calls, state = _run_pipeline(monkeypatch, three_agents['third'][0].id, SELLER_MESSAGE, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': ['Your home is worth $700k.', 'It is worth $700k.', 'Worth $700k for sure.'],
        'sms.review': [hard, hard, hard]})
    assert sent == []
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS
    assert state['handled_by'] == 'broker' and not state['ai_enabled']
