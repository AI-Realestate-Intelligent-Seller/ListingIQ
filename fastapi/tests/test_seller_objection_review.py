"""Replay of conversation 10 / inbound message 73: a seller objection answer was
rejected by the reviewer three times and handed to a human.

Fully offline: model calls are scripted per feature, knowledge search runs the
real owner-scoped filter over a fake in-memory store, and SMS delivery is
captured. No provider, vector database or SMS gateway is reached.
"""

import json
import logging

from app import db
from app.models import Conversation, Message
from app.personas import template_parser
from app.sms import bobbie, knowledge, policy, service
from app.sms.runtime_context import resolve_agent_runtime_context

from test_personal_ai_runtime_e2e import (  # noqa: F401  (fixtures are used by name)
    _conversation,
    _publish_manual,
    _republish,
    offline,
    scripted,
)
from test_sms_ai_quality_reviewer import FakeKnowledgeStore, _chunk, _disposition, _review

# The exact thread of conversation_id=10 up to inbound_message_id=73.
HISTORY = [
    ('outbound', 'Hey Testing, I’m reaching out about 10th ln Estates, Chicago, IL 60006. I see your property came '
                 'off market without any sale. Would you be open to a brief conversation? Claude Tester – Claude '
                 'Test Brokerage'),
    ('inbound', "Yeah, I'm still thinking about selling. The last agent couldn't get it sold."),
    ('outbound', "Thanks for being open about that. A listing that doesn't sell is frustrating, and it's a good "
                 "reason to look at things fresh. I'm with Claude Test Brokerage, and before I can say anything "
                 "useful about the next steps, I'd like to understand what happened. Do you know why it didn't "
                 "sell, or what outcome you're hoping for this time?"),
    ('inbound', "I want to sell quickly, but I don't want to sell it cheap. I'm hoping for around $675,000. What "
                "do you think?"),
    ('outbound', "I hear you: you want a quick sale without giving it away, and $675,000 is the number you're "
                 "hoping for. I can't tell you whether that's right by text, because I don't want to guess. A real "
                 "recommendation needs a look at the property in person and a review of comparable sales. I'd like "
                 "to stop by for a quick, no-obligation visit so I can see the condition and talk through your "
                 "options. When would be convenient for you?"),
    ('inbound', 'But what exactly would you do differently? My last agent just listed the house online and waited. '
                'Why should I trust your approach?'),
]
LATEST = HISTORY[-1][1]
SIGNATURE = "I'd rather give you a number we can actually close at."
# A safe, substantially responsive 301-character answer in the Persona's voice.
ORIGINAL_REPLY = ("It's a fair question. I wouldn't just list it and wait. I'd see the house in person, review recent "
                  "comparable sales, and set a price buyers will actually pay, then keep you updated on every "
                  f"showing and offer. {SIGNATURE} Want me to walk you through the plan?")
AVOIDED = ['Pressure tactics or arguing with an owner about price.', 'Criticizing another agent.']
# What the reviewer returned in the failed localhost run.
OBSERVED_REJECTION = _review(blocking=[
    "non_responsive: draft does not directly and clearly answer the owner's question about what makes this",
    'non_responsive: owner asked what the agent would do differently; draft answers generally but does no'])


def _michael_style_agent(client, make_user, auth_header, email='claude-tester@example.com'):
    user, headers, persona, _version = _publish_manual(
        client, make_user, auth_header, email, 'brokerage-claude-test', 'Claude Tester',
        brokerage_name='Claude Test Brokerage')
    version = _republish(
        client, headers, persona['id'],
        professional_title='Founder & Team Lead, SpaceMatch Inc. (SpaceMatch Chicago)',
        style_config={'tone': 'conversational', 'preferred_phrases': ['real numbers'], 'avoided_phrases': AVOIDED},
        profile_config={
            'identity': {'license_number': f'TEST-{user.id}', 'licensing_jurisdiction': 'Test State',
                         'licensing_status': 'Active'},
            'communication': {'signature_phrases': [SIGNATURE, "Let's look at the real numbers first."]},
        })
    return user, version


def _store(user, version_id, *extra):
    return FakeKnowledgeStore([
        _chunk('license', 'Owner-confirmed information: Licensed Illinois real estate broker (IDFPR).',
               user, version_id, score=0.2),
        _chunk('bio', 'Owner-confirmed information: Background in IT and construction; founded a digital '
                      'staging company.', user, version_id, score=0.25),
        *extra,
    ])


def _run(monkeypatch, user_id, script, store, history=HISTORY):
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or Message(id=1))
    monkeypatch.setattr(knowledge, 'vector_store', store)
    calls = scripted(monkeypatch, script)
    with db.SessionLocal() as session:
        conversation_id = _conversation(session, user_id, history=history).id
    service.process_ai_reply(conversation_id, history[-1][1])
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        state = {'handled_by': conversation.handled_by, 'ai_enabled': conversation.ai_enabled}
    return sent, calls, state


def _reviews(calls):
    return [json.loads(call['messages'][1]['content']) for call in calls if call['feature'] == 'sms.review']


