"""Seller pricing -> onsite property visit, through the real reply pipeline.

Every test drives ``service.process_ai_reply`` (disposition, generation,
policy check, review, delivery) with scripted model replies, or the real
Anthropic adapter over fake HTTP. No provider, vector database or SMS gateway
is reached, and no paid call is made.
"""

import json
import re

import pytest
import requests

from app import db
from app.ai import client as ai_client
from app.ai.client import AiUnavailableError
from app.core.config import settings
from app.leads import events as lead_events
from app.models import Conversation, Lead, LeadEvent, Notification
from app.sms import bobbie, calendar_service, knowledge, policy, service

from test_personal_ai_runtime_e2e import (  # noqa: F401  (fixtures are used by name)
    _conversation,
    offline,
    scripted,
    three_agents,
)
from test_sms_ai_quality_reviewer import (
    GREETING_EXAMPLE,
    PRICING_EXAMPLE,
    VALID,
    FakeKnowledgeStore,
    _chunk,
    _disposition,
    _first_reply_request,
    _publish_with_examples,
    _review,
    _reviews,
)

# Captured before the autouse `offline` fixture replaces it.
REAL_COMPLETION = ai_client.completion

SELLER_675K = 'I just want to sell it quickly at best price like 675000$'
OPENING = [
    ('outbound', 'Hi, this is Dana about 1 Test St. I saw it came off the market recently.'),
    ('inbound', 'Yes it expired last month.'),
    ('outbound', "Thanks for letting me know. What are you hoping to do with the property now that it's off "
                 'market: sell it, relist it, or something else?'),
]
VISIT_REPLY = ("Understood! You're looking to sell quickly and aiming for around $675,000. I'd suggest a quick visit "
               "to look at the property's condition and review comparable sales before recommending a listing "
               'price. When would be a convenient time?')


class KnowledgeSpy:
    """Counts every agent-knowledge search, preload or model-initiated."""

    def __init__(self, monkeypatch, store=None):
        self.calls = []
        real = knowledge.search_agent_knowledge
        self.store = store or FakeKnowledgeStore([])
        monkeypatch.setattr(knowledge, 'vector_store', self.store)

        def spy(query, context, limit=4, **options):
            self.calls.append(query)
            return real(query, context, limit, **options)
        monkeypatch.setattr(knowledge, 'search_agent_knowledge', spy)


def _run(monkeypatch, user_id, latest, script, history=OPENING, with_lead=False):
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or service.Message(id=1))
    calls = scripted(monkeypatch, script)
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, history=[*history, ('inbound', latest)])
        conversation_id = conversation.id
        if with_lead:
            session.add(Lead(user_id=user_id, owner_name='Owner', phone=conversation.contact,
                             property_address='1 Test St', conversation_id=conversation_id))
            session.commit()
    service.process_ai_reply(conversation_id, latest)
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        state = {'id': conversation_id, 'handled_by': conversation.handled_by,
                 'ai_enabled': conversation.ai_enabled, 'lead_status': conversation.lead_status}
    return sent, calls, state


def _control(request):
    return ' '.join(m['content'] for m in request['messages'] if m['role'] == 'system')


def _tool_names(request):
    return [tool['function']['name'] for tool in request['options'].get('tools') or []]


def _runtime(request):
    text = request['messages'][1]['content'].split('Approved runtime context (facts only): ', 1)[1]
    return json.loads(text)


def _timeline_reasons(conversation_id):
    with db.SessionLocal() as session:
        lead = session.query(Lead).filter(Lead.conversation_id == conversation_id).first()
        return [event.reason for event in session.query(LeadEvent).filter(LeadEvent.lead_id == lead.id)
                if event.event_type == 'handover_to_agent']


# ---------------------------------------------------------------------------
# 1. $675,000 seller goal -> acknowledges price and requests a property visit
# ---------------------------------------------------------------------------

