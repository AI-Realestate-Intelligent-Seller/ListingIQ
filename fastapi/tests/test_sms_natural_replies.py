"""Natural, concise SMS replies; no repeated sign-off; Persona phrases as voice, not facts.

Fully offline: model calls are scripted per feature, knowledge search uses a
fake in-memory store, and SMS delivery is captured.
"""

import json
import logging

from app import db
from app.sms import bobbie, knowledge, policy
from app.sms.outreach import build_initial_outreach
from app.sms.prompt import NATURAL_SMS_RULES, PERSONA_STYLE_RULES
from app.sms.runtime_context import resolve_agent_runtime_context

from test_personal_ai_runtime_e2e import (  # noqa: F401  (fixtures are used by name)
    _conversation,
    _publish_manual,
    _republish,
    offline,
    scripted,
)
from test_seller_objection_review import _run, _reviews
from test_sms_ai_quality_reviewer import FakeKnowledgeStore, _chunk, _disposition, _review

OUTREACH = ('Hey Testing, I’m reaching out about 120th ln, Chicago, IL 60007. I see your property came off market '
            'without any sale. Would you be open to a brief conversation? Claude Tester – Claude Test Brokerage')
NOT_SOLD = "Yeah, I'm still thinking about selling. I had it listed for a while, but it didn't sell."
PREFERRED = 'I see. Were you getting offers below your asking price, or not getting offers at all?'
SIGNATURE_LINE = 'Claude Tester – Claude Test Brokerage'

# Michael Kang Persona "Use" and "Avoid" lists as the fixed parser stores them.
PHRASES = [
    "We don't list homes — we launch assets.",
    "Let's look at the real numbers first.",
    'No pressure. Just want you to know where you stand.',
    "I'd rather give you a number we can actually close at.",
    "We package the whole thing: building, business and income. That's how it gets financed.",
    'Step one is simple: a quick 10-minute call.',
    "There's flexibility on structure. Let's find what works for you.",
    "I've owned and run businesses myself, so I know what buyers look for.",
]
AVOIDED = [
    'Hype: "amazing opportunity!!!", "act now", "guaranteed", "don\'t miss out".',
    'Strings of emoji or multiple exclamation points.',
    'Promising a specific sale price or outcome.',
    'Guessing or inventing a value, sale, or buyer interest.',
    'Legal, tax or lending advice.',
    'Pressure tactics or arguing with an owner about price.',
    'Criticizing another agent.',
    'Long corporate intros or generic filler.',
]


def _agent(client, make_user, auth_header, email):
    user, headers, persona, _version = _publish_manual(
        client, make_user, auth_header, email, 'brokerage-claude-test', 'Claude Tester',
        brokerage_name='Claude Test Brokerage')
    version = _republish(
        client, headers, persona['id'],
        style_config={'tone': 'conversational', 'avoided_phrases': AVOIDED},
        profile_config={
            'identity': {'license_number': f'TEST-{user.id}', 'licensing_jurisdiction': 'Test State',
                         'licensing_status': 'Active'},
            'communication': {'signature_phrases': PHRASES},
        })
    return user, version


def _store(user, version_id):
    return FakeKnowledgeStore([_chunk('bio', 'Owner-confirmed information: Sold about 120 laundromats.',
                                      user, version_id, score=0.2)])


def _thread(latest):
    return [('outbound', OUTREACH), ('inbound', latest)]


def _system_text(call):
    return ' '.join(m['content'] for m in call['messages'] if m['role'] == 'system')


def _reply_calls(calls):
    return [call for call in calls if call['feature'] == 'sms.reply']