def _history_dicts(history=HISTORY):
    return [{'direction': direction, 'text': text} for direction, text in history]


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def test_replay_conversation_10_sends_the_first_safe_reply(client, make_user, auth_header, monkeypatch, caplog):
    user, version = _michael_style_agent(client, make_user, auth_header)
    store = _store(user, version['id'])
    caplog.set_level(logging.INFO)
    # The provider responses observed in the failed run: call_declined, then a
    # reviewer that files "answers generally" as blocking non_responsive.
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'call_requested', calendar='call_declined',
                                         lead_status='ready_to_sell')],
        'sms.reply': [ORIGINAL_REPLY],
        'sms.review': [OBSERVED_REJECTION],
    }, store)

    assert len(ORIGINAL_REPLY) == 301
    # The first safe reply is sent and stored; the AI keeps the thread.
    assert sent == [(ORIGINAL_REPLY, 'ai.reply')]
    assert state == {'handled_by': 'bobbie', 'ai_enabled': True}
    messages = [record.getMessage() for record in caplog.records]
    assert not any('review_exhausted' in message for message in messages)
    assert not any('AI_HANDOFF' in message or 'handed_to_human' in message for message in messages)

    # One review, no regeneration; the quality notes stayed advisory.
    reviews = _reviews(calls)
    assert len(reviews) == 1 and [c['feature'] for c in calls].count('sms.reply') == 1

    # The challenge was not treated as a declined meeting.
    assert reviews[0]['disposition']['calendar']['state'] == 'none'
    control = ' '.join(m['content'] for m in calls[1]['messages'] if m['role'] == 'system')
    assert 'Calendar state: none' in control and 'declined or deferred a meeting' not in control
    assert 'A meeting invitation is optional here' in control

    # Persona: the signature phrase survives and the reviewer is told it is approved.
    assert SIGNATURE in sent[0][0]
    assert SIGNATURE in reviews[0]['approved_persona_phrases']
    prompt = next(call for call in calls if call['feature'] == 'sms.reply')['messages'][0]['content']
    assert '"preferred_vocabulary":["real numbers"]' in prompt
    assert all(json.dumps(item, ensure_ascii=False) in prompt for item in AVOIDED)
    persona_log = next(message for message in messages if '[ai-persona] feature=sms.reply' in message)
    assert 'preferred_vocabulary=1' in persona_log and 'avoided_expressions=2' in persona_log

    # Trusted identity stays separate from imported title text.
    identity = json.loads(prompt.split('ACTIVE PUBLISHED PERSONA', 1)[1].split('\n', 1)[1])['identity']
    assert identity == {'name': 'Claude Tester', 'brokerage': 'Claude Test Brokerage',
                        'trust': 'trusted authenticated account'}
    assert '"self_described_role":{"professional_title":"Founder & Team Lead, SpaceMatch Inc.' in prompt

    # Knowledge searched once; licensing and biography are not offered as approach evidence.
    assert len(store.calls) == 1
    runtime = json.loads(calls[1]['messages'][1]['content'].split('Approved runtime context (facts only): ', 1)[1])
    assert runtime['agent_knowledge']['matches'] == []
    assert runtime['agent_knowledge']['relevance_filter'] == {'topic': 'agent_approach', 'retrieved': 2,
                                                              'dropped': 2}
    assert 'approved_knowledge_evidence' not in reviews[0]


def test_fabricated_marketing_claim_is_rejected_then_repaired_conservatively(
        client, make_user, auth_header, monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'repair@example.com')
    approach = _chunk('approach', 'Owner-confirmed information: My listing process starts with a walkthrough and '
                                  'a comparative market analysis.', user, version['id'])
    store = _store(user, version['id'], approach)
    # Passes the deterministic policy; only the reviewer can catch it.
    fabricated = ("I'd run professional photography, a 3D tour and paid social ads to my 35K followers. {} "
                  'Want the plan?').format(SIGNATURE)
    repaired = ("I'd start with a walkthrough and a comparative market analysis so the price is one buyers will "
                f'pay. {SIGNATURE} Want the plan?')
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [fabricated, repaired],
        'sms.review': [_review(blocking=['unsupported_claim: photography, 3D tour and ad reach are not in evidence']),
                       _review()],
    }, store)

    assert sent == [(repaired, 'ai.reply')] and state['handled_by'] == 'bobbie'
    reviews = _reviews(calls)
    assert [review['proposed_agent_reply'] for review in reviews] == [fabricated, repaired]
    # The exact same evidence snapshot reached both reviews, and nothing was searched twice.
    assert len(store.calls) == 1
    assert reviews[0]['approved_knowledge_evidence'] == reviews[1]['approved_knowledge_evidence']
    assert [m['id'] for m in reviews[0]['approved_knowledge_evidence'][0]['matches']] == ['approach']
    # Regeneration is a conservative edit of the rejected draft, with no new search tool.
    regeneration = [call for call in calls if call['feature'] == 'sms.reply'][1]
    control = ' '.join(m['content'] for m in regeneration['messages'] if m['role'] == 'system')
    assert 'smallest change that fixes only these issues' in control and json.dumps(fabricated) in control
    assert [tool['function']['name'] for tool in regeneration['options']['tools']] == ['get_lead_details']