def test_1_675k_goal_gets_a_visit_request(three_agents, monkeypatch):
    spy = KnowledgeSpy(monkeypatch)
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call', lead_status='ready_to_sell')],
        'sms.reply': [VISIT_REPLY],
        'sms.review': [VALID]})
    assert sent == [(VISIT_REPLY, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled'] and state['lead_status'] == 'ready_to_sell'
    request = _first_reply_request(calls)
    control = _control(request)
    # Visit-first, ask when convenient, never a specific time from the AI.
    assert 'suggest a short onsite visit' in control and 'ask when would be convenient' in control
    assert 'Never propose, offer or confirm a specific day or time yourself' in control
    assert 'Meeting offer permitted in this reply: yes' in control
    # The price is the owner's target, not a valuation.
    owner = _runtime(request)['owner_stated']
    assert owner['stated_price'] == '$675,000' and owner['urgency'] == 'high'
    assert 'not a verified valuation' in owner['trust']
    prompt = request['messages'][0]['content']
    assert 'my recommended listing price, which comes only after I see the property' in prompt
    assert 'never claim a visit guarantees a price' in prompt
    assert spy.calls == []


def test_1b_675k_with_disposition_model_down_still_offers_a_visit(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [AiUnavailableError('down')],
        'sms.reply': [VISIT_REPLY],
        'sms.review': [VALID]})
    assert sent == [(VISIT_REPLY, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert 'Required next step: offer_call' in _control(_first_reply_request(calls))
    fallback = policy.safe_grounded_fallback(SELLER_675K, [], [], {'next_step': 'offer_call'}, 'Dana Rivera')
    assert '$675,000' in fallback and 'quick visit' in fallback and 'When would be a convenient time?' in fallback
    assert policy.find_reply_policy_violations(fallback, history=[{'direction': 'inbound', 'text': SELLER_675K}],
                                               next_step='offer_call') == []


# ---------------------------------------------------------------------------
# 2. $800,000 goal -> no price guarantee
# ---------------------------------------------------------------------------

def test_2_800k_goal_never_gets_a_guarantee(three_agents, monkeypatch):
    spy = KnowledgeSpy(monkeypatch)
    honest = ("I can't promise a specific price, since buyers and the market decide that. I'd like to see the "
              'property and review comparable sales first, then recommend a realistic listing strategy. '
              'When would be a good time for a quick visit?')
    sent, calls, _state = _run(monkeypatch, three_agents['third'][0].id, 'Can you get me $800,000?', {
        'sms.disposition': [_disposition('answer_and_qualify')],
        'sms.reply': ["Absolutely, I guarantee you'll get $800,000."],
        'sms.reply.policy_repair': [honest],
        'sms.review': [VALID]})
    assert sent == [(honest, 'ai.reply')]
    repair = next(call for call in calls if call['feature'] == 'sms.reply.policy_repair')
    assert 'unsupported_guarantee' in repair['messages'][-1]['content']
    assert _reviews(calls)[0]['disposition']['meeting_offer_permitted'] is True
    assert spy.calls == []


# ---------------------------------------------------------------------------
# 3. Urgent seller -> visit offered
# ---------------------------------------------------------------------------

def test_3_urgent_seller_is_offered_a_prompt_visit(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    reply = ('Understood, you want to move quickly. I can come take a look at the property soon so we can talk '
             'through a listing plan. When would be a convenient time?')
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, 'I need to sell quickly.', {
        'sms.disposition': [_disposition('answer_and_qualify')],  # cautious model output
        'sms.reply': [reply],
        'sms.review': [VALID]})
    assert sent == [(reply, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert 'Meeting offer permitted in this reply: yes' in _control(_first_reply_request(calls))
    assert not any(call['feature'] == 'sms.reply.policy_repair' for call in calls)


# ---------------------------------------------------------------------------
# 4. Valuation question -> no invented estimate
# ---------------------------------------------------------------------------

def test_4_valuation_question_gets_no_invented_estimate(three_agents, monkeypatch):
    spy = KnowledgeSpy(monkeypatch)
    invented = 'Homes like yours sell for about $650,000 right now.'
    visit = ("Good question. A reliable number depends on the home's condition and recent comparable sales, so "
             "I'd want to see it first. When would be a convenient time for a quick visit?")
    sent, calls, _state = _run(monkeypatch, three_agents['third'][0].id, 'How much can I sell my house for?', {
        'sms.disposition': [_disposition('answer_and_qualify')],
        'sms.reply': [invented, visit],
        'sms.review': [_review(blocking=['fabricated_valuation: states a $650,000 value']), VALID]})
    assert sent == [(visit, 'ai.reply')]
    assert '$' not in sent[0][0]
    assert 'Never give an estimate yourself' in _first_reply_request(calls)['messages'][0]['content']
    assert _reviews(calls)[0]['disposition']['meeting_offer_permitted'] is True
    assert spy.calls == []


# ---------------------------------------------------------------------------
# 5. Visit refused -> phone alternative
# ---------------------------------------------------------------------------

def test_5_visit_refusal_gets_a_phone_alternative(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    reply = ('No problem at all. We could do a quick phone call instead to go over your goals and the local '
             'market. Would that work for you?')
    # Worst case: the model labels a visit-only refusal as call_declined.
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, "I don't want anyone coming to the house.", {
        'sms.disposition': [_disposition('answer_and_qualify', calendar='call_declined')],
        'sms.reply': [reply],
        'sms.review': [VALID]})
    assert sent == [(reply, 'ai.reply')] and state['handled_by'] == 'bobbie'
    control = _control(_first_reply_request(calls))
    assert 'The owner declined an in-person visit' in control and 'offer a quick phone call' in control
    assert 'Calendar state: none' in control and 'Required next step: offer_call' in control
    assert _reviews(calls)[0]['disposition']['meeting_offer_permitted'] is True
    # A refusal of calls too stays a full refusal.
    assert not policy.visit_only_refusal("Don't call me and no visits")
    fallback = policy.safe_grounded_fallback("I don't want anyone coming to the house.", [], [],
                                             {'next_step': 'offer_call'})
    assert 'no visit needed' in fallback and 'phone call' in fallback


# ---------------------------------------------------------------------------
# 6. Owner gives availability -> acknowledge, hand to the human agent, book nothing
# ---------------------------------------------------------------------------

def _time_disposition(state='time_proposed', requested='', next_step='schedule'):
    disposition = json.loads(_disposition(next_step, 'scheduling', calendar=state))
    disposition['calendar'].update(should_fetch_availability=True, requested_time_text=requested)
    return json.dumps(disposition)


def _handoff_events(conversation_id):
    with db.SessionLocal() as session:
        lead = session.query(Lead).filter(Lead.conversation_id == conversation_id).first()
        return [lead_events.serialize(event)
                for event in session.query(LeadEvent).filter(LeadEvent.lead_id == lead.id)
                if event.event_type == 'handover_to_agent']


def _max_notification_id():
    with db.SessionLocal() as session:
        return max([item.id for item in session.query(Notification.id)] or [0])


def _notifications(user_id, after_id=0):
    """Notifications for one user created after ``after_id`` (the table outlives each test)."""
    with db.SessionLocal() as session:
        return [(item.type, item.title, item.message, item.action_url)
                for item in session.query(Notification).filter(Notification.user_id == user_id,
                                                                Notification.id > after_id)]


def test_6_owner_availability_is_acknowledged_and_handed_to_the_agent(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    user = three_agents['third'][0]
    booking_calls = []
    monkeypatch.setattr(calendar_service, 'create_booking', lambda *a, **k: booking_calls.append(a))
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    before = _max_notification_id()
    sent, calls, state = _run(monkeypatch, user.id, 'Tomorrow afternoon works.', {
        'sms.disposition': [_time_disposition(requested='Tomorrow afternoon works.')]},
        history=history, with_lead=True)
    # Natural acknowledgment, no booking claim, no generated text.
    assert [call['feature'] for call in calls] == ['sms.disposition']
    assert sent == [("Thanks, I've noted tomorrow afternoon as your preference. Our team will reach out to "
                     'coordinate the visit.', 'ai.reply')]
    assert not service.contains_unbooked_confirmation(sent[0][0])
    assert not re.search(r'\b(?:works|confirmed|booked|scheduled|see you)\b', sent[0][0], re.I)
    # 11. Ownership moves to the human agent.
    assert state['handled_by'] == 'broker' and not state['ai_enabled']
    assert state['lead_status'] == 'location_discussion'
    # 12. Nothing booked.
    assert booking_calls == []
    with db.SessionLocal() as session:
        assert calendar_service.list_bookings(session, user.id) == []
        assert session.get(Conversation, state['id']).meeting_booked in (False, 0, None)
    # 13. The agent sees availability and the owner-stated price.
    event, = _handoff_events(state['id'])
    assert event['reason'] == (
        'Seller interested in selling quickly. Target price: $675,000 (owner-stated, unverified). '
        'Requested onsite assessment. Preferred availability: tomorrow afternoon. Property: 1 Test St. '
        'Last customer message: "Tomorrow afternoon works.". Nothing is booked. '
        'Agent to coordinate and confirm the appointment.')
    assert event['meta'] == {
        'handoff_reason': 'seller_availability_collected', 'conversation_id': state['id'],
        'property_address': '1 Test St', 'selling_intent': True, 'urgency': 'high',
        'owner_stated_target_price': '$675,000', 'meeting_type': 'onsite_visit',
        'preferred_availability': 'tomorrow afternoon', 'last_customer_message': 'Tomorrow afternoon works.',
        'booked': False, 'ai_agent_name': 'Dana Rivera'}
    notice, = [item for item in _notifications(user.id, before) if item[0] == 'AI_HANDOFF']
    assert notice[1] == 'AI for Dana Rivera handed Owner to you' and 'Bobbie' not in notice[1] + notice[2]
    assert 'tomorrow afternoon' in notice[2] and '$675,000 (owner-stated, unverified)' in notice[2]
    assert f'conversation_id={state["id"]}' in notice[3] and 'view=followups' in notice[3]


def test_6b_availability_found_in_the_message_even_without_model_text(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Saturday at 10am is good for me', {
        'sms.disposition': [_time_disposition('call_accepted')]}, history=history)
    # Clock times are passed to the agent, never echoed back as if confirmed.
    assert sent[0][0] == "Thanks, I've noted your availability. Our team will reach out to coordinate the visit."
    assert not any(char.isdigit() for char in sent[0][0])
    assert state['handled_by'] == 'broker'


def test_3_interest_without_availability_continues_qualifying(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    reply = 'Great to hear. Could you tell me a little about your timing for selling?'
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Yes, interested', {
        'sms.disposition': [_disposition('answer_and_qualify', lead_status='interested')],
        'sms.reply': [reply], 'sms.review': [VALID]})
    assert sent == [(reply, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']


def test_3b_visit_accepted_without_a_time_asks_once_instead_of_handing_off(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    reply = 'Great, glad that works. What day or time generally suits you best?'
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Yes, a visit sounds good', {
        'sms.disposition': [_time_disposition('call_accepted', next_step='answer_only')],
        'sms.reply': [reply], 'sms.review': [VALID]}, history=history)
    assert sent == [(reply, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']
    assert 'has not said when' in _first_reply_request(calls)['messages'][0]['content']
    assert not any(call['feature'] == 'sms.reply.policy_repair' for call in calls)


def test_5b_phone_consultation_availability_is_handed_off_as_a_call(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    phone_offer = ('No problem at all. We could do a quick phone call instead to go over your goals and the local '
                   'market. Would that work for you?')
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY),
               ('inbound', "I don't want anyone coming to the house."), ('outbound', phone_offer)]
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Sure, a call Thursday morning works', {
        'sms.disposition': [_time_disposition('call_accepted', requested='a call Thursday morning')]},
        history=history, with_lead=True)
    assert sent == [("Thanks, I've noted Thursday morning as your preference. Our team will reach out to "
                     'coordinate the call.', 'ai.reply')]
    assert state['handled_by'] == 'broker' and not state['ai_enabled']
    event, = _handoff_events(state['id'])
    assert 'Requested phone consultation' in event['reason'] and event['meta']['meeting_type'] == 'phone_call'


def test_14_messages_after_handoff_never_restart_the_ai(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Tomorrow afternoon works.', {
        'sms.disposition': [_time_disposition(requested='Tomorrow afternoon')]}, history=history)
    assert state['handled_by'] == 'broker' and len(sent) == 1
    for text in ('Hello?', 'Are you there?', SELLER_675K):
        service.process_ai_reply(state['id'], text)
    assert len(calls) == 1 and len(sent) == 1


# ---------------------------------------------------------------------------
# 7. Seller price -> zero search_agent_knowledge calls (preload and model tool)
# ---------------------------------------------------------------------------

def test_7_seller_price_makes_zero_agent_knowledge_searches(three_agents, monkeypatch):
    user, _headers, _persona, version = three_agents['third']
    spy = KnowledgeSpy(monkeypatch, FakeKnowledgeStore([
        _chunk('pricing', 'Owner-confirmed information: pricing starts with a walkthrough.', user, version['id'])]))
    # Even if a model tried the tool anyway, nothing is searched.
    rogue = {'role': 'assistant', 'content': '', 'tool_calls': [{
        'id': 'call_1', 'type': 'function',
        'function': {'name': 'search_agent_knowledge', 'arguments': json.dumps({'query': 'pricing'})}}]}
    sent, calls, _state = _run(monkeypatch, user.id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': [rogue, VISIT_REPLY],
        'sms.review': [VALID]})
    assert sent == [(VISIT_REPLY, 'ai.reply')]
    assert spy.calls == [] and spy.store.calls == []
    reply_calls = [call for call in calls if call['feature'].startswith('sms.reply')]
    assert all(_tool_names(call) == ['get_lead_details'] for call in reply_calls)
    runtime = _runtime(reply_calls[0])
    assert runtime['agent_knowledge']['available'] is False and 'matches' not in runtime['agent_knowledge']
    tool_result = next(m for m in reply_calls[1]['messages'] if m['role'] == 'tool')
    assert 'not used for this message' in tool_result['content']
    assert 'approved_knowledge_evidence' not in _reviews(calls)[0]
    for text in (SELLER_675K, 'Can you get me $800,000?', 'How much can I sell my house for?',
                 'I need to sell quickly.', "What's my house worth?"):
        assert not knowledge.needs_agent_knowledge(text) and not knowledge.agent_knowledge_allowed(text)


# ---------------------------------------------------------------------------
# 8. Agent-experience question -> RAG still available
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('question', [
    'How many years of experience do you have?',
    'How many homes have you sold?',
    "What's your pricing approach for a home like mine?",
])
def test_8_agent_knowledge_questions_still_use_rag(three_agents, monkeypatch, question):
    user, _headers, _persona, version = three_agents['third']
    spy = KnowledgeSpy(monkeypatch, FakeKnowledgeStore([
        _chunk('exp', 'Owner-confirmed information: My pricing approach starts with a market analysis.',
               user, version['id'], score=0.2)]))
    reply = 'My pricing approach starts with a market analysis of the home and nearby sales.'
    sent, calls, _state = _run(monkeypatch, user.id, question, {
        'sms.disposition': [_disposition('answer_only')], 'sms.reply': [reply], 'sms.review': [VALID]})
    assert sent == [(reply, 'ai.reply')]
    assert len(spy.calls) == 1 and spy.store.calls
    request = _first_reply_request(calls)
    assert _tool_names(request) == ['get_lead_details', 'search_agent_knowledge']
    assert [m['id'] for m in _runtime(request)['agent_knowledge']['matches']] == ['exp']


# ---------------------------------------------------------------------------
# 9-11. Reviewer approves a visit; long SMS not rejected; no needless handoff
# ---------------------------------------------------------------------------

def test_9_reviewer_is_told_a_visit_is_the_normal_next_step():
    assert 'is NOT confirming an appointment' in bobbie.REVIEW_SYSTEM
    assert 'the normal professional next step is a short onsite visit' in bobbie.REVIEW_SYSTEM
    assert 'Asking the owner when suits them is not proposing a time' in bobbie.REVIEW_SYSTEM
    assert "repeating the owner's own target price is not negotiating" in bobbie.REVIEW_SYSTEM
    assert 'not using a specific scripted phrase' in bobbie.REVIEW_SYSTEM


def test_9_reviewer_approves_a_reasonable_visit_reply_with_advisories(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': [VISIT_REPLY],
        'sms.review': [_review(advisory=['could offer a phone call as well', 'sign-off differs from Persona'])]})
    assert sent == [(VISIT_REPLY, 'ai.reply')] and state['handled_by'] == 'bobbie'
    review = _reviews(calls)[0]
    assert review['proposed_agent_reply'] == VISIT_REPLY and review['disposition']['meeting_offer_permitted']


@pytest.mark.parametrize('length', [320, 450, 600])
def test_10_long_seller_sms_is_not_rejected_for_length(three_agents, monkeypatch, length):
    KnowledgeSpy(monkeypatch)
    filler = ' I want to make sure the listing plan fits your timeline and goals for the home.'
    reply = (VISIT_REPLY + filler * 10)[:length].rsplit(' ', 1)[0].rstrip('.') + '.'
    assert 300 <= len(reply) <= 600
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': [reply],
        'sms.review': [_review(advisory=['longer than one segment'], valid=False, issues=['too_long'])]})
    assert sent == [(reply, 'ai.reply')] and state['handled_by'] == 'bobbie'
    assert [call['feature'] for call in calls] == ['sms.disposition', 'sms.reply', 'sms.review']


