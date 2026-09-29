"""Tests for the Follow-ups board: only replied threads, and the decisions on them."""

from datetime import datetime, timedelta

import pytest

from app.models import Booking, Conversation, Message
from app.sms import service

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
OTHER_EMAIL = 'rival@othergroup.net'
CONTACT = '+13125848528'
SECOND_CONTACT = '+13125848529'
SMS_URL = '/api/v1/sms/conversations'
FOLLOWUPS_URL = '/api/v1/followups'
APPOINTMENT_ADDRESS = '4517 W Adams St, Chicago, IL 60624'


@pytest.fixture
def sent_sms(monkeypatch):
    """Capture outbound SMS instead of calling the simulator/Telnyx."""
    outbox = []

    def fake_send(payload):
        outbox.append(payload)
        return {'data': {'id': f'sim-{len(outbox)}'}}

    monkeypatch.setattr('app.sms.service.send_sms', fake_send)
    return outbox


@pytest.fixture(autouse=True)
def no_ai_replies(monkeypatch):
    """Bobbie's pipeline needs DeepSeek; the board only cares that a reply landed."""
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: None)


def conversation_payload(**overrides):
    return {
        'contact': CONTACT,
        'name': 'Marcus Webb',
        'property_address': '4517 W Adams St, Austin, TX',
        'outreach_reason': 'The property appears to have come off the market without a recorded sale',
        'ai_enabled': True,
        **overrides,
    }