def test_fabricated_claims_still_fail_closed(client, make_user, auth_header, monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'closed@example.com')
    hard = _review(blocking=['unsupported_claim: invents a list of ready buyers'])
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': ['I have 200 buyers ready for it.'] * 3,
        'sms.review': [hard, hard, hard],
    }, _store(user, version['id']))
    assert sent == [] and state == {'handled_by': 'broker', 'ai_enabled': False}
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

def test_quality_preferences_are_advisory_and_hard_blocks_are_not():
    verdict = bobbie._review_verdict({
        'valid': False, 'advisory_issues': [], 'issues': [],
        'blocking_issues': ['non_responsive: answers generally', 'missing_next_step: no visit invitation',
                            'too_generic: could differentiate more', 'more_detail: name concrete steps']})
    assert verdict['valid'] is True and len(verdict['advisory_issues']) == 4
    for issue in ('unsupported_claim: claims professional staging', 'invented_commitment: invents a buyer',
                  'false_identity: wrong brokerage', 'guarantee_or_misleading: guarantees $675,000',
                  'consent_violation: ignored STOP', 'invented_commitment: says the visit is booked',
                  'ignores_message: talks about the weather', 'claims a buyer is waiting'):
        assert bobbie._review_verdict({'valid': True, 'blocking_issues': [issue], 'advisory_issues': [],
                                       'issues': []})['valid'] is False, issue
    for text in ('CLAIM CLASSES', 'approved_persona_phrases', 'never owed an elaborate marketing pitch',
                 'smallest edit', 'it never requires one'):
        assert text in bobbie.REVIEW_SYSTEM


def test_challenge_is_not_call_declined_but_a_real_refusal_is_kept(monkeypatch):
    class Conv:
        contact, property_address, lead_status = '+13125550695', '10th ln Estates', 'ready_to_sell'

    def disposition(history):
        scripted(monkeypatch, {'sms.disposition': [_disposition('answer_and_qualify', 'call_requested',
                                                                calendar='call_declined')]})
        return bobbie.analyze_conversation_disposition(Conv(), history)['calendar']['state']

    assert disposition(_history_dicts()) == 'none'
    refused = HISTORY[:3] + [('inbound', "I don't want anyone coming by, and no calls. Just text me.")] + HISTORY[4:]
    assert disposition(_history_dicts(refused)) == 'call_declined'
    assert not policy.owner_declined_meeting(_history_dicts())
    for text in ('No thanks', 'Not right now', "I'd rather not meet", 'Please stop calling me',
                 "Don't come to the house", 'Text only please'):
        assert policy.owner_declined_meeting([{'direction': 'inbound', 'text': text}]), text


def test_approach_question_drops_licensing_and_biography(client, make_user, auth_header, monkeypatch):
    user, version = _michael_style_agent(client, make_user, auth_header, 'filter@example.com')
    approach = _chunk('approach', 'Owner-confirmed information: My listing process starts with a walkthrough.',
                      user, version['id'])
    store = _store(user, version['id'], approach)
    monkeypatch.setattr(knowledge, 'vector_store', store)
    with db.SessionLocal() as session:
        context = resolve_agent_runtime_context(session, _conversation(session, user.id))
    query = knowledge.knowledge_query(LATEST)
    assert query.startswith(LATEST) and 'marketing approach' in query
    result = knowledge.search_agent_knowledge(query, context, 3, relevance_filter=True)
    assert [match['id'] for match in result['matches']] == ['approach']
    # Identity questions keep biography.
    assert 'relevance_filter' not in knowledge.search_agent_knowledge('Who are you?', context, 3,
                                                                      relevance_filter=True)


def test_avoid_list_with_labelled_items_is_imported_completely():
    document = '\n'.join([
        '# Communication DNA',
        '- Vocabulary and signature phrases to use, plus expressions to avoid:',
        '  - Use:',
        '    - "Let\'s look at the real numbers first."',
        f'    - "{SIGNATURE}"',
        '    - Style: short and direct.',
        '  - Avoid:',
        '    - Hype: "amazing opportunity!!!", "act now", "guaranteed".',
        '    - Strings of emoji or multiple exclamation points.',
        '    - Criticizing another agent.',
        '    - Long corporate intros or generic filler.',
        '- How I communicate uncertainty, negative replies, and next steps:',
        '  - Uncertainty: Never guesses a value.',
    ])
    builder = template_parser._Builder()
    sections = template_parser._split_sections(document)
    template_parser._communication(template_parser._fields('communication', sections['communication'], builder),
                                   builder)
    data = {'style_config': builder.style, 'profile_config': builder.profile}
    assert data['style_config']['avoided_phrases'] == [
        'Hype: "amazing opportunity!!!", "act now", "guaranteed".',
        'Strings of emoji or multiple exclamation points.', 'Criticizing another agent.',
        'Long corporate intros or generic filler.']
    communication = data['profile_config']['communication']
    assert communication['signature_phrases'] == ["Let's look at the real numbers first.", SIGNATURE]
    assert not communication.get('industry_terminology')
    assert communication['uncertainty_behavior'] == 'Never guesses a value.'