def test_11_old_failure_replay_continues_instead_of_handing_off(three_agents, monkeypatch):
    """The original incident: cautious disposition, a >240-char draft and a picky reviewer."""
    KnowledgeSpy(monkeypatch)
    draft = VISIT_REPLY + ' No pressure at all, it usually takes about twenty minutes and helps me give you a real answer.'
    assert len(draft) > 240
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('answer_and_qualify')],
        'sms.reply': [draft],
        'sms.review': [_review(blocking=['length: over 240 characters', 'tone: prefer a shorter close'],
                               valid=False)]}, with_lead=True)
    assert sent == [(draft, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']
    assert not any(call['feature'] == 'sms.reply.policy_repair' for call in calls)
    assert _timeline_reasons(state['id']) == []


# ---------------------------------------------------------------------------
# 12. Genuine hard-policy violations stay blocked, and the agent is told why
# ---------------------------------------------------------------------------

def test_12_hard_violations_fail_closed_with_a_visible_handoff(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    hard = _review(blocking=['fabricated_valuation: says the home is worth $700k'])
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': ['Your home is worth $700k.', 'It is worth $700k.', 'Worth $700k for sure.'],
        'sms.review': [hard, hard, hard]}, with_lead=True)
    assert sent == []
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS
    assert state['handled_by'] == 'broker' and not state['ai_enabled']
    reasons = _timeline_reasons(state['id'])
    assert reasons and 'AI reply withheld' in reasons[0] and 'fabricated_valuation' in reasons[0]
    # Deterministic blocks are unchanged.
    for text, violation in (('I guarantee you will get $675,000.', 'unsupported_guarantee'),
                            ('I have a cash buyer ready for your home.', 'unsupported_buyer'),
                            ('Call me at 312-555-0100.', 'invented_phone')):
        assert violation in policy.find_reply_policy_violations(text)


def test_12b_reviewer_outage_sends_nothing(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id, SELLER_675K, {
        'sms.disposition': [_disposition('offer_call', 'qualified_for_call')],
        'sms.reply': [VISIT_REPLY],
        'sms.review': [AiUnavailableError('down')]})
    assert sent == [] and state['handled_by'] == 'bobbie'


# ---------------------------------------------------------------------------
# 13-14. Active published Persona reaches the first Claude generation request
# ---------------------------------------------------------------------------

@pytest.fixture
def claude_http(monkeypatch):
    """Real Anthropic adapter, fake HTTP: records each /v1/messages body."""
    state = {'bodies': [], 'replies': []}

    class Reply:
        ok = True
        status_code = 200
        headers = {'request-id': 'req_offline'}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith('/v1/messages')
        state['bodies'].append(json)
        text = state['replies'].pop(0)
        return Reply({'type': 'message', 'role': 'assistant', 'model': 'claude-haiku-5-5',
                      'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                      'content': [{'type': 'text', 'text': text}]})

    monkeypatch.setattr(requests, 'post', fake_post)
    monkeypatch.setattr(bobbie, 'completion', REAL_COMPLETION)
    monkeypatch.setattr(ai_client, 'completion', REAL_COMPLETION)
    monkeypatch.setitem(settings['ai'], 'provider', 'anthropic')
    monkeypatch.setitem(settings['ai'], 'model', 'claude-haiku-5-5')
    monkeypatch.setitem(settings['ai'], 'anthropic_api_key', 'offline-anthropic-key')
    monkeypatch.setitem(settings['ai'], 'anthropic_base_url', 'https://api.anthropic.com')
    return state


def _claude_run(monkeypatch, claude_http, user_id, contact):
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append(text) or service.Message(id=1))
    claude_http['replies'] = [_disposition('offer_call', 'qualified_for_call'), VISIT_REPLY, VALID]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, contact=contact,
                                     history=[*OPENING, ('inbound', SELLER_675K)])
    service.process_ai_reply(conversation.id, SELLER_675K)
    generation = [body for body in claude_http['bodies'] if 'GLOBAL SYSTEM POLICIES' in json.dumps(body['system'])]
    return sent, generation[0], json.dumps(generation[0]['system'], ensure_ascii=False)


def test_13_first_claude_generation_request_carries_the_published_persona(
        client, make_user, auth_header, monkeypatch, claude_http):
    spy = KnowledgeSpy(monkeypatch)
    user, _headers, _persona, version = _publish_with_examples(
        client, make_user, auth_header, 'claude-seller@example.com', 'b-claude', 'Dana Rivera',
        [GREETING_EXAMPLE, PRICING_EXAMPLE], brokerage_name='Rivera Homes')
    sent, body, system = _claude_run(monkeypatch, claude_http, user.id, '+13125550170')
    assert sent == [VISIT_REPLY]
    # Trusted identity, active version, language and approved examples, ranked by relevance.
    assert 'Speak in first person as Dana Rivera' in system and 'Rivera Homes' in system
    assert f'\\"active_published_version_id\\":{version["id"]}' in system
    assert '\\"preferred_language\\":\\"en\\"' in system
    assert PRICING_EXAMPLE['preferred_response'] in system and GREETING_EXAMPLE['preferred_response'] in system
    assert system.index(PRICING_EXAMPLE['preferred_response']) < system.index(GREETING_EXAMPLE['preferred_response'])
    # Conversation history is sent, ending on the owner's message.
    history_text = json.dumps(body['messages'], ensure_ascii=False)
    assert 'sell it, relist it, or something else?' in history_text and SELLER_675K in history_text
    # No agent-knowledge tool and no search for the examples.
    assert [tool['name'] for tool in body['tools']] == ['get_lead_details']
    assert spy.calls == []


def test_14_each_agent_gets_only_their_own_examples_and_identity(
        client, make_user, auth_header, monkeypatch, claude_http):
    KnowledgeSpy(monkeypatch)
    avery = _publish_with_examples(client, make_user, auth_header, 'avery@example.com', 'b-two', 'Avery Stone', [
        {**PRICING_EXAMPLE, 'preferred_response': 'AVERY STYLE: happy to swing by and take a look.'}],
        brokerage_name='Stone Realty')
    blake = _publish_with_examples(client, make_user, auth_header, 'blake@example.com', 'b-two', 'Blake Moss', [
        {**PRICING_EXAMPLE, 'preferred_response': 'BLAKE STYLE: let us set up a walkthrough.'}],
        brokerage_name='Stone Realty')
    for (user, *_rest), own, other, name, contact in (
            (avery, 'AVERY STYLE', 'BLAKE STYLE', 'Avery Stone', '+13125550181'),
            (blake, 'BLAKE STYLE', 'AVERY STYLE', 'Blake Moss', '+13125550182')):
        claude_http['bodies'].clear()
        _sent, _body, system = _claude_run(monkeypatch, claude_http, user.id, contact)
        assert own in system and other not in system
        assert f'Speak in first person as {name}' in system


# ---------------------------------------------------------------------------
# 15. STOP / DNC / opt-out safeguards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('message', ['STOP', 'Please do not contact me again', 'unsubscribe'])
def test_15_opt_out_is_honored_before_any_model_call(three_agents, monkeypatch, message):
    spy = KnowledgeSpy(monkeypatch)
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, message, {})
    assert calls == [] and spy.calls == []
    assert sent == [('Understood — I’ll remove you from my outreach list. Take care.', 'ai.reply')]
    assert state['handled_by'] == 'broker' and not state['ai_enabled'] and state['lead_status'] == 'dnc'
    # A later message does not wake the AI.
    service.process_ai_reply(state['id'], SELLER_675K)
    assert calls == [] and len(sent) == 1
    # The reviewer still hard-blocks consent violations.
    assert bobbie._review_verdict({'valid': True, 'blocking_issues': ['consent_violation: kept messaging'],
                                   'advisory_issues': [], 'issues': []})['valid'] is False


