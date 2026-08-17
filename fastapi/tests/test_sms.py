"""Tests for the SMS workspace: ported Bobbie guards and the broker-scoped API."""

import pytest

from app import db
from app.models import Conversation, Message
from app.sms import service
from app.sms.bobbie import exact_offered_slot
from app.sms.classifier import classify_lead_message, is_opt_out, merge_lead_status
from app.sms.outreach import build_initial_outreach, build_single_lead_context
from app.sms.policy import (
    find_conversation_repetition,
    find_reply_policy_violations,
    fit_complete_sms,
    safe_grounded_fallback,
)

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
CONTACT = '+13125848528'
SMS_URL = '/api/v1/sms/conversations'


@pytest.fixture
def sent_sms(monkeypatch):
    """Capture outbound SMS instead of calling the simulator/Telnyx."""
    outbox = []

    def fake_send(payload):
        outbox.append(payload)
        return {'data': {'id': f'sim-{len(outbox)}'}}

    monkeypatch.setattr('app.sms.service.send_sms', fake_send)
    return outbox


def new_conversation_payload(**overrides):
    return {
        'contact': CONTACT,
        'name': 'Maya Chen',
        'property_address': '123 Maple Ave, Austin, TX',
        'outreach_reason': 'The property appears to have come off the market without a recorded sale',
        'ai_enabled': True,
        **overrides,
    }


# --------------------------------------------------------------------------
# Ported policy guards
# --------------------------------------------------------------------------

def test_fit_complete_sms_trims_to_a_sentence():
    text = ('This first sentence is deliberately long enough to survive the sentence-boundary check applied '
            'to a trimmed draft. And here is a second sentence that pushes it past the limit entirely.')
    trimmed = fit_complete_sms(text, 120)
    assert trimmed.endswith('.') and len(trimmed) <= 120


def test_fit_complete_sms_falls_back_to_an_ellipsis_without_a_sentence_break():
    # No sentence end past character 80, so the draft is cut on a word boundary.
    text = 'word ' * 60
    assert fit_complete_sms(text, 120).endswith('…')


def test_fit_complete_sms_keeps_short_replies_untouched():
    assert fit_complete_sms('Short and complete.', 240) == 'Short and complete.'


@pytest.mark.parametrize('draft,expected', [
    ('We typically charge a 3% commission on the sale.', 'unsupported_fee'),
    ('Call me at 555 123 4567 today.', 'invented_phone'),
    ('Email me at bobbie@remax.com.', 'invented_email'),
    ('I have active buyers looking in your area right now.', 'unsupported_buyer'),
    ('I will send you the recent sales data by email shortly.', 'unsupported_delivery'),
    ('I have sold many homes nearby.', 'unsupported_production'),
    ('Bobbie will reach out to you shortly.', 'identity_switch'),
    ('I have been selling homes since 2011 in this area.', 'unsupported_tenure'),
])
def test_policy_blocks_unsupported_claims(draft, expected):
    assert expected in find_reply_policy_violations(draft)


def test_policy_allows_an_honest_buyer_denial():
    draft = 'I don’t have a verified buyer ready today, so I won’t claim otherwise.'
    assert 'unsupported_buyer' not in find_reply_policy_violations(draft)


def test_policy_flags_phone_source_claim_only_when_source_is_unknown():
    draft = 'I found your number in public property records.'
    assert 'unsupported_phone_source' in find_reply_policy_violations(draft, phone_source_known=False)
    assert 'unsupported_phone_source' not in find_reply_policy_violations(draft, phone_source_known=True)


def test_policy_detects_repeated_questions():
    history = [{'direction': 'outbound', 'text': 'Would you be open to a quick call this week?'}]
    assert find_conversation_repetition('Would you be open to a quick call tomorrow?', history) == 'repeated_question'
    assert find_conversation_repetition('What condition is the roof in?', history) == ''


def test_grounded_fallback_never_repeats_itself():
    fallback = safe_grounded_fallback('what commission do you charge?', ['unsupported_fee'], [])
    assert 'fee' in fallback.lower() or 'commission' in fallback.lower()
    history = [{'direction': 'outbound', 'text': fallback}]
    assert safe_grounded_fallback('what commission do you charge?', ['unsupported_fee'], history) != fallback