def test_unsold_listing_gets_a_concise_relevant_reply(client, make_user, auth_header, monkeypatch):
    user, version = _agent(client, make_user, auth_header, 'unsold@example.com')
    store = _store(user, version['id'])
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [PREFERRED],
        'sms.review': [_review()],
    }, store, history=_thread(NOT_SOLD))
    assert sent == [(PREFERRED, 'ai.reply')] and state == {'handled_by': 'bobbie', 'ai_enabled': True}
    request = _reply_calls(calls)[0]
    prompt = request['messages'][0]['content']
    # The writing principles reach the generator, with no fixed character limit.
    assert NATURAL_SMS_RULES in prompt and 'never a limit' in prompt
    for phrase in ('"Thanks for sharing"', '"I hear you"', 'that sounds frustrating', 'answer every part'):
        assert phrase in prompt
    # A statement about the listing is not a question about the agent: no biography lookup.
    assert store.calls == []
    assert [tool['function']['name'] for tool in request['options']['tools']] == ['get_lead_details']


def test_outreach_is_signed_but_conversational_replies_are_not(client, make_user, auth_header, monkeypatch,
                                                                caplog):
    user, version = _agent(client, make_user, auth_header, 'signoff@example.com')
    with db.SessionLocal() as session:
        context = resolve_agent_runtime_context(session, _conversation(session, user.id))
    outreach = build_initial_outreach('Testing Owner', '1 Test St', 'I see it came off market', context)
    assert outreach.endswith(SIGNATURE_LINE)

    caplog.set_level(logging.INFO)
    signed = f'{PREFERRED} {SIGNATURE_LINE}'
    sent, calls, _state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [signed],
        'sms.review': [_review()],
    }, _store(user, version['id']), history=_thread(NOT_SOLD))
    assert sent == [(PREFERRED, 'ai.reply')]
    assert _reviews(calls)[0]['proposed_agent_reply'] == PREFERRED
    assert 'trailing_signature=removed' in caplog.text
    prompt = _reply_calls(calls)[0]['messages'][0]['content']
    assert 'never append it, or any name or brokerage sign-off, to a conversational reply' in prompt


def test_signature_guard_only_removes_a_standalone_trailing_sign_off():
    name, brokerage = 'Claude Tester', 'Claude Test Brokerage'
    strip = policy.strip_trailing_signature
    assert strip(f'Got it. {SIGNATURE_LINE}', name, brokerage) == 'Got it.'
    assert strip('Got it. — Claude Tester', name, brokerage) == 'Got it.'
    # Identification sentences, a lone sign-off and opt-out wording are kept.
    for text in ('This is Claude Tester with Claude Test Brokerage.', SIGNATURE_LINE,
                 'Talk soon, Claude Tester.'):
        assert strip(text, name, brokerage) == text
    assert strip('Reply STOP to opt out. Claude Tester', name, brokerage) == 'Reply STOP to opt out.'


def test_persona_phrases_and_avoided_expressions_reach_generator_and_reviewer(
        client, make_user, auth_header, monkeypatch, caplog):
    user, version = _agent(client, make_user, auth_header, 'persona-v3@example.com')
    caplog.set_level(logging.INFO)
    reply = "Let's look at the real numbers first. Were you getting offers, or no offers at all?"
    sent, calls, _state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [reply],
        'sms.review': [_review()],
    }, _store(user, version['id']), history=_thread(NOT_SOLD))
    assert sent == [(reply, 'ai.reply')]
    prompt = _reply_calls(calls)[0]['messages'][0]['content']
    persona = json.loads(prompt.split('ACTIVE PUBLISHED PERSONA', 1)[1].split('\n', 1)[1])
    assert persona['active_published_version_id'] == version['id']
    assert persona['identity'] == {'name': 'Claude Tester', 'brokerage': 'Claude Test Brokerage',
                                   'trust': 'trusted authenticated account'}
    assert persona['communication_style']['signature_phrases'] == PHRASES
    assert persona['communication_style']['avoided_expressions'] == AVOIDED
    # Phrases are voice examples, not mandatory sentences or proof.
    assert 'contextual voice examples, not sentences to insert' in PERSONA_STYLE_RULES
    assert 'is a factual claim and still needs approved evidence' in PERSONA_STYLE_RULES
    assert _reviews(calls)[0]['approved_persona_phrases'] == PHRASES
    assert 'never required in a reply' in bobbie.REVIEW_SYSTEM
    log = next(r.getMessage() for r in caplog.records if '[ai-persona] feature=sms.reply' in r.getMessage())
    assert f'persona_version_id={version["id"]}' in log
    assert 'signature_phrases=8' in log and 'avoided_expressions=8' in log