# ---------------------------------------------------------------------------
# Review follow-ups: backstop, notification routing, tool availability
# ---------------------------------------------------------------------------

def test_backstop_hands_off_when_the_disposition_misses_an_availability_reply(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    booking_calls = []
    monkeypatch.setattr(calendar_service, 'create_booking', lambda *a, **k: booking_calls.append(a))
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    # Model returns a plain continue with no calendar signal at all.
    sent, calls, state = _run(monkeypatch, three_agents['third'][0].id, 'Tomorrow afternoon works', {
        'sms.disposition': [_disposition('answer_only')]}, history=history, with_lead=True)
    assert [call['feature'] for call in calls] == ['sms.disposition']
    assert sent == [("Thanks, I've noted tomorrow afternoon as your preference. Our team will reach out to "
                     'coordinate the visit.', 'ai.reply')]
    assert state['handled_by'] == 'broker' and not state['ai_enabled'] and booking_calls == []
    event, = _handoff_events(state['id'])
    assert event['meta']['preferred_availability'] == 'tomorrow afternoon'


@pytest.mark.parametrize('previous, latest', [
    # No meeting invitation from the agent: a date is just information.
    ("Thanks for letting me know. What are you hoping to do with the property?", 'Sell it next month'),
    ('What timeline are you working with?', 'Probably by next week'),
    # Invitation, but the owner declines or defers.
    (VISIT_REPLY, "Not tomorrow, I'm busy"),
    (VISIT_REPLY, 'Maybe next week, not sure yet'),
])
def test_backstop_ignores_unrelated_or_declining_date_mentions(three_agents, monkeypatch, previous, latest):
    KnowledgeSpy(monkeypatch)
    reply = 'Understood, thanks for letting me know.'
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', previous)]
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id, latest, {
        'sms.disposition': [_disposition('answer_only')], 'sms.reply': [reply], 'sms.review': [VALID]},
        history=history)
    assert sent == [(reply, 'ai.reply')]
    assert state['handled_by'] == 'bobbie' and state['ai_enabled']