# --------------------------------------------------------------------------
# Lead classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize('text', ['STOP', 'please remove me', 'do not contact me again'])
def test_opt_out_detection(text):
    assert is_opt_out(text)
    assert classify_lead_message(text)['lead_status'] == 'dnc'


def test_conditional_objection_is_not_a_rejection():
    # "Not interested in repeating that process" still leaves an opening, so the
    # classifier declines to label it at all rather than ending the conversation.
    assert classify_lead_message('Not interested in going through that process again') is None
    assert classify_lead_message('Not interested, I have to pass')['terminal'] is True


def test_buyer_condition_outranks_not_interested():
    assert classify_lead_message('Do you have a ready buyer?')['lead_status'] == 'interested'


def test_merge_lead_status_never_downgrades_a_terminal_status():
    assert merge_lead_status('interested', 'processing') == 'interested'
    assert merge_lead_status('not_interested', 'interested') == 'not_interested'
    assert merge_lead_status('interested', 'dnc') == 'dnc'


# --------------------------------------------------------------------------
# Outreach + closing detection
# --------------------------------------------------------------------------

def test_initial_outreach_uses_the_first_name_and_property():
    text = build_initial_outreach('Maya Chen', '123 Maple Ave.', 'It came off the market.')
    assert text.startswith('Hey Maya,')
    assert '123 Maple Ave' in text
    assert text.endswith('Bobbie Fisher – RE/MAX')


def test_lead_context_marks_the_phone_source_as_unknown():
    context = build_single_lead_context('123 Maple Ave', 'Came off the market')
    assert context['available'] is True
    assert context['phone_number_source']['available'] is False


@pytest.mark.parametrize('text,closing', [
    ('Take care!', True),
    ('Sounds good, talk soon.', True),
    ('Would 2pm work for you?', False),
    ('I can do Tuesday at 10:00 AM. Does that work?', False),
])
def test_closing_detection(text, closing):
    assert service.is_conversation_closing(text) is closing


def test_exact_offered_slot_matches_only_what_bobbie_offered():
    availability = {'slots': [
        {'start_at': '2026-08-12T10:00:00', 'end_at': '2026-08-12T10:30:00',
         'label': 'Wednesday, August 12, 2026 at 10:00 AM America/Chicago'},
        {'start_at': '2026-08-13T14:00:00', 'end_at': '2026-08-13T14:30:00',
         'label': 'Thursday, August 13, 2026 at 2:00 PM America/Chicago'},
    ]}
    history = [
        {'direction': 'outbound', 'text': 'I can do Wednesday, August 12 at 10:00 AM or Thursday, August 13 at 2:00 PM. Which works?'},
        {'direction': 'inbound', 'text': '10 am works'},
    ]
    assert exact_offered_slot(history, availability)['start_at'] == '2026-08-12T10:00:00'

    unoffered = [
        {'direction': 'outbound', 'text': 'Would you be open to a quick call?'},
        {'direction': 'inbound', 'text': '9 am works'},
    ]
    assert exact_offered_slot(unoffered, availability) is None


# --------------------------------------------------------------------------
# API: authorization and ownership
# --------------------------------------------------------------------------

def test_unauthenticated_cannot_list_conversations(client):
    assert client.get(SMS_URL).status_code == 401


