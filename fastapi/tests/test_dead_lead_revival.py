"""A reply on a dead (no response) lead brings it back and Bobbie answers that reply."""

from datetime import datetime

import pytest

from app import db
from app.models import Conversation
from tests.test_followups import BROKER_EMAIL, owner_replies, sent_sms, start_conversation  # noqa: F401


@pytest.fixture
def ai_calls(monkeypatch):
    calls = []
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: calls.append((conversation_id, text)))
    return calls


def _set(conversation_id, **values):
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        for key, value in values.items():
            setattr(conversation, key, value)
        session.commit()


def _state(conversation_id):
    with db.SessionLocal() as session:
        c = session.get(Conversation, conversation_id)
        return {'ai_enabled': c.ai_enabled, 'lead_status': c.lead_status, 'queue_status': c.queue_status,
                'dead_at': c.dead_at, 'handled_by': c.handled_by}


DEAD = {'dead_at': datetime(2026, 9, 1), 'lead_status': 'no_response', 'queue_status': 'dead',
        'ai_enabled': False, 'handled_by': 'bobbie', 'next_followup_at': None}


def _dead_thread(client, make_user, auth_header, **overrides):
    make_user(BROKER_EMAIL, role='broker')
    conversation_id = start_conversation(client, auth_header(BROKER_EMAIL))
    _set(conversation_id, **{**DEAD, **overrides})
    return conversation_id


def test_reply_on_dead_lead_turns_ai_back_on_and_bobbie_answers_it(client, make_user, auth_header,
                                                                   sent_sms, ai_calls):
    conversation_id = _dead_thread(client, make_user, auth_header)
    owner_replies(client, 'Sorry for the late reply, I might be open to selling')
    # The same reply that revived the lead is handed to Bobbie.
    assert ai_calls == [(conversation_id, 'Sorry for the late reply, I might be open to selling')]
    state = _state(conversation_id)
    assert state['ai_enabled'] is True
    assert state['handled_by'] == 'bobbie'
    assert state['dead_at'] is None
    assert state['queue_status'] == 'idle'
    assert state['lead_status'] == 'interested'  # classified as usual


def test_neutral_reply_on_dead_lead_goes_back_in_progress(client, make_user, auth_header, sent_sms, ai_calls):
    conversation_id = _dead_thread(client, make_user, auth_header)
    owner_replies(client, 'Who is this?')
    assert len(ai_calls) == 1
    assert _state(conversation_id)['lead_status'] == 'processing'


@pytest.mark.parametrize('overrides', [
    {'lead_status': 'dnc', 'dnc_alert': True},
    {'lead_status': 'realtor', 'dnc_alert': True},
    {'lead_status': 'wrong_info'},
    {'lead_status': 'not_interested'},
    {'handled_by': 'broker'},
])
def test_closed_or_broker_threads_stay_off(client, make_user, auth_header, sent_sms, ai_calls, overrides):
    conversation_id = _dead_thread(client, make_user, auth_header, **overrides)
    owner_replies(client, 'Hello?')
    assert ai_calls == []
    assert _state(conversation_id)['ai_enabled'] is False


def test_ai_switched_off_by_hand_on_a_live_thread_is_not_turned_on(client, make_user, auth_header,
                                                                   sent_sms, ai_calls):
    make_user(BROKER_EMAIL, role='broker')
    conversation_id = start_conversation(client, auth_header(BROKER_EMAIL))
    _set(conversation_id, ai_enabled=False, lead_status='processing', queue_status='idle', dead_at=None)
    owner_replies(client, 'Any update?')
    assert ai_calls == []
    assert _state(conversation_id)['ai_enabled'] is False