def test_backstop_never_overrides_a_declined_meeting(three_agents, monkeypatch):
    KnowledgeSpy(monkeypatch)
    reply = 'Understood, I will keep this to text.'
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    # The owner's refusal keeps call_declined; the date mention must not reopen scheduling.
    sent, _calls, state = _run(monkeypatch, three_agents['third'][0].id,
                               "Tomorrow afternoon works for a text, but no visit please", {
        'sms.disposition': [_disposition('answer_only', calendar='call_declined')],
        'sms.reply': [reply], 'sms.review': [VALID]}, history=history)
    assert sent == [(reply, 'ai.reply')] and state['handled_by'] == 'bobbie'


def _assign(conversation_id, agent_id):
    with db.SessionLocal() as session:
        lead = session.query(Lead).filter(Lead.conversation_id == conversation_id).first()
        lead.assigned_agent_id = agent_id
        session.commit()


def test_handoff_notifies_the_assigned_agent_even_after_a_broker_touched_the_thread(
        client, make_user, auth_header, monkeypatch):
    KnowledgeSpy(monkeypatch)
    owner, *_rest = _publish_with_examples(client, make_user, auth_header, 'route-broker@example.com', 'b-route',
                                           'Dana Rivera', [PRICING_EXAMPLE], brokerage_name='Rivera Homes')
    with db.SessionLocal() as session:
        from app.models import User
        session.get(User, owner.id).role = 'broker'
        session.commit()
    agent = make_user('route-agent@example.com', role='agent', brokerage_id='b-route', brokerage_name='Rivera Homes')
    outsider = make_user('route-outsider@example.com', role='agent', brokerage_id='b-other',
                         brokerage_name='Other Realty')
    history = [*OPENING, ('inbound', SELLER_675K), ('outbound', VISIT_REPLY)]
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append(text) or service.Message(id=1))
    scripted(monkeypatch, {'sms.disposition': [_time_disposition(requested='Tomorrow afternoon')]})
    with db.SessionLocal() as session:
        conversation = _conversation(session, owner.id, contact='+13125550191',
                                     history=[*history, ('inbound', 'Tomorrow afternoon works.')])
        conversation_id = conversation.id
        session.add(Lead(user_id=owner.id, owner_name='Owner', phone=conversation.contact,
                         property_address='1 Test St', conversation_id=conversation_id))
        # The broker texted the owner once by hand earlier: the reply rule alone picks the broker.
        session.add(service.Message(conversation_id=conversation_id, direction='outbound', text='Hi from the office',
                                    event_type='broker.reply', status='delivered', sender_user_id=owner.id))
        session.commit()
    _assign(conversation_id, agent.id)
    from app.routes.webhooks import _reply_recipient_ids
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        assert _reply_recipient_ids(session, conversation) == [owner.id]
        assert service.handoff_recipient_ids(session, conversation) == [agent.id, owner.id]
        # An assignee outside the brokerage is never notified.
        _assign(conversation_id, outsider.id)
        session.expire_all()
        assert service.handoff_recipient_ids(session, conversation) == [owner.id]
    _assign(conversation_id, agent.id)
    before = _max_notification_id()
    service.process_ai_reply(conversation_id, 'Tomorrow afternoon works.')
    assert len(sent) == 1
    for user_id in (agent.id, owner.id):
        assert [item[0] for item in _notifications(user_id, before)] == ['AI_HANDOFF']
    assert _notifications(outsider.id, before) == []


@pytest.mark.parametrize('message, offered', [
    ('Hi', False), ('ok thanks', False), ('Is the roof included?', False), (SELLER_675K, False),
    ('How many years of experience do you have?', True),
    ("What's your commission if I sell for $675,000?", True),
    ('Who is Bobbie, and how many bedrooms?', True),
])
def test_knowledge_tool_is_offered_only_when_relevant(three_agents, monkeypatch, message, offered):
    spy = KnowledgeSpy(monkeypatch)
    reply = 'Thanks for asking. Happy to help with that.'
    _sent, calls, _state = _run(monkeypatch, three_agents['third'][0].id, message, {
        'sms.disposition': [_disposition('answer_only')], 'sms.reply': [reply], 'sms.review': [VALID]})
    names = _tool_names(_first_reply_request(calls))
    assert names == (['get_lead_details', 'search_agent_knowledge'] if offered else ['get_lead_details'])
    assert bool(spy.calls) is offered
