"""A redelivered Telnyx message.received event is handled exactly once."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Conversation, InboundSmsEvent, Message, Notification
from app.routes import webhooks
from app.sms import service

from tests.test_sms import BROKER_EMAIL, CONTACT, SMS_URL, new_conversation_payload, sent_sms  # noqa: F401

WEBHOOK_URL = '/api/v1/webhooks/telnyx'


def inbound_event(provider_id, text='Call me about the house.'):
    return {'data': {'event_type': 'message.received', 'payload': {
        'id': provider_id,
        'from': {'phone_number': CONTACT},
        'to': [{'phone_number': service.FIXED_FROM}],
        'text': text,
    }}}


@pytest.fixture(autouse=True)
def no_leftover_notifications(session):
    # The shared cleanup does not clear notifications, and conversation IDs are
    # reused between tests, so old rows would be counted here.
    session.query(Notification).delete()
    session.commit()


@pytest.fixture
def effects(monkeypatch):
    """Count the downstream actions: AI replies (which send the outbound SMS) and emails."""
    counts = {'ai': [], 'email': []}
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: counts['ai'].append((conversation_id, text)))
    monkeypatch.setattr('app.routes.webhooks.send_reply_notification_email',
                        lambda **kwargs: counts['email'].append(kwargs))
    return counts


@pytest.fixture
def conversation_id(client, make_user, auth_header, sent_sms):  # noqa: F811
    make_user(BROKER_EMAIL, role='broker')
    return client.post(SMS_URL, json=new_conversation_payload(),
                       headers=auth_header(BROKER_EMAIL)).json()['conversation']['id']


def hand_to_human(session, conversation_id):
    conversation = session.get(Conversation, conversation_id)
    conversation.handled_by = 'user'
    session.commit()


def inbound_rows(session, provider_id):
    session.expire_all()
    return session.query(Message).filter_by(direction='inbound', telnyx_id=provider_id).count()


def reply_notifications(session):
    session.expire_all()
    return session.query(Notification).filter_by(type='CUSTOMER_REPLY').all()


@pytest.mark.parametrize('handled_by', ['bobbie', 'user'])
def test_same_id_delivered_twice_is_processed_once(client, session, conversation_id, effects, handled_by):
    if handled_by == 'user':
        hand_to_human(session, conversation_id)

    first = client.post(WEBHOOK_URL, json=inbound_event('tx-1'))
    second = client.post(WEBHOOK_URL, json=inbound_event('tx-1'))

    assert first.status_code == second.status_code == 200
    assert 'duplicate' not in first.json()
    assert second.json()['duplicate'] is True
    assert second.json()['message_id'] == first.json()['message_id']
    assert inbound_rows(session, 'tx-1') == 1
    notifications = reply_notifications(session)
    assert len(notifications) == 1 and notifications[0].unread_count == 1
    assert len(effects['email']) == 1
    assert len(effects['ai']) == (1 if handled_by == 'bobbie' else 0)
    assert session.query(InboundSmsEvent).one().status == 'processed'


def test_two_different_ids_are_both_processed(client, session, conversation_id, effects):
    assert client.post(WEBHOOK_URL, json=inbound_event('tx-a', 'First')).status_code == 200
    assert client.post(WEBHOOK_URL, json=inbound_event('tx-b', 'Second')).status_code == 200

    assert inbound_rows(session, 'tx-a') == inbound_rows(session, 'tx-b') == 1
    assert [text for _, text in effects['ai']] == ['First', 'Second']
    assert reply_notifications(session)[0].unread_count == 2


def test_same_id_delivered_concurrently_is_processed_once(conversation_id, session, effects, monkeypatch):
    # Hold the first request inside processing so the second arrives mid-flight.
    real_notify = webhooks.notify_conversation_reply

    def slow_notify(**kwargs):
        time.sleep(0.5)
        return real_notify(**kwargs)

    monkeypatch.setattr('app.routes.webhooks.notify_conversation_reply', slow_notify)
    start = threading.Barrier(2)
    statuses = []

    def deliver():
        local = TestClient(app, raise_server_exceptions=False, backend_options={'use_uvloop': True})
        start.wait()
        statuses.append(local.post(WEBHOOK_URL, json=inbound_event('tx-race')).status_code)

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    # The loser gets 503 (Telnyx retries later and then sees "duplicate"), or
    # 200 duplicate if the winner had already finished.
    assert sorted(statuses) in ([200, 503], [200, 200])
    assert inbound_rows(session, 'tx-race') == 1
    assert len(effects['ai']) == 1
    assert len(reply_notifications(session)) == 1


def test_failure_after_saving_resumes_on_retry_without_a_second_message(client, session, conversation_id,
                                                                          effects, monkeypatch):
    real_classify = service.record_inbound_classification
    calls = []

    def classify_fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('classifier unavailable')
        return real_classify(*args, **kwargs)

    monkeypatch.setattr('app.routes.webhooks.service.record_inbound_classification', classify_fails_once)

    telnyx = TestClient(app, raise_server_exceptions=False, backend_options={'use_uvloop': True})
    assert telnyx.post(WEBHOOK_URL, json=inbound_event('tx-retry')).status_code == 500  # Telnyx retries
    session.expire_all()
    event = session.query(InboundSmsEvent).one()
    assert event.status == 'failed' and 'classifier unavailable' in event.last_error
    assert inbound_rows(session, 'tx-retry') == 1  # the reply itself is kept
    assert effects['ai'] == [] and effects['email'] == []  # nothing ran for the failed attempt

    retry = client.post(WEBHOOK_URL, json=inbound_event('tx-retry'))
    assert retry.status_code == 200 and retry.json()['retried'] is True
    assert inbound_rows(session, 'tx-retry') == 1
    assert len(effects['ai']) == 1 and len(effects['email']) == 1
    session.expire_all()
    event = session.query(InboundSmsEvent).one()
    assert event.status == 'processed' and event.attempts == 2

    # Any later redelivery is a plain duplicate.
    assert client.post(WEBHOOK_URL, json=inbound_event('tx-retry')).json()['duplicate'] is True
    assert len(effects['ai']) == 1


def test_inbound_stored_before_this_table_existed_is_not_processed_again(client, session, conversation_id,
                                                                        effects):
    session.add(Message(conversation_id=conversation_id, direction='inbound', from_number=CONTACT,
                        to_number=service.FIXED_FROM, text='Old reply', status='received',
                        event_type='message.received', telnyx_id='tx-old'))
    session.commit()

    replay = client.post(WEBHOOK_URL, json=inbound_event('tx-old', 'Old reply'))

    assert replay.status_code == 200 and replay.json()['duplicate'] is True
    assert inbound_rows(session, 'tx-old') == 1
    assert effects['ai'] == [] and reply_notifications(session) == []
