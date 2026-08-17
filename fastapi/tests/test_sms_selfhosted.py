"""The SMS workspace must run entirely on port 8000 — no 5051/5052 services."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models import Booking
from app.sms import calendar_client, calendar_service, service
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