def test_broker_creates_a_conversation_and_bobbie_sends_the_intro(
        client, make_user, auth_header, session, sent_sms):
    broker = make_user(BROKER_EMAIL, role='broker')
    response = client.post(SMS_URL, json=new_conversation_payload(), headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 201, response.text

    body = response.json()
    assert body['started'] is True
    assert body['conversation']['contact'] == CONTACT
    assert body['conversation']['ai_enabled'] is True

    conversation = session.query(Conversation).one()
    assert conversation.user_id == broker.id
    assert conversation.lead_status == 'processing'

    # The intro SMS actually went out and was stored on the thread.
    assert len(sent_sms) == 1
    assert sent_sms[0]['to'] == CONTACT
    assert sent_sms[0]['text'].startswith('Hey Maya,')
    message = session.query(Message).one()
    assert message.direction == 'outbound'
    assert message.event_type == 'outreach.initial'
    assert message.telnyx_id == 'sim-1'


def test_new_threads_are_owned_by_bobbie(client, make_user, auth_header, session, sent_sms):
    """Bobbie is the default sender; there is no simulated owner any more."""
    make_user(BROKER_EMAIL, role='broker')
    payload = new_conversation_payload()
    payload.pop('ai_enabled')
    body = client.post(SMS_URL, json=payload, headers=auth_header(BROKER_EMAIL)).json()

    assert body['started'] is True
    assert body['conversation']['ai_enabled'] is True
    assert body['conversation']['handled_by'] == 'bobbie'
    assert body['conversation']['awaiting_broker_reply'] is False
    # Simulation-only fields are no longer sent to the provider.
    assert 'simulation_recipient_enabled' not in sent_sms[0]


def test_ai_disabled_conversation_sends_nothing(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(SMS_URL, json=new_conversation_payload(ai_enabled=False),
                           headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 201
    assert response.json()['started'] is False
    assert response.json()['conversation']['handled_by'] == 'broker'
    assert sent_sms == []


def test_duplicate_contact_is_rejected(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    assert client.post(SMS_URL, json=new_conversation_payload(), headers=headers).status_code == 201
    duplicate = client.post(SMS_URL, json=new_conversation_payload(), headers=headers)
    assert duplicate.status_code == 409


@pytest.mark.parametrize('contact', ['5551234567', '+1', 'not-a-phone', ''])
def test_invalid_phone_numbers_are_rejected(client, make_user, auth_header, contact, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(SMS_URL, json=new_conversation_payload(contact=contact),
                           headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 422


def test_brokers_cannot_see_another_brokers_threads(
        client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker', brokerage_id='brokerage-2')
    created = client.post(SMS_URL, json=new_conversation_payload(), headers=auth_header(BROKER_EMAIL))
    conversation_id = created.json()['conversation']['id']

    other = auth_header('other.broker@linchpinglobal.net')
    assert client.get(SMS_URL, headers=other).json() == []
    assert client.get(f'{SMS_URL}/{conversation_id}', headers=other).status_code == 404
    assert client.get(f'{SMS_URL}/{conversation_id}/messages', headers=other).status_code == 404
    assert client.post(f'{SMS_URL}/{conversation_id}/messages', json={'text': 'hi'},
                       headers=other).status_code == 404
    assert client.delete(f'{SMS_URL}/{conversation_id}', headers=other).status_code == 404


def test_conversation_toggles_and_manual_send(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    toggled = client.patch(f'{SMS_URL}/{conversation_id}', json={'ai_enabled': False}, headers=headers)
    assert toggled.status_code == 200 and toggled.json()['ai_enabled'] is False

    sent = client.post(f'{SMS_URL}/{conversation_id}/messages', json={'text': 'Following up by hand.'},
                       headers=headers)
    assert sent.status_code == 200
    assert sent.json()['direction'] == 'outbound'
    assert sent.json()['event_type'] == 'broker.message'
    assert sent_sms[-1]['text'] == 'Following up by hand.'

    listed = client.get(f'{SMS_URL}/{conversation_id}/messages', headers=headers).json()
    assert [item['direction'] for item in listed] == ['outbound', 'outbound']


def test_inbound_reply_lands_on_the_thread_and_wakes_bobbie(
        client, make_user, auth_header, session, sent_sms, monkeypatch):
    replies = []
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: replies.append((conversation_id, text)))
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    webhook = client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {
            'id': 'inbound-1',
            'from': {'phone_number': CONTACT},
            'to': [{'phone_number': service.FIXED_FROM}],
            'text': 'I might sell at the right price.',
        },
    }})
    assert webhook.status_code == 200
    assert webhook.json()['conversation_id'] == conversation_id

    messages = client.get(f'{SMS_URL}/{conversation_id}/messages', headers=headers).json()
    assert messages[-1]['direction'] == 'inbound'
    assert messages[-1]['text'] == 'I might sell at the right price.'

    # Inbound is classified, and Bobbie was scheduled to answer.
    session.expire_all()
    assert session.query(Conversation).one().lead_status == 'interested'
    assert replies == [(conversation_id, 'I might sell at the right price.')]


def test_inbound_from_an_unknown_number_is_ignored(client):
    response = client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {'from': {'phone_number': '+19995550000'}, 'to': [], 'text': 'hello?'},
    }})
    assert response.status_code == 404
    assert db.SessionLocal().query(Conversation).count() == 0


def test_opt_out_reply_stops_autopilot(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    # An opt-out is handled without any model call.
    service.process_ai_reply(conversation_id, 'STOP')

    session.expire_all()
    conversation = session.query(Conversation).one()
    assert conversation.ai_enabled is False
    assert conversation.lead_status == 'dnc'
    assert conversation.dnc_alert is True
    assert conversation.processed_at is not None
    assert sent_sms[-1]['text'].startswith('Understood')


def test_delete_removes_the_thread_and_its_messages(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']
    assert client.delete(f'{SMS_URL}/{conversation_id}', headers=headers).status_code == 200
    assert session.query(Conversation).count() == 0
    assert session.query(Message).count() == 0


# --------------------------------------------------------------------------
# Going live on Telnyx
# --------------------------------------------------------------------------

def test_outbound_uses_the_configured_sending_number(monkeypatch):
    from app.core.config import settings

    monkeypatch.setitem(settings['telnyx'], 'from_number', '+12245798015')
    assert service.sending_number() == '+12245798015'
    monkeypatch.setitem(settings['telnyx'], 'from_number', '+15551230000')
    assert service.sending_number() == '+15551230000'
    # Falls back to the built-in default rather than sending with no number.
    monkeypatch.setitem(settings['telnyx'], 'from_number', '')
    assert service.sending_number() == service.FIXED_FROM


def test_telnyx_mode_requires_an_api_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setitem(settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(settings['telnyx'], 'api_key', '')
    with pytest.raises(service.SmsDeliveryError, match='TELNYX_API_KEY'):
        service.send_sms({'to': CONTACT, 'text': 'hello'})


def test_telnyx_mode_strips_simulation_only_fields(monkeypatch):
    """Real Telnyx rejects unknown body fields."""
    from app.core.config import settings

    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {'data': {'id': 'telnyx-1'}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({'url': url, 'body': json, 'headers': headers})
        return FakeResponse()

    monkeypatch.setitem(settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(settings['telnyx'], 'api_key', 'KEY-test')
    monkeypatch.setitem(settings['telnyx'], 'from_number', '+12245798015')
    monkeypatch.setattr('app.sms.service.requests.post', fake_post)

    service.send_sms({
        'to': CONTACT, 'text': 'hello',
        'simulation_event_type': 'message.sent',
        'simulation_recipient_enabled': False,
        'simulation_lead_context': {'a': 1},
        'suppress_auto_reply': False,
    })

    assert captured['url'] == 'https://api.telnyx.com/v2/messages'
    assert captured['headers']['Authorization'] == 'Bearer KEY-test'
    assert captured['body'] == {'to': CONTACT, 'text': 'hello', 'from': '+12245798015'}


# --------------------------------------------------------------------------
# Webhook signature verification
# --------------------------------------------------------------------------

def test_webhook_accepts_unsigned_posts_when_no_public_key_is_set(client, make_user, auth_header,
                                                                 sent_sms):
    """Local simulator posts are unsigned; verification is opt-in."""
    make_user(BROKER_EMAIL, role='broker')
    client.post(SMS_URL, json=new_conversation_payload(), headers=auth_header(BROKER_EMAIL))
    response = client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {'from': {'phone_number': CONTACT}, 'to': [], 'text': 'hi'},
    }})
    assert response.status_code == 200


def test_webhook_rejects_unsigned_posts_once_a_public_key_is_set(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setitem(settings['telnyx'], 'public_key',
                        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    response = client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {'from': {'phone_number': CONTACT}, 'to': [], 'text': 'hi'},
    }})
    assert response.status_code == 401


def test_webhook_rejects_a_stale_timestamp(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setitem(settings['telnyx'], 'public_key',
                        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    response = client.post(
        '/api/v1/webhooks/telnyx',
        json={'data': {'event_type': 'message.received',
                       'payload': {'from': {'phone_number': CONTACT}, 'to': [], 'text': 'hi'}}},
        headers={'telnyx-signature-ed25519': 'c2ln', 'telnyx-timestamp': '1000000000'},
    )
    assert response.status_code == 401


def test_a_genuine_signature_is_accepted(client, make_user, auth_header, sent_sms):
    """Sign a payload with a generated key and check it verifies end to end."""
    import base64
    import json as jsonlib
    import time

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    from app.core.config import settings

    private = Ed25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)

    make_user(BROKER_EMAIL, role='broker')
    client.post(SMS_URL, json=new_conversation_payload(), headers=auth_header(BROKER_EMAIL))

    body = jsonlib.dumps({'data': {
        'event_type': 'message.received',
        'payload': {'from': {'phone_number': CONTACT}, 'to': [], 'text': 'I might sell.'},
    }}).encode()
    timestamp = str(int(time.time()))
    signature = base64.b64encode(private.sign(f'{timestamp}|'.encode() + body)).decode()

    original = settings['telnyx'].get('public_key')
    settings['telnyx']['public_key'] = base64.b64encode(public_bytes).decode()
    try:
        response = client.post(
            '/api/v1/webhooks/telnyx',
            content=body,
            headers={'content-type': 'application/json',
                     'telnyx-signature-ed25519': signature,
                     'telnyx-timestamp': timestamp},
        )
    finally:
        settings['telnyx']['public_key'] = original
    assert response.status_code == 200, response.text
    assert response.json()['ok'] is True


# --------------------------------------------------------------------------
# Bobbie ↔ broker handover
# --------------------------------------------------------------------------

def test_a_broker_message_takes_the_thread_off_bobbie(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    client.post(f'{SMS_URL}/{conversation_id}/messages', json={'text': 'I will take this one.'},
                headers=headers)

    body = client.get(f'{SMS_URL}/{conversation_id}', headers=headers).json()
    assert body['handled_by'] == 'broker'
    assert body['ai_enabled'] is False


def test_bobbie_never_answers_a_broker_owned_thread(client, make_user, auth_header, session,
                                                    sent_sms, monkeypatch):
    """The critical rule: after handover an owner reply waits for the broker."""
    replies = []
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: replies.append(conversation_id))
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    client.post(f'{SMS_URL}/{conversation_id}/handover?to=broker', headers=headers)
    client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {'from': {'phone_number': CONTACT}, 'to': [], 'text': 'Can you call me?'},
    }})

    assert replies == [], 'Bobbie must not be woken on a broker-owned thread'
    body = client.get(f'{SMS_URL}/{conversation_id}', headers=headers).json()
    assert body['awaiting_broker_reply'] is True


