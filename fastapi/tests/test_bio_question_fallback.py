"""Manual test failure: a Bobbie-tenure question got the property-timing fallback.

"Who is Bobbie, and how long has she been with RE/MAX?" contains "how long",
which safe_grounded_fallback() read as a sale-timeline question, so a failed
generation/repair sent "Timing depends on price, condition, and current demand…".
Provider HTTP is faked; these tests pin the pipeline, not live model wording.
"""

from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.models import Conversation, Message
from app.sms import service
from test_ai_review_evidence import (  # noqa: F401  (offline is a fixture)
    LIVE_KNOWLEDGE, PROVIDERS, REJECTED, VALID, _capture_sent, _disposition, _review_payload, offline, use,
)

QUESTION = 'Who is Bobbie, and how long has she been with RE/MAX?'
TIMING_LINE = ('Timing depends on price, condition, and current demand, so I don’t have '
               'verified data to promise a timeframe.')
BIO_FALLBACK = ('I’m with Real Broker LLC now, and my RE/MAX Premier work was earlier. '
                'I don’t have a verified exact tenure to share, so I won’t guess.')
# The manual test's history: outreach, then a price question Bobbie already answered.
HISTORY = [
    ('outbound', 'outreach.initial', 'Hey Test, I’m reaching out about 1 Test St. The listing is no longer active. '
                                     'Would you be open to a brief conversation? Bobbie Fisher'),
    ('inbound', 'message.received', 'Maybe. What would it sell for?'),
    ('outbound', 'ai.reply', "I can't give an accurate number without seeing the property in person. "
                             'What price were you hoping to get for it?'),
]
OVERLONG = ("I'm Bobbie Fisher, a Chicago-area real estate professional, and I'm with Real Broker LLC now. "
            'Before that I worked with RE/MAX Premier on brokerage growth, agent recruiting and leadership '
            'development across the North Shore. Are you thinking about selling 1 Test St soon?')
STILL_OVERLONG = OVERLONG.replace('Are you thinking', 'So, are you still thinking')
BIO_DISPOSITION = _disposition(intent='asks_about_agent_background', lead_status='want_more_info')


def _conversation(session, make_user, question):
    broker = make_user('bio-fallback-broker@linchpinglobal.net', role='broker')
    conversation = Conversation(contact='+13125550199', user_id=broker.id, name='Test', property_address='1 Test St',
                                ai_enabled=True, handled_by='bobbie', lead_status='want_more_info')
    session.add(conversation)
    session.commit()
    start = datetime.utcnow() - timedelta(minutes=10)
    for minute, (direction, event, text) in enumerate([*HISTORY, ('inbound', 'message.received', question)]):
        session.add(Message(conversation_id=conversation.id, direction=direction, event_type=event, text=text,
                            status='received' if direction == 'inbound' else 'delivered',
                            created_at=start + timedelta(minutes=minute)))
    session.commit()
    return conversation


def _run(offline, monkeypatch, make_user, session, replies, question=QUESTION):
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', 'Real Broker LLC')
    offline['knowledge'] = LIVE_KNOWLEDGE
    conversation = _conversation(session, make_user, question)
    offline['replies'] = replies
    sent = _capture_sent(monkeypatch)
    service.process_ai_reply(conversation.id, question)
    return sent


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_failed_generation_and_repair_send_the_bio_fallback_not_the_timing_line(
        offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, OVERLONG, STILL_OVERLONG, VALID])

    assert sent == [(BIO_FALLBACK, 'ai.reply')]
    assert len(offline['bodies']) == 4
    assert _review_payload(offline['bodies'][3])['proposed_bobbie_reply'] == BIO_FALLBACK


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_reviewer_rejection_then_failed_regeneration_still_sends_the_bio_fallback(
        offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session,
                [BIO_DISPOSITION, OVERLONG, STILL_OVERLONG, REJECTED, OVERLONG, STILL_OVERLONG])

    assert sent == [(BIO_FALLBACK, 'ai.reply')]
    assert len(offline['bodies']) == 6 and len(offline['searches']) == 2


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_empty_draft_and_empty_retry_send_the_bio_fallback(offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, '', '', VALID])

    assert sent == [(BIO_FALLBACK, 'ai.reply')]


@pytest.mark.parametrize('question', ['How long would it take to sell my house?', 'What is the timeline to list?'])
def test_property_timeline_questions_keep_the_timing_fallback(offline, monkeypatch, make_user, session, question):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')

    sent = _run(offline, monkeypatch, make_user, session,
                [_disposition(intent='asks_timeline'), OVERLONG, STILL_OVERLONG, VALID], question=question)

    assert sent == [(TIMING_LINE, 'ai.reply')]


def test_bio_fallback_passes_sms_policy_and_calendar_guard():
    from app.sms.policy import find_reply_policy_violations
    from app.sms.scheduling import contains_time_proposal, contains_unbooked_confirmation
    history = [{'direction': 'inbound', 'text': QUESTION}]
    assert find_reply_policy_violations(BIO_FALLBACK, phone_source_known=False, max_length=240, history=history) == []
    assert not contains_unbooked_confirmation(BIO_FALLBACK) and not contains_time_proposal(BIO_FALLBACK)


@pytest.mark.parametrize('question,expected', [
    ('Who is Bobbie, and how long has she been with RE/MAX?', BIO_FALLBACK),
    ('How long have you been an agent?', 'I’m with Real Broker LLC now. I don’t have a verified exact tenure '
                                         'to share, so I won’t guess.'),
    ('What brokerage are you with?', 'I’m with Real Broker LLC now.'),
    ('How long would it take to sell my house?', TIMING_LINE),
])
def test_fallback_intent_comes_from_the_latest_question(monkeypatch, question, expected):
    from app.sms.policy import safe_grounded_fallback
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', 'Real Broker LLC')
    history = [{'direction': d, 'text': t} for d, _e, t in HISTORY]
    assert safe_grounded_fallback(question, ['over_length'], history, {'next_step': 'answer_and_qualify'}) == expected


def test_unapproved_brokerage_fallback_does_not_name_a_current_firm(monkeypatch):
    from app.sms.policy import safe_grounded_fallback
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', '')
    reply = safe_grounded_fallback(QUESTION, [], [], {})
    assert 'Real Broker' not in reply and 'I don’t have a verified exact tenure' in reply