def test_example_facts_are_not_treated_as_verified(client, make_user, auth_header, monkeypatch):
    user, version = _agent(client, make_user, auth_header, 'example-facts@example.com')
    # A claim copied from an illustrative example ("We've got buyers looking in ...").
    copied = "We've got buyers looking in your area right now. Would you consider selling?"
    clean = 'I see. Were you getting offers below your asking price, or none at all?'
    sent, calls, _state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [copied],
        'sms.reply.policy_repair': [clean],
        'sms.review': [_review()],
    }, _store(user, version['id']), history=_thread(NOT_SOLD))
    assert sent == [(clean, 'ai.reply')]
    assert 'an example is a style sample, not verified fact' in _reply_calls(calls)[0]['messages'][0]['content']
    # A biographical Persona phrase is still a claim the reviewer may block.
    blocked = bobbie._review_verdict({
        'valid': False, 'advisory_issues': [], 'issues': [],
        'blocking_issues': ['unsupported_claim: "I\'ve owned and run businesses myself" has no evidence']})
    assert blocked['valid'] is False


def test_reviewer_accepts_a_concise_safe_reply_without_regeneration(client, make_user, auth_header, monkeypatch):
    user, version = _agent(client, make_user, auth_header, 'concise@example.com')
    sent, calls, state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [PREFERRED],
        'sms.review': [_review(blocking=['verbosity: could be even shorter', 'filler: opening "I see"',
                                         'empathy: could acknowledge the frustration'],
                               advisory=['missing sign-off'])],
    }, _store(user, version['id']), history=_thread(NOT_SOLD))
    assert sent == [(PREFERRED, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert len(_reviews(calls)) == 1 and len(_reply_calls(calls)) == 1
    review_rules = bobbie.REVIEW_SYSTEM
    assert 'no separate acknowledgment sentence is needed' in review_rules
    assert 'a safe reply that could be shorter is still valid' in review_rules


def test_multi_part_question_is_answered_in_full(client, make_user, auth_header, monkeypatch):
    user, version = _agent(client, make_user, auth_header, 'multi@example.com')
    question = 'Do I have to pay anything upfront? And how long does it usually take to sell a house like mine?'
    answer = ("Any costs depend on the listing agreement, and I'd go over them with you before you commit to "
              "anything. How long it takes depends on price, "
              "condition and the market, so I'd want to see the house and recent sales before giving you a "
              'timeline. What timing are you hoping for?')
    sent, calls, _state = _run(monkeypatch, user.id, {
        'sms.disposition': [_disposition('answer_and_qualify', 'discovery')],
        'sms.reply': [answer],
        'sms.review': [_review()],
    }, _store(user, version['id']), history=_thread(question))
    # Sent whole: no truncation to a character target.
    assert sent == [(answer, 'ai.reply')] and len(answer) > 160
    assert 'If the owner asks several things, answer every part, even if that takes longer' in \
        _reply_calls(calls)[0]['messages'][0]['content']
    assert 'answering every part when the owner asked several things' in bobbie.REVIEW_SYSTEM


def test_questions_still_get_knowledge_and_statements_do_not():
    assert knowledge.needs_agent_knowledge('Do you have buyers?')
    assert knowledge.needs_agent_knowledge('What does your brokerage charge?')
    assert knowledge.needs_agent_knowledge('Tell me about your fees')
    assert not knowledge.needs_agent_knowledge(NOT_SOLD)
    assert not knowledge.agent_knowledge_allowed(NOT_SOLD)
