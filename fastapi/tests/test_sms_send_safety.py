"""Outbound SMS safety: no unmounted direct-send route, and opt-out is final for manual sends.

Offline: the shared sender and the legacy Telnyx adapter are both trapped, so a
test that reached either would fail rather than send.
"""

import pytest

from app import db
from app.adapters import telnyx as telnyx_adapter
from app.models import Conversation

SMS_URL = '/api/v1/sms/conversations'
BROKER = 'send-safety-broker@example.com'


@pytest.fixture
def trapped(monkeypatch):
    """Record shared-sender calls; fail on any use of the direct Telnyx adapter."""
    sent = []
    monkeypatch.setattr('app.sms.service.send_sms',
                        lambda payload: sent.append(payload) or {'data': {'id': f'sim-{len(sent)}'}})

    def forbidden(*_args, **_kwargs):
        raise AssertionError('the direct Telnyx adapter must be unreachable')

    monkeypatch.setattr(telnyx_adapter, 'send_message', forbidden)
    monkeypatch.setattr(telnyx_adapter.requests, 'post', forbidden)
    return sent


@pytest.mark.parametrize('authenticated', [False, True], ids=['anonymous', 'authenticated'])
def test_direct_send_route_is_not_mounted(client, make_user, auth_header, trapped, authenticated):
    headers = {}
    if authenticated:
        make_user(BROKER, role='broker')
        headers = auth_header(BROKER)
    for path in ('/api/v1/messages/', '/api/v1/messages'):
        response = client.post(path, headers=headers, json={'to': '+13125550100', 'text': 'hello'})
        assert response.status_code in (404, 405), (path, response.status_code)
    assert trapped == []


def test_no_mounted_route_reaches_the_telnyx_adapter():
    from app.main import app
    for route in app.routes:
        endpoint = getattr(route, 'endpoint', None)
        module = getattr(endpoint, '__module__', '')
        assert module != 'app.routes.messages', route.path
        assert getattr(endpoint, '__globals__', {}).get('send_message') is not telnyx_adapter.send_message, route.path


def _thread(client, headers):
    created = client.post(SMS_URL, headers=headers, json={
        'contact': '+13125550101', 'name': 'Maya Chen', 'property_address': '1 Test St',
        'outreach_reason': 'It came off the market', 'ai_enabled': False,
    })
    assert created.status_code in (200, 201), created.text
    return created.json()['conversation']['id']


@pytest.mark.parametrize('flag', ['dnc_alert', 'lead_status'])
def test_manual_send_to_an_opted_out_owner_is_refused(client, make_user, auth_header, trapped, flag):
    make_user(BROKER, role='broker')
    headers = auth_header(BROKER)
    conversation_id = _thread(client, headers)
    before = len(trapped)
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if flag == 'dnc_alert':
            conversation.dnc_alert = True
        else:
            conversation.lead_status = 'dnc'
        session.commit()
    response = client.post(f'{SMS_URL}/{conversation_id}/messages', headers=headers, json={'text': 'Just checking in'})
    assert response.status_code == 409
    assert len(trapped) == before  # nothing reached the sender


def test_manual_send_to_an_opted_in_owner_still_works(client, make_user, auth_header, trapped):
    make_user(BROKER, role='broker')
    headers = auth_header(BROKER)
    conversation_id = _thread(client, headers)
    response = client.post(f'{SMS_URL}/{conversation_id}/messages', headers=headers, json={'text': 'Following up.'})
    assert response.status_code == 200 and response.json()['event_type'] == 'broker.message'
    assert trapped[-1]['text'] == 'Following up.'