def start_conversation(client, headers, **overrides) -> int:
    response = client.post(SMS_URL, json=conversation_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()['conversation']['id']


def owner_replies(client, text='Maybe — depends what my place is worth honestly', contact=CONTACT):
    response = client.post('/api/v1/webhooks/telnyx', json={'data': {
        'event_type': 'message.received',
        'payload': {
            'id': f'inbound-{contact}',
            'from': {'phone_number': contact},
            'to': [{'phone_number': service.FIXED_FROM}],
            'text': text,
        },
    }})
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# Who is listed
# --------------------------------------------------------------------------

def test_unauthenticated_cannot_list_followups(client):
    assert client.get(FOLLOWUPS_URL).status_code == 401


def test_only_leads_who_replied_are_listed(client, make_user, auth_header, sent_sms):
    """The SMS tab is the whole inbox; Follow-ups is only the answered threads."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    replied_id = start_conversation(client, headers)
    silent_id = start_conversation(client, headers, contact=SECOND_CONTACT, name='Priya Nguyen')
    owner_replies(client)

    rows = client.get(FOLLOWUPS_URL, headers=headers).json()
    assert [row['id'] for row in rows] == [replied_id]
    assert rows[0]['reply_count'] == 1
    assert rows[0]['followup_state'] == 'pending'
    # The silent thread is still in the SMS workspace, just not here.
    listed = client.get(SMS_URL, headers=headers).json()
    assert {item['id'] for item in listed} == {replied_id, silent_id}


def test_all_scope_includes_replied_and_silent_conversations(client, make_user, auth_header,
                                                             sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    replied_id = start_conversation(client, headers)
    silent_id = start_conversation(client, headers, contact=SECOND_CONTACT, name='Priya Nguyen')
    owner_replies(client)

    rows = client.get(f'{FOLLOWUPS_URL}?scope=all', headers=headers)
    assert rows.status_code == 200, rows.text
    by_id = {row['id']: row for row in rows.json()}
    assert set(by_id) == {replied_id, silent_id}
    assert by_id[replied_id]['reply_count'] == 1
    assert by_id[silent_id]['reply_count'] == 0


def test_followups_reject_unknown_scope(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.get(f'{FOLLOWUPS_URL}?scope=unknown', headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 400


def test_a_broker_never_sees_another_brokerages_followups(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user(OTHER_EMAIL, role='broker', brokerage_id='brokerage-2', brokerage_name='Other Group')
    start_conversation(client, auth_header(BROKER_EMAIL))
    owner_replies(client)

    assert client.get(FOLLOWUPS_URL, headers=auth_header(OTHER_EMAIL)).json() == []


def test_followups_are_ordered_by_the_most_recent_reply(client, make_user, auth_header, session,
                                                        sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    first_id = start_conversation(client, headers)
    second_id = start_conversation(client, headers, contact=SECOND_CONTACT, name='Priya Nguyen')
    owner_replies(client)
    owner_replies(client, 'Send me the estimate', SECOND_CONTACT)

    # The webhook stamps both replies within the same second, so make the gap explicit.
    reply = (session.query(Message)
             .filter(Message.conversation_id == second_id, Message.direction == 'inbound')
             .one())
    reply.created_at = datetime.utcnow() + timedelta(minutes=5)
    session.commit()

    rows = client.get(FOLLOWUPS_URL, headers=headers).json()
    assert [row['id'] for row in rows] == [second_id, first_id]


def test_the_reason_names_an_unanswered_reply(client, make_user, auth_header, sent_sms):
    """Bobbie stepped back and the owner spoke last: the assignee owes a reply."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    client.post(f'{SMS_URL}/{conversation_id}/handover?to=broker', headers=headers)
    owner_replies(client)

    row = client.get(f'{FOLLOWUPS_URL}/{conversation_id}', headers=headers).json()
    assert row['awaiting_broker_reply'] is True
    assert row['reason'] == 'reply_needed'
    assert row['reason_label'] == 'Reply needs an answer'


def test_a_quiet_thread_reports_the_days_of_silence(client, make_user, auth_header, session,
                                                    sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client, 'Not right now, thanks')
    # Bobbie answers, the owner goes quiet, and two days pass.
    conversation = session.query(Conversation).filter(Conversation.id == conversation_id).one()
    service.update_lead_progress(session, conversation, lead_status='processing', dnc_alert=False)
    client.post(f'{SMS_URL}/{conversation_id}/messages', json={'text': 'No problem at all.'},
                headers=headers)
    for message in session.query(Message).filter(Message.conversation_id == conversation_id):
        message.created_at = datetime.utcnow() - timedelta(days=3)
    session.commit()

    row = client.get(f'{FOLLOWUPS_URL}/{conversation_id}', headers=headers).json()
    assert row['reason'] == 'no_response'
    assert row['waiting_days'] == 3
    assert row['reason_label'] == 'No response · 3 days'


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------

def test_accepting_and_declining_a_lead_round_trips(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    accepted = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/state',
                           json={'state': 'accepted'}, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['followup_state'] == 'accepted'

    declined = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/state',
                           json={'state': 'declined'}, headers=headers)
    assert declined.json()['followup_state'] == 'declined'

    # A decision is a note on the outcome; it never moves the thread off Bobbie.
    assert declined.json()['handled_by'] == 'bobbie'
    assert client.get(f'{FOLLOWUPS_URL}?state=declined', headers=headers).json()[0]['id'] == conversation_id
    assert client.get(f'{FOLLOWUPS_URL}?state=pending', headers=headers).json() == []


def test_an_unknown_decision_is_rejected(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    assert client.post(f'{FOLLOWUPS_URL}/{conversation_id}/state',
                       json={'state': 'maybe'}, headers=headers).status_code == 422
    assert client.get(f'{FOLLOWUPS_URL}?state=maybe', headers=headers).status_code == 400


def test_a_manual_status_override_is_not_merged(client, make_user, auth_header, sent_sms):
    """The person reading the thread outranks the classifier, in either direction."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client, 'I might sell at the right price.')
    assert client.get(f'{FOLLOWUPS_URL}/{conversation_id}', headers=headers).json()['lead_status'] == 'interested'

    response = client.patch(f'{FOLLOWUPS_URL}/{conversation_id}',
                            json={'lead_status': 'want_more_info'}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['lead_status'] == 'want_more_info'


def test_marking_do_not_contact_stops_autopilot(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    body = client.patch(f'{FOLLOWUPS_URL}/{conversation_id}',
                        json={'lead_status': 'dnc'}, headers=headers).json()
    assert body['dnc_alert'] is True
    assert body['ai_enabled'] is False
    assert body['handled_by'] == 'broker'
    assert body['reason'] == 'opted_out'


def test_an_unknown_status_is_rejected(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    assert client.patch(f'{FOLLOWUPS_URL}/{conversation_id}',
                        json={'lead_status': 'hot'}, headers=headers).status_code == 422


def test_actions_on_another_brokerages_thread_are_not_found(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user(OTHER_EMAIL, role='broker', brokerage_id='brokerage-2', brokerage_name='Other Group')
    conversation_id = start_conversation(client, auth_header(BROKER_EMAIL))
    owner_replies(client)

    intruder = auth_header(OTHER_EMAIL)
    assert client.get(f'{FOLLOWUPS_URL}/{conversation_id}', headers=intruder).status_code == 404
    assert client.post(f'{FOLLOWUPS_URL}/{conversation_id}/state',
                       json={'state': 'accepted'}, headers=intruder).status_code == 404


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------

def test_booking_an_appointment_texts_the_owner(client, make_user, auth_header, session, sent_sms):
    broker = make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    slots = client.get('/api/v1/sms/calendar/availability', headers=headers).json()['slots']
    assert slots, 'the broker should have open weekday slots'

    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/appointment',
                           json={'start_at': slots[0]['start_at'], 'end_at': slots[0]['end_at'],
                                 'title': 'Home value walkthrough',
                                 'address': APPOINTMENT_ADDRESS, 'notify': True},
                           headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['notified'] is True
    assert body['note'] is None
    assert body['followup']['meeting_booked'] is True
    assert body['followup']['reason'] == 'appointment_booked'

    booking = session.query(Booking).one()
    assert booking.user_id == broker.id
    assert booking.phone == CONTACT
    assert booking.title == 'Home value walkthrough'
    assert booking.location_address == APPOINTMENT_ADDRESS
    # The owner was told, and Bobbie must not answer this confirmation.
    assert sent_sms[-1]['to'] == CONTACT
    assert 'confirmed' in sent_sms[-1]['text']
    assert sent_sms[-1]['suppress_auto_reply'] is True
    # A person booked it, so the owner is told they are meeting that person, and
    # the thread records it as theirs rather than as Bobbie's.
    assert 'meeting with Test User is confirmed' in sent_sms[-1]['text']
    assert f'Address: {APPOINTMENT_ADDRESS}' in sent_sms[-1]['text']
    assert 'google.com/maps/search/' in sent_sms[-1]['text']
    assert 'Join here:' not in sent_sms[-1]['text']
    assert 'Bobbie' not in sent_sms[-1]['text']
    assert sent_sms[-1]['simulation_event_type'] == 'broker.booking'
    # Greeted by first name, as the initial outreach does.
    assert sent_sms[-1]['text'].startswith('Thanks, Marcus.')


def test_a_taken_slot_is_refused(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    slot = client.get('/api/v1/sms/calendar/availability', headers=headers).json()['slots'][0]
    booking = {'start_at': slot['start_at'], 'end_at': slot['end_at'],
               'address': APPOINTMENT_ADDRESS, 'notify': False}
    assert client.post(f'{FOLLOWUPS_URL}/{conversation_id}/appointment', json=booking,
                       headers=headers).status_code == 200
    assert client.post(f'{FOLLOWUPS_URL}/{conversation_id}/appointment', json=booking,
                       headers=headers).status_code == 409


def test_manual_appointment_requires_a_complete_street_address(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)
    slot = client.get('/api/v1/sms/calendar/availability', headers=headers).json()['slots'][0]

    response = client.post(
        f'{FOLLOWUPS_URL}/{conversation_id}/appointment',
        json={
            'start_at': slot['start_at'],
            'end_at': slot['end_at'],
            'address': 'Chicago',
            'notify': False,
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert 'complete street address' in response.json()['detail']


def test_an_opted_out_owner_cannot_be_booked(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)
    slot = client.get('/api/v1/sms/calendar/availability', headers=headers).json()['slots'][0]
    client.patch(f'{FOLLOWUPS_URL}/{conversation_id}', json={'lead_status': 'dnc'}, headers=headers)

    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/appointment',
                           json={'start_at': slot['start_at'], 'end_at': slot['end_at'],
                                 'address': APPOINTMENT_ADDRESS, 'notify': True},
                           headers=headers)
    assert response.status_code == 409
    assert 'opted out' in response.json()['detail']


def test_a_failed_confirmation_still_reports_the_booking(client, make_user, auth_header, session,
                                                         monkeypatch, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)
    slot = client.get('/api/v1/sms/calendar/availability', headers=headers).json()['slots'][0]

    def refuse(*args, **kwargs):
        raise service.SmsDeliveryError('carrier rejected')

    monkeypatch.setattr('app.routes.followups.service.send_and_store_message', refuse)
    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/appointment',
                           json={'start_at': slot['start_at'], 'end_at': slot['end_at'],
                                 'address': APPOINTMENT_ADDRESS, 'notify': True},
                           headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()['notified'] is False
    assert 'carrier rejected' in response.json()['note']
    # The meeting is real even though the text is not.
    assert session.query(Booking).count() == 1
    assert response.json()['followup']['meeting_booked'] is True


# --------------------------------------------------------------------------
# Reply suggestions
# --------------------------------------------------------------------------

def test_reply_suggestions_are_drafted_from_the_thread_history(
        client, make_user, auth_header, sent_sms, monkeypatch):
    seen = {}

    def fake_completion(messages, **options):
        seen['messages'] = messages
        return {'content': '```json\n{"suggestions": ["Happy to run the numbers for you."]}\n```'}

    monkeypatch.setattr('app.sms.reply_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.sms.reply_suggest.deepseek.completion', fake_completion)

    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/reply-suggestions', headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source'] == 'ai'
    assert body['suggestions'] == ['Happy to run the numbers for you.']
    # The owner's latest message is what the drafts must answer.
    assert 'depends what my place is worth' in seen['messages'][1]['content']
    # Suggesting never sends anything.
    assert len(sent_sms) == 1


def test_reply_suggestions_see_the_property_details_behind_the_thread(
        client, make_user, auth_header, session, sent_sms, monkeypatch):
    """An owner asking about their own house should get the imported value back."""
    import json

    from app.models import Lead

    seen = {}

    def fake_completion(messages, **options):
        seen['messages'] = messages
        return {'content': '{"suggestions": ["It shows 3 beds and 2 baths."]}'}

    monkeypatch.setattr('app.sms.reply_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.sms.reply_suggest.deepseek.completion', fake_completion)

    user = make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    session.add(Lead(user_id=user.id, owner_name='Marcus Webb', phone=CONTACT,
                     property_address='4517 W Adams St, Austin, TX',
                     signals='expired,pre_foreclosure',
                     details=json.dumps({'Bedrooms': 3, 'Bathrooms': 2, 'Year Built': 1962}),
                     conversation_id=conversation_id))
    session.commit()
    owner_replies(client, text='How many bedrooms do you have on file for it?')

    body = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/reply-suggestions', headers=headers).json()
    assert body['source'] == 'ai'
    prompt = seen['messages'][1]['content']
    assert '"Bedrooms": 3' in prompt
    assert '"Year Built": 1962' in prompt
    # Visible to the model, but marked as something a draft must not raise.
    assert '"sensitive_signals": ["Pre-Foreclosure"]' in prompt


def test_reply_suggestions_fall_back_to_templates_when_ai_is_down(
        client, make_user, auth_header, sent_sms, monkeypatch):
    from app.sms import deepseek

    def boom(messages, **options):
        raise deepseek.AiUnavailableError('AI provider unreachable')

    monkeypatch.setattr('app.sms.reply_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.sms.reply_suggest.deepseek.completion', boom)

    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)

    body = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/reply-suggestions', headers=headers).json()
    assert body['source'] == 'template'
    assert body['suggestions']
    # The owner asked about value, so the templates should speak to price.
    assert any('price' in text.lower() for text in body['suggestions'])
    assert 'could not be reached' in body['note']


def test_reply_suggestions_for_another_brokerages_thread_are_not_found(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user(OTHER_EMAIL, role='broker', brokerage_id='brokerage-2', brokerage_name='Other Group')
    conversation_id = start_conversation(client, auth_header(BROKER_EMAIL))
    owner_replies(client)

    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/reply-suggestions',
                           headers=auth_header(OTHER_EMAIL))
    assert response.status_code == 404


def test_no_reply_suggestions_for_an_opted_out_owner(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    conversation_id = start_conversation(client, headers)
    owner_replies(client)
    client.patch(f'{FOLLOWUPS_URL}/{conversation_id}', json={'lead_status': 'dnc'}, headers=headers)

    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/reply-suggestions', headers=headers)
    assert response.status_code == 409
