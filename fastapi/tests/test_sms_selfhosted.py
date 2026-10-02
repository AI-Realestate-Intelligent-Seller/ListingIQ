"""The SMS workspace must run entirely on port 8000 — no 5051/5052 services."""

from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models import Booking, Conversation, Lead, Message
from app.sms import bobbie, calendar_client, calendar_service, service
from app.sms.knowledge import search_bobbie_knowledge
from app.sms.knowledge_index import BobbieKnowledgeIndex

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
CONTACT = '+13125848528'


@pytest.fixture(autouse=True)
def self_hosted(monkeypatch):
    """Force the in-process defaults regardless of the developer's .env."""
    monkeypatch.setitem(settings['sms'], 'rag_url', '')
    monkeypatch.setitem(settings['sms'], 'calendar_url', '')
    monkeypatch.setitem(settings['telnyx'], 'simulation_url', '')
    monkeypatch.setitem(settings['telnyx'], 'mode', 'simulation')


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound HTTP call is a regression: nothing should leave the process."""
    def explode(*args, **kwargs):
        raise AssertionError(f'unexpected outbound HTTP call: {args[:1]}')

    monkeypatch.setattr('requests.post', explode)
    monkeypatch.setattr('requests.get', explode)


# --------------------------------------------------------------------------
# Outbound SMS without the simulator process
# --------------------------------------------------------------------------

def test_send_sms_simulates_in_process(no_network):
    response = service.send_sms({'to': CONTACT, 'text': 'Hello from Bobbie'})
    data = response['data']
    # Same Telnyx envelope the external simulator returned.
    assert data['simulation'] is True
    assert data['direction'] == 'outbound'
    assert data['to'][0] == {'phone_number': CONTACT, 'status': 'queued'}
    assert data['from']['phone_number'] == service.FIXED_FROM
    assert data['id']


@pytest.mark.parametrize('payload', [
    {'to': '5551234567', 'text': 'hi'},
    {'to': CONTACT, 'text': '   '},
    {'to': None, 'text': 'hi'},
])
def test_in_process_simulation_validates_like_telnyx(payload, no_network):
    with pytest.raises(service.SmsDeliveryError):
        service.send_sms(payload)


def test_conversation_creation_needs_no_external_service(
        client, make_user, auth_header, session, no_network):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post('/api/v1/sms/conversations', json={
        'contact': CONTACT,
        'name': 'Maya Chen',
        'property_address': '123 Maple Ave, Austin, TX',
        'outreach_reason': 'It came off the market without a recorded sale',
    }, headers=auth_header(BROKER_EMAIL))

    assert response.status_code == 201, response.text
    assert response.json()['started'] is True
    assert response.json()['conversation']['message_count'] == 1


# --------------------------------------------------------------------------
# Calendar without the 5052 service
# --------------------------------------------------------------------------

def test_availability_is_generated_in_process(client, make_user, auth_header, session, no_network):
    broker = make_user(BROKER_EMAIL, role='broker')
    availability = calendar_client.fetch_availability(session, broker.id)

    assert availability['slot_minutes'] == settings['sms']['calendar_slot_minutes']
    assert availability['timezone'] == settings['ai']['timezone']
    assert availability['slots'], 'expected open weekday slots'
    for slot in availability['slots'][:5]:
        assert slot['start_at'].endswith('Z')
        assert ' at ' in slot['label']
        # Never offer a slot in the past.
        assert datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc)


def _local_times(availability, zone_name):
    zone = ZoneInfo(zone_name)
    return [datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00')).astimezone(zone)
            for slot in availability['slots']]


def test_default_availability_keeps_business_hours(client, make_user, auth_header, session, no_network):
    broker = make_user(BROKER_EMAIL, role='broker')
    availability = calendar_client.fetch_availability(session, broker.id)

    hours = {start.hour for start in _local_times(availability, settings['ai']['timezone'])}
    assert min(hours) >= 9 and max(hours) < 18


def test_manual_booking_lists_the_whole_day_in_the_brokers_timezone(client, make_user, auth_header, no_network):
    make_user(BROKER_EMAIL, role='broker')
    response = client.get('/api/v1/sms/calendar/availability',
                          params={'all_day': 'true', 'timezone': 'Asia/Karachi'},
                          headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 200, response.text
    availability = response.json()

    assert availability['timezone'] == 'Asia/Karachi'
    starts = _local_times(availability, 'Asia/Karachi')
    # Every half hour from midnight to 11:30 PM local time is offered.
    full_day = {(start.hour, start.minute) for start in starts if start.date() == starts[-1].date()}
    assert len(full_day) == 48
    # Weekends included.
    assert {start.weekday() for start in starts} == set(range(7))
    assert availability['slots'][0]['label'].endswith('Asia/Karachi')


def test_manual_booking_falls_back_for_an_unknown_timezone(client, make_user, auth_header, no_network):
    make_user(BROKER_EMAIL, role='broker')
    response = client.get('/api/v1/sms/calendar/availability',
                          params={'all_day': 'true', 'timezone': 'Not/AZone'},
                          headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 200
    assert response.json()['timezone'] == settings['ai']['timezone']


def test_booking_persists_and_removes_the_slot(client, make_user, auth_header, session, no_network):
    broker = make_user(BROKER_EMAIL, role='broker')
    slot = calendar_client.fetch_availability(session, broker.id)['slots'][0]

    class FakeConversation:
        contact, name, user_id = CONTACT, 'Maya Chen', broker.id

    booking = calendar_client.create_booking(FakeConversation(), slot, session)
    assert booking['join_url'].startswith(settings['frontend_url'])
    assert 'confirmed for' in booking['message']
    assert session.query(Booking).count() == 1

    remaining = [item['start_at'] for item in calendar_client.fetch_availability(session, broker.id)['slots']]
    assert slot['start_at'] not in remaining


def test_double_booking_is_rejected(client, make_user, auth_header, session, no_network):
    broker = make_user(BROKER_EMAIL, role='broker')
    slot = calendar_client.fetch_availability(session, broker.id)['slots'][0]

    class FakeConversation:
        contact, name, user_id = CONTACT, 'Maya Chen', broker.id

    calendar_client.create_booking(FakeConversation(), slot, session)
    with pytest.raises(calendar_client.SlotTakenError):
        calendar_client.create_booking(FakeConversation(), slot, session)


def test_each_broker_keeps_a_separate_calendar(client, make_user, auth_header, session, no_network):
    first = make_user(BROKER_EMAIL, role='broker')
    second = make_user('other@linchpinglobal.net', role='broker')
    slot = calendar_client.fetch_availability(session, first.id)['slots'][0]

    class FakeConversation:
        contact, name, user_id = CONTACT, 'Maya Chen', first.id

    calendar_client.create_booking(FakeConversation(), slot, session)
    # The same time is still open for the other broker.
    assert slot['start_at'] in [item['start_at']
                                for item in calendar_client.fetch_availability(session, second.id)['slots']]


def test_calendar_endpoints_are_scoped_to_the_caller(client, make_user, auth_header, session, no_network):
    broker = make_user(BROKER_EMAIL, role='broker')
    make_user('other@linchpinglobal.net', role='broker')
    headers = auth_header(BROKER_EMAIL)

    availability = client.get('/api/v1/sms/calendar/availability', headers=headers)
    assert availability.status_code == 200 and availability.json()['slots']

    session.add(Booking(user_id=broker.id, phone=CONTACT, name='Maya', title='Property consultation',
                        start_at=datetime.utcnow() + timedelta(days=1),
                        end_at=datetime.utcnow() + timedelta(days=1, minutes=30),
                        join_token='token-1', created_at=datetime.utcnow()))
    session.commit()

    assert len(client.get('/api/v1/sms/calendar/bookings', headers=headers).json()) == 1
    other = auth_header('other@linchpinglobal.net')
    assert client.get('/api/v1/sms/calendar/bookings', headers=other).json() == []
    assert client.get('/api/v1/sms/calendar/availability').status_code == 401


@pytest.mark.parametrize('calendar_state', ['call_accepted', 'time_proposed', 'booking_confirmed'])
def test_bobbie_hands_meetings_to_a_person_without_touching_the_calendar(
        client, make_user, auth_header, session, monkeypatch, calendar_state):
    broker = make_user(BROKER_EMAIL, role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    conversation = Conversation(
        contact=CONTACT,
        user_id=broker.id,
        name='Oksana',
        property_address='416 Glendale Rd, Glenview, IL, 60025',
        ai_enabled=True,
        handled_by='bobbie',
        lead_status='ready_to_sell',
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    session.add_all([
        Message(
            conversation_id=conversation.id,
            direction='inbound',
            from_number=CONTACT,
            to_number=service.FIXED_FROM,
            text='9:00 works for me',
            status='received',
            event_type='message.received',
            created_at=datetime.utcnow(),
        ),
        Lead(
            user_id=broker.id,
            owner_name='Oksana',
            phone=CONTACT,
            property_address=conversation.property_address,
            conversation_id=conversation.id,
            assigned_agent_id=agent.id,
        ),
    ])
    session.commit()

    monkeypatch.setattr(bobbie, 'analyze_conversation_disposition', lambda *_: {
        'action': 'continue',
        'intent': 'schedule_visit',
        'outcome': 'positive',
        'lead_status': 'ready_to_sell',
        'confidence': 1.0,
        'conversation_stage': 'scheduling',
        'next_step': 'schedule',
        'qualification_focus': '',
        'calendar': {
            'state': calendar_state,
            'should_fetch_availability': True,
            'requested_time_text': '9:00',
            'reason': 'owner selected the offered slot',
        },
        'reason': 'owner selected the offered slot',
    })
    no_calendar = lambda *_args, **_kwargs: pytest.fail('Bobbie must not use the calendar')
    monkeypatch.setattr(calendar_client, 'fetch_availability', no_calendar)
    monkeypatch.setattr(calendar_client, 'create_booking', no_calendar)
    monkeypatch.setattr(bobbie, 'resolve_calendar_action', no_calendar)
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _session, _conversation, text, event_type, *rest: sent.append((text, event_type))
                        or Message(id=1))

    service.process_ai_reply(conversation.id, '9:00 works for me')

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated.ai_enabled is False
    assert updated.handled_by == 'broker'
    assert updated.lead_status == 'location_discussion'
    assert updated.meeting_booked is False
    assert session.query(Booking).count() == 0
    assert len(sent) == 1
    text, event_type = sent[0]
    assert event_type == 'ai.reply'
    assert 'someone from our team' in text.lower()
    assert not any(char.isdigit() for char in text), 'Bobbie must not propose a time'

    followup = client.get(
        f'/api/v1/followups/{conversation.id}',
        headers=auth_header('agent@linchpinglobal.net'),
    )
    assert followup.status_code == 200, followup.text
    assert followup.json()['reason_label'] == 'Location to confirm'
    assert followup.json()['reason'] == 'location_discussion'
    assert followup.json()['awaiting_broker_reply'] is True


def test_meeting_links_point_at_the_frontend_not_a_calendar_port():
    assert calendar_service.join_url('abc123') == f"{settings['frontend_url'].rstrip('/')}/meeting/abc123"
    assert '5052' not in calendar_service.join_url('abc123')


# --------------------------------------------------------------------------
# Bobbie's knowledge base without the 5051 service
# --------------------------------------------------------------------------

def test_knowledge_index_searches_the_local_pdf(no_network):
    result = search_bobbie_knowledge('What experience does Bobbie have with RE/MAX?', 3)
    assert result['document'].endswith('.pdf')
    assert result['matches'], 'expected at least one knowledge match'
    assert len(result['matches']) <= 3
    top = result['matches'][0]
    assert top['score'] > 0 and top['page'] >= 1 and top['text']
    # The caution stays attached so Bobbie never upgrades unverified claims.
    assert 'do not upgrade unverified claims' in result['document_status']


def test_knowledge_search_endpoint(client, make_user, auth_header, no_network):
    make_user(BROKER_EMAIL, role='broker')
    response = client.get('/api/v1/sms/knowledge/search?q=commission&limit=2',
                          headers=auth_header(BROKER_EMAIL))
    assert response.status_code == 200
    assert len(response.json()['matches']) <= 2
    assert client.get('/api/v1/sms/knowledge/search?q=commission').status_code == 401


def test_missing_knowledge_pdf_reports_clearly(monkeypatch):
    index = BobbieKnowledgeIndex('/nonexistent/knowledge.pdf')
    with pytest.raises(RuntimeError, match='not found'):
        index.search('anything')