def test_the_pipeline_refuses_to_run_when_the_broker_owns_the_thread(client, make_user, auth_header,
                                                                    session, sent_sms):
    """Even called directly, the AI pipeline is a no-op after handover."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']
    client.post(f'{SMS_URL}/{conversation_id}/handover?to=broker', headers=headers)

    before = len(sent_sms)
    service.process_ai_reply(conversation_id, 'Are you there?')
    assert len(sent_sms) == before, 'no automatic reply may be sent'


def test_the_broker_can_hand_the_thread_back_to_bobbie(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    client.post(f'{SMS_URL}/{conversation_id}/handover?to=broker', headers=headers)
    body = client.post(f'{SMS_URL}/{conversation_id}/handover?to=bobbie', headers=headers).json()
    assert body['handled_by'] == 'bobbie'
    assert body['ai_enabled'] is True


def test_handover_rejects_an_unknown_target(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']
    assert client.post(f'{SMS_URL}/{conversation_id}/handover?to=someone',
                       headers=headers).status_code == 400


def test_opt_out_hands_the_thread_to_the_broker(client, make_user, auth_header, session, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = client.post(SMS_URL, json=new_conversation_payload(),
                                  headers=headers).json()['conversation']['id']

    service.process_ai_reply(conversation_id, 'STOP')

    body = client.get(f'{SMS_URL}/{conversation_id}', headers=headers).json()
    assert body['handled_by'] == 'broker' and body['ai_enabled'] is False


# --------------------------------------------------------------------------
# Session refresh
# --------------------------------------------------------------------------

def test_refresh_returns_a_new_session(client, make_user):
    make_user(BROKER_EMAIL, role='broker')
    login = client.post('/api/v1/auth/login',
                        json={'email': BROKER_EMAIL, 'password': 'Password123!', 'full_name': None})
    refresh_token = login.json()['refresh_token']

    response = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh_token})
    assert response.status_code == 200
    body = response.json()
    assert body['access_token'] and body['user']['email'] == BROKER_EMAIL
    # The new token works.
    assert client.get('/api/v1/auth/me',
                      headers={'Authorization': f"Bearer {body['access_token']}"}).status_code == 200


def test_refresh_rejects_a_bad_token(client):
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': 'nonsense'}).status_code == 401
