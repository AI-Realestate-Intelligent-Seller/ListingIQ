"""Send to Lofty: preview, AI summary, and the resumable Lofty hand-off.

Lofty and the AI provider are always mocked. The fake Lofty below records
every call so tests can assert exactly what would have been sent.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

import pytest

from app import db
from app.core.config import settings
from app.lofty import client as lofty_client_module
from app.lofty import export as lofty_export
from app.lofty.auth import ApiKeyCredentials
from app.lofty.client import LoftyClient, LoftyError
from app.models import Conversation, FeatureFlag, Lead, LoftyExport, Message

FOLLOWUPS_URL = '/api/v1/followups'
BROKER = 'broker@spacematchtest.com'
AGENT = 'agent@spacematchtest.com'
OUTSIDER = 'broker@othergrouptest.com'
OTHER_TENANT_BROKER = 'broker@unconnectedtest.com'
CONTACT = '+13125550123'
API_KEY = 'lofty-secret-key-123'


class FakeLofty:
    """In-memory stand-in for LoftyClient with switchable failures."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.existing: dict[str, list[str]] = {}  # phone/email -> lead ids
        self.next_lead_id = 9001
        self.notes: list[dict] = []
        self.muted: set[str] = set()
        self.fail: dict[str, Exception] = {}
        self.fail_note_at: int | None = None
        self.mute_reports: str | None = None

    def _maybe_fail(self, name):
        error = self.fail.pop(name, None)
        if error:
            raise error

    def find_leads(self, *, phone=None, email=None):
        self.calls.append(('find', phone or email))
        self._maybe_fail('find')
        return list(self.existing.get(phone or email, []))

    def create_lead(self, payload):
        self.calls.append(('create', payload))
        self._maybe_fail('create')
        lead_id = str(self.next_lead_id)
        self.next_lead_id += 1
        for phone in payload.get('phones', []):
            self.existing.setdefault(phone, []).append(lead_id)
        return lead_id

    def update_lead(self, lead_id, payload):
        self.calls.append(('update', lead_id, payload))
        self._maybe_fail('update')

    def mute_sales_agent(self, lead_id):
        self.calls.append(('mute', lead_id))
        self._maybe_fail('mute')
        self.muted.add(lead_id)

    def sales_agent_muted(self, lead_id):
        self.calls.append(('mute_status', lead_id))
        if self.mute_reports is not None:
            return self.mute_reports == 'true'
        return lead_id in self.muted

    def create_note(self, lead_id, content):
        index = len(self.notes)
        self.calls.append(('note', lead_id))
        if self.fail_note_at is not None and index == self.fail_note_at:
            self.fail_note_at = None
            raise LoftyError('Lofty returned HTTP 500.', status_code=500, ambiguous=True)
        self.notes.append({'noteId': 500 + index, 'leadId': lead_id, 'content': content})
        return str(500 + index)

    def list_notes(self, lead_id):
        self.calls.append(('list_notes', lead_id))
        return [note for note in self.notes if note['leadId'] == lead_id]

    def count(self, name):
        return sum(1 for call in self.calls if call[0] == name)


def enable_flag(brokerage_id, enabled=True):
    session = db.SessionLocal()
    session.add(FeatureFlag(key='lofty_export', brokerage_id=brokerage_id, enabled=enabled))
    session.commit()
    session.close()


@pytest.fixture
def lofty_settings(monkeypatch):
    monkeypatch.setitem(settings['lofty'], 'api_key', API_KEY)
    enable_flag('spacematch')
    return settings['lofty']


@pytest.fixture
def fake_lofty(monkeypatch, lofty_settings):
    fake = FakeLofty()
    monkeypatch.setattr(lofty_export, 'LoftyClient', lambda credentials: fake)
    return fake


@pytest.fixture
def people(make_user):
    broker = make_user(BROKER, role='broker', brokerage_id='spacematch')
    agent = make_user(AGENT, role='agent', brokerage_id='spacematch')
    outsider = make_user(OUTSIDER, role='broker', brokerage_id='othergroup')
    return {'broker': broker, 'agent': agent, 'outsider': outsider}


def make_thread(owner_id, *, name='John Doe', emails=None, contact=CONTACT,
                messages=None, address='123 Main St, Chicago, IL 60601', **conversation_fields):
    session = db.SessionLocal()
    try:
        conversation = Conversation(contact=contact, user_id=owner_id, name=name,
                                    property_address=address, **conversation_fields)
        session.add(conversation)
        session.commit()
        details = json.dumps({'emails': emails}) if emails else None
        session.add(Lead(user_id=owner_id, owner_name=name, phone=contact, property_address=address,
                         details=details, conversation_id=conversation.id))
        start = datetime(2026, 11, 2, 16, 15)
        rows = messages if messages is not None else [
            ('outbound', 'outreach.initial', None, 'Would you consider selling your property?'),
            ('inbound', 'message.received', None, 'Yes, if the offer makes sense.'),
            ('outbound', 'ai.reply', None, 'Would you like to discuss its market value?'),
            ('outbound', 'broker.message', owner_id, 'We can arrange a consultation.'),
        ]
        for index, (direction, event_type, sender, text) in enumerate(rows):
            session.add(Message(conversation_id=conversation.id, direction=direction,
                                event_type=event_type, sender_user_id=sender, text=text,
                                status='delivered' if direction == 'outbound' else 'received',
                                created_at=start + timedelta(minutes=index)))
        session.commit()
        return conversation.id
    finally:
        session.close()


def export_row(conversation_id) -> LoftyExport | None:
    session = db.SessionLocal()
    try:
        row = session.query(LoftyExport).filter_by(conversation_id=conversation_id).first()
        if row is not None:
            session.expunge(row)
        return row
    finally:
        session.close()


def send(client, headers, conversation_id, **body):
    payload = {'summary': 'Seller open to an offer; timeline 30-60 days.', **body}
    return client.post(f'{FOLLOWUPS_URL}/{conversation_id}/lofty', json=payload, headers=headers)


# --------------------------------------------------------------------------
# 1. Authorization and organization isolation
# --------------------------------------------------------------------------

def test_other_brokerage_cannot_preview_or_send(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(OUTSIDER)
    assert client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty', headers=headers).status_code == 404
    assert send(client, headers, conversation_id).status_code == 404
    assert fake_lofty.calls == []


def test_unauthenticated_is_rejected(client, people):
    conversation_id = make_thread(people['broker'].id)
    assert client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty').status_code == 401


def test_brokerage_not_on_allowlist_cannot_use_the_key(client, make_user, auth_header, fake_lofty):
    broker = make_user(OTHER_TENANT_BROKER, role='broker', brokerage_id='unconnected')
    conversation_id = make_thread(broker.id)
    headers = auth_header(OTHER_TENANT_BROKER)
    preview = client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty', headers=headers).json()
    assert preview['available'] is False
    assert 'not enabled for your organization' in preview['unavailable_reason']
    assert send(client, headers, conversation_id).status_code == 403
    assert client.post(f'{FOLLOWUPS_URL}/{conversation_id}/lofty/summary',
                       headers=headers).status_code == 403
    assert fake_lofty.calls == []


def test_global_or_disabled_flag_does_not_open_the_key(client, make_user, auth_header, fake_lofty):
    """Only a flag scoped to the brokerage counts; a global flag is ignored."""
    enable_flag(None)
    enable_flag('unconnected', enabled=False)
    broker = make_user(OTHER_TENANT_BROKER, role='broker', brokerage_id='unconnected')
    conversation_id = make_thread(broker.id)
    assert send(client, auth_header(OTHER_TENANT_BROKER), conversation_id).status_code == 403
    assert fake_lofty.calls == []


def test_unconfigured_key_disables_the_feature(client, people, auth_header, fake_lofty, monkeypatch):
    monkeypatch.setitem(settings['lofty'], 'api_key', '')
    conversation_id = make_thread(people['broker'].id)
    response = send(client, auth_header(BROKER), conversation_id)
    assert response.status_code == 503


def test_agent_only_exports_assigned_leads(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(AGENT)
    assert send(client, headers, conversation_id).status_code == 404
    session = db.SessionLocal()
    session.query(Lead).filter_by(conversation_id=conversation_id).update(
        {'assigned_agent_id': people['agent'].id})
    session.commit()
    session.close()
    assert send(client, headers, conversation_id).json()['status'] == 'completed'


def test_api_key_never_returned(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    preview = client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty', headers=headers)
    result = send(client, headers, conversation_id)
    assert API_KEY not in preview.text and API_KEY not in result.text
    assert API_KEY not in repr(ApiKeyCredentials(API_KEY, 'spacematch'))


def test_opted_out_owner_is_not_sent(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id, dnc_alert=True, lead_status='dnc')
    assert send(client, auth_header(BROKER), conversation_id).status_code == 422
    assert fake_lofty.calls == []


# --------------------------------------------------------------------------
# 2. Preview
# --------------------------------------------------------------------------

def test_preview_fields_and_sender_labels(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id, emails=['john@example.com'])
    preview = client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty',
                         headers=auth_header(BROKER)).json()
    assert preview['name'] == 'John Doe'
    assert preview['property_address'] == '123 Main St, Chicago, IL 60601'
    assert preview['phone'] == CONTACT
    assert preview['emails'] == ['john@example.com']
    assert preview['available'] is True
    assert preview['export'] is None
    assert [(m['sender'], m['text']) for m in preview['messages']] == [
        ('AI', 'Would you consider selling your property?'),
        ('Owner', 'Yes, if the offer makes sense.'),
        ('AI', 'Would you like to discuss its market value?'),
        ('You', 'We can arrange a consultation.'),
    ]
    assert fake_lofty.calls == []  # preview never touches Lofty


def test_sender_label_uses_metadata_not_direction():
    assert lofty_export.sender_label(Message(direction='outbound', event_type='broker.booking')) == 'You'
    assert lofty_export.sender_label(Message(direction='outbound', event_type='message.sent',
                                             sender_user_id=7)) == 'You'
    assert lofty_export.sender_label(Message(direction='outbound', event_type='ai.closing')) == 'AI'
    assert lofty_export.sender_label(Message(direction='inbound', event_type='message.received')) == 'Owner'


# --------------------------------------------------------------------------
# 3. AI summary through the configured adapter
# --------------------------------------------------------------------------

def test_summary_uses_configured_ai_adapter(client, people, auth_header, fake_lofty, monkeypatch):
    captured = {}

    def fake_completion(messages, **options):
        captured['messages'] = messages
        captured['options'] = options
        return {'role': 'assistant', 'content': 'Seller motivation: Not discussed'}

    monkeypatch.setattr('app.lofty.export.ai_client.is_configured', lambda: True)
    monkeypatch.setattr('app.lofty.export.ai_client.completion', fake_completion)
    conversation_id = make_thread(people['broker'].id)
    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/lofty/summary',
                           headers=auth_header(BROKER))
    assert response.status_code == 200, response.text
    assert response.json() == {'summary': 'Seller motivation: Not discussed'}
    assert captured['options']['feature'] == 'followups.lofty_summary'
    prompt = captured['messages'][1]['content']
    assert 'Owner: Yes, if the offer makes sense.' in prompt
    assert 'You: We can arrange a consultation.' in prompt
    assert 'Never invent facts' in captured['messages'][0]['content']
    assert fake_lofty.calls == []


def test_summary_provider_failure_is_recoverable(client, people, auth_header, fake_lofty, monkeypatch):
    from app.ai.client import AiUnavailableError

    def boom(messages, **options):
        raise AiUnavailableError('timeout', category='timeout')

    monkeypatch.setattr('app.lofty.export.ai_client.is_configured', lambda: True)
    monkeypatch.setattr('app.lofty.export.ai_client.completion', boom)
    conversation_id = make_thread(people['broker'].id)
    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/lofty/summary',
                           headers=auth_header(BROKER))
    assert response.status_code == 502
    assert 'could not be generated' in response.json()['detail']


def test_summary_without_ai_configured(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    response = client.post(f'{FOLLOWUPS_URL}/{conversation_id}/lofty/summary',
                           headers=auth_header(BROKER))
    assert response.status_code == 503


def test_send_requires_a_summary(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    response = send(client, auth_header(BROKER), conversation_id, summary='   ')
    assert response.status_code == 422
    assert fake_lofty.calls == []


# --------------------------------------------------------------------------
# 4-8. Lead creation, matching, mute, notes
# --------------------------------------------------------------------------

def test_new_lead_full_flow(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id, emails=['john@example.com'])
    response = send(client, auth_header(BROKER), conversation_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'completed'
    assert body['lead_action'] == 'created'
    assert body['lofty_lead_id'] == '9001'
    assert body['lofty_url'] == 'https://crm.lofty.com/admin/home/lead/detail/9001'
    assert body['mute_verified'] is True
    assert body['notes_saved'] == body['notes_total'] == 1

    create = next(call[1] for call in fake_lofty.calls if call[0] == 'create')
    assert create == {
        'firstName': 'John', 'lastName': 'Doe', 'emails': ['john@example.com'],
        'phones': ['3125550123'], 'leadTypes': [1], 'source': 'ListingIQ',
        'stage': 'Connected Qualifying', 'tagsAdd': ['ListingIQ'],
        'property': {'streetAddress': '123 Main St', 'city': 'Chicago', 'state': 'IL',
                     'zipCode': '60601'},
    }
    assert 'assignedUserId' not in create
    # Order: lead -> tag -> mute -> verify -> notes
    names = [call[0] for call in fake_lofty.calls]
    assert names.index('create') < names.index('update') < names.index('mute') \
        < names.index('mute_status') < names.index('note')

    note = fake_lofty.notes[0]['content']
    assert 'ListingIQ — Qualified Seller' in note
    assert 'Property: 123 Main St, Chicago, IL 60601' in note
    assert 'AI SUMMARY\nSeller open to an offer; timeline 30-60 days.' in note
    assert note.index('] AI:\nWould you consider') < note.index('] Owner:\nYes, if') \
        < note.index('] You:\nWe can arrange')


def test_configured_agent_assignment(client, people, auth_header, fake_lofty, monkeypatch):
    monkeypatch.setitem(settings['lofty'], 'assigned_user_id', '777')
    conversation_id = make_thread(people['broker'].id)
    send(client, auth_header(BROKER), conversation_id)
    create = next(call[1] for call in fake_lofty.calls if call[0] == 'create')
    assert create['assignedUserId'] == 777


def test_existing_lead_is_updated_not_recreated(client, people, auth_header, fake_lofty):
    fake_lofty.existing['3125550123'] = ['4242']
    conversation_id = make_thread(people['broker'].id)
    body = send(client, auth_header(BROKER), conversation_id).json()
    assert body['status'] == 'completed'
    assert body['lead_action'] == 'existing'
    assert body['lofty_lead_id'] == '4242'
    assert fake_lofty.count('create') == 0
    update = next(call for call in fake_lofty.calls if call[0] == 'update')
    # Only the tag is added; owner, source and stage are left alone.
    assert update == ('update', '4242', {'tagsAdd': ['ListingIQ']})


def test_merged_post_still_gets_tag(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    send(client, auth_header(BROKER), conversation_id)
    assert ('update', '9001', {'tagsAdd': ['ListingIQ']}) in fake_lofty.calls


def test_ambiguous_match_stops_and_can_be_resolved(client, people, auth_header, fake_lofty):
    fake_lofty.existing['3125550123'] = ['111']
    fake_lofty.existing['john@example.com'] = ['222']
    conversation_id = make_thread(people['broker'].id, emails=['john@example.com'])
    headers = auth_header(BROKER)
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'needs_review'
    assert sorted(body['candidate_lead_ids']) == ['111', '222']
    assert fake_lofty.count('create') == 0 and fake_lofty.count('mute') == 0

    # A lead id outside the matches is refused.
    assert send(client, headers, conversation_id, lofty_lead_id='999').json()['status'] == 'needs_review'
    body = send(client, headers, conversation_id, lofty_lead_id='222').json()
    assert body['status'] == 'completed'
    assert body['lofty_lead_id'] == '222'
    assert fake_lofty.count('create') == 0


def test_phone_only_lead_omits_email(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    send(client, auth_header(BROKER), conversation_id)
    create = next(call[1] for call in fake_lofty.calls if call[0] == 'create')
    assert 'emails' not in create
    # Lofty is searched by phone before anything is created.
    assert ('find', '3125550123') in fake_lofty.calls
    note = fake_lofty.notes[0]['content']
    assert 'Email:' not in note and 'Mobile: +13125550123' in note


def test_search_failure_never_creates_blind(client, people, auth_header, fake_lofty):
    fake_lofty.fail['find'] = LoftyError('Lofty returned HTTP 500.', status_code=500)
    conversation_id = make_thread(people['broker'].id)
    body = send(client, auth_header(BROKER), conversation_id).json()
    assert body['status'] == 'failed' and body['step'] == 'lead'
    assert fake_lofty.count('create') == 0


def test_missing_name_is_actionable(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id, name=None)
    body = send(client, auth_header(BROKER), conversation_id).json()
    assert body['status'] == 'failed'
    assert 'first name' in body['last_error']
    assert fake_lofty.count('create') == 0


def test_mute_must_verify_before_success(client, people, auth_header, fake_lofty):
    fake_lofty.mute_reports = 'false'
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'failed' and body['step'] == 'mute'
    assert body['mute_verified'] is False
    assert fake_lofty.count('note') == 0

    fake_lofty.mute_reports = None
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'completed'
    assert fake_lofty.count('create') == 1  # the lead was not created again


# --------------------------------------------------------------------------
# Real HTTP client: headers, mute endpoints, response wrappers
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=None, *, text=None, content_type='application/json',
                 request_headers=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)
        self.content = self.text.encode()
        self.headers = {'content-type': content_type}
        self.request = type('Sent', (), {'headers': request_headers or {}})()

    def json(self):
        return self._body


def test_http_client_mute_request_and_headers(monkeypatch):
    sent = []

    def fake_request(method, url, **kwargs):
        sent.append((method, url, kwargs))
        if url.endswith('/mute'):
            return FakeResponse(200, {})
        return FakeResponse(200, {'data': {'mute': 'true'}})

    monkeypatch.setattr(lofty_client_module.requests, 'request', fake_request)
    client = LoftyClient(ApiKeyCredentials(API_KEY, 'spacematch'), base_url='https://api.lofty.com')
    client.mute_sales_agent('123')
    assert client.sales_agent_muted('123') is True

    method, url, kwargs = sent[0]
    assert (method, url) == ('PUT', 'https://api.lofty.com/v2.0/sales-agents/working-lead/123/mute')
    assert kwargs['headers']['Authorization'] == f'token {API_KEY}'
    assert kwargs['headers']['Content-Type'] == 'application/json'
    assert kwargs['json'] is None
    method, url, kwargs = sent[1]
    assert url == 'https://api.lofty.com/v2.0/sales-agent/lead/mute-status'
    assert kwargs['params'] == {'leadId': '123'}


def test_http_client_sends_listingiq_user_agent(monkeypatch):
    """Lofty's firewall 403s the default python-requests User-Agent."""
    sent = []
    monkeypatch.setattr(lofty_client_module.requests, 'request',
                        lambda method, url, **kwargs: sent.append(kwargs) or FakeResponse(200, {'leads': []}))
    LoftyClient(ApiKeyCredentials(API_KEY, 'x'), base_url='https://api.lofty.com').find_leads(
        phone='3125550123')
    assert sent[0]['headers']['User-Agent'] == lofty_client_module.USER_AGENT
    assert not sent[0]['headers']['User-Agent'].startswith('python-requests')
    assert sent[0]['params'] == {'limit': 10, 'phone': '3125550123'}


def test_firewall_403_is_readable_and_diagnostics_are_sanitized(monkeypatch):
    html = '<html><head><title>OpsAny Web Firewall</title></head><body>blocked</body></html>'
    events = []
    monkeypatch.setattr(lofty_client_module, 'log_event',
                        lambda _logger, name, **fields: events.append((name, fields)))
    monkeypatch.setattr(
        lofty_client_module.requests, 'request',
        lambda method, url, **kwargs: FakeResponse(
            403, text=html, content_type='text/html',
            request_headers={**kwargs['headers'], 'Authorization': kwargs['headers']['Authorization']}))
    client = LoftyClient(ApiKeyCredentials(API_KEY, 'x'), base_url='https://api.lofty.com')
    with pytest.raises(LoftyError) as error:
        client.find_leads(phone='3125550123')
    assert error.value.status_code == 403
    assert 'firewall rejected' in str(error.value) and '<html' not in str(error.value)
    failed = [fields for name, fields in events if name == 'lofty.request.failed']
    assert failed and failed[0]['param_names'] == ['limit', 'phone']
    assert failed[0]['authorization_scheme'] == 'token'
    dumped = json.dumps(failed)
    assert API_KEY not in dumped and '3125550123' not in dumped


@pytest.mark.parametrize('body, expected', [
    ({'data': {'mute': 'true'}}, True),
    ({'mute': 'true'}, True),
    ({'mute': True}, True),
    ({'data': {'mute': 'false'}}, False),
    ({}, False),
])
def test_mute_status_wrappers(monkeypatch, body, expected):
    monkeypatch.setattr(lofty_client_module.requests, 'request',
                        lambda method, url, **kwargs: FakeResponse(200, body))
    client = LoftyClient(ApiKeyCredentials(API_KEY, 'x'), base_url='https://api.lofty.com')
    assert client.sales_agent_muted('1') is expected


def test_http_client_write_timeout_is_ambiguous_and_not_retried(monkeypatch):
    import requests

    calls = []

    def timeout(method, url, **kwargs):
        calls.append(method)
        raise requests.Timeout('slow')

    monkeypatch.setattr(lofty_client_module.requests, 'request', timeout)
    client = LoftyClient(ApiKeyCredentials(API_KEY, 'x'), base_url='https://api.lofty.com')
    with pytest.raises(LoftyError) as error:
        client.create_note('1', 'hello')
    assert error.value.ambiguous is True
    assert calls == ['POST']
    assert API_KEY not in str(error.value)


def test_http_client_parses_lead_search_and_create(monkeypatch):
    def fake_request(method, url, **kwargs):
        if method == 'GET':
            return FakeResponse(200, {'leads': [{'leadId': 11}, {'leadId': 11}, {'leadId': 12}]})
        return FakeResponse(200, {'leadId': 651095960136641})

    monkeypatch.setattr(lofty_client_module.requests, 'request', fake_request)
    client = LoftyClient(ApiKeyCredentials(API_KEY, 'x'), base_url='https://api.lofty.com')
    assert client.find_leads(phone='3125550123') == ['11', '12']
    assert client.create_lead({'firstName': 'A'}) == '651095960136641'


# --------------------------------------------------------------------------
# 9. Long conversations are split without loss
# --------------------------------------------------------------------------

def test_long_conversation_split_into_numbered_notes(client, people, auth_header, fake_lofty):
    texts = [f'Message {index}: ' + ('word ' * 60).strip() for index in range(60)]
    texts.append('X' * 4500)  # one message longer than a whole note
    rows = [('inbound' if index % 2 else 'outbound', 'message.received' if index % 2 else 'ai.reply',
             None, text) for index, text in enumerate(texts)]
    conversation_id = make_thread(people['broker'].id, messages=rows)
    body = send(client, auth_header(BROKER), conversation_id).json()
    assert body['status'] == 'completed'
    total = body['notes_total']
    assert total > 3 and body['notes_saved'] == total
    contents = [note['content'] for note in fake_lofty.notes]
    assert all(len(content) <= 1900 for content in contents)
    for index, content in enumerate(contents, start=1):
        assert f'Part {index} of {total}' in content
        assert f'Ref: ListingIQ-{conversation_id}-r1 · Part {index}/{total}' in content
    combined = ''.join(contents)
    position = -1
    for text in texts[:-1]:
        found = combined.find(text)
        assert found > position, text[:20]  # present and in order
        position = found
    assert combined.count('X') >= 4500  # the oversized message is complete


def test_build_note_parts_respects_limit_and_keeps_everything():
    from zoneinfo import ZoneInfo
    messages = [Message(direction='inbound', text=('abc ' * 400) + str(index),
                        created_at=datetime(2026, 1, 1, 12, index)) for index in range(5)]
    parts = lofty_export.build_note_parts(
        {'name': 'A B', 'property_address': '1 St', 'phone': None, 'emails': []},
        'summary', messages, reference='ListingIQ-1-r1', zone=ZoneInfo('UTC'), max_chars=600)
    assert all(len(part) <= 600 for part in parts)
    body = ' '.join(parts)
    assert body.count('abc') == 2000


# --------------------------------------------------------------------------
# 10. Repeated and concurrent sends
# --------------------------------------------------------------------------

def test_repeat_send_after_success_does_nothing_new(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    first = send(client, headers, conversation_id).json()
    calls = len(fake_lofty.calls)
    second = send(client, headers, conversation_id).json()
    assert second['status'] == 'completed' and second['lofty_lead_id'] == first['lofty_lead_id']
    assert len(fake_lofty.calls) == calls


def test_concurrent_send_is_refused(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    session = db.SessionLocal()
    session.add(LoftyExport(conversation_id=conversation_id, status='in_progress', step='lead',
                            claimed_at=datetime.utcnow(), revision=1, attempts=1))
    session.commit()
    session.close()
    response = send(client, auth_header(BROKER), conversation_id)
    assert response.status_code == 409
    assert fake_lofty.calls == []


def test_stale_claim_can_be_taken_over(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    session = db.SessionLocal()
    session.add(LoftyExport(conversation_id=conversation_id, status='in_progress', step='lead',
                            claimed_at=datetime.utcnow() - timedelta(minutes=10),
                            revision=1, attempts=1))
    session.commit()
    session.close()
    assert send(client, auth_header(BROKER), conversation_id).json()['status'] == 'completed'


def test_parallel_requests_create_one_lead(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    gate = threading.Barrier(3)
    original = fake_lofty.find_leads

    def slow_find(**kwargs):
        try:
            gate.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return original(**kwargs)

    fake_lofty.find_leads = slow_find
    results = []

    def worker():
        results.append(send(client, headers, conversation_id).status_code)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert fake_lofty.count('create') == 1
    assert results.count(200) >= 1
    assert set(results) <= {200, 409}


# --------------------------------------------------------------------------
# 11. Partial failures, retries and recovery
# --------------------------------------------------------------------------

def test_note_failure_resumes_without_new_lead_or_duplicate_notes(client, people, auth_header, fake_lofty):
    rows = [('outbound', 'ai.reply', None, f'Line {index} ' + 'text ' * 80) for index in range(30)]
    conversation_id = make_thread(people['broker'].id, messages=rows)
    headers = auth_header(BROKER)
    fake_lofty.fail_note_at = 1
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'failed' and body['step'] == 'notes'
    assert body['notes_saved'] == 1 and body['lofty_lead_id'] == '9001'

    body = send(client, headers, conversation_id, summary='edited later').json()
    assert body['status'] == 'completed'
    assert body['notes_saved'] == body['notes_total'] == len(fake_lofty.notes)
    assert fake_lofty.count('create') == 1
    assert fake_lofty.count('mute') == 1  # mute was already verified
    contents = [note['content'] for note in fake_lofty.notes]
    assert len(set(contents)) == len(contents)
    # The summary frozen with the first notes attempt is kept.
    assert 'edited later' not in ''.join(contents)


def test_note_saved_without_response_is_not_duplicated(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    original = fake_lofty.create_note

    def saved_then_timeout(lead_id, content):
        original(lead_id, content)
        raise LoftyError('Lofty could not be reached (Timeout).', ambiguous=True)

    fake_lofty.create_note = saved_then_timeout
    assert send(client, headers, conversation_id).json()['status'] == 'failed'
    fake_lofty.create_note = original
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'completed'
    assert len(fake_lofty.notes) == 1
    assert fake_lofty.count('list_notes') == 1


def test_lead_create_timeout_recovers_by_search(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    original = fake_lofty.create_lead

    def created_then_timeout(payload):
        original(payload)
        raise LoftyError('Lofty could not be reached (Timeout).', ambiguous=True)

    fake_lofty.create_lead = created_then_timeout
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'failed' and body['lofty_lead_id'] is None
    fake_lofty.create_lead = original
    body = send(client, headers, conversation_id).json()
    assert body['status'] == 'completed'
    assert body['lofty_lead_id'] == '9001' and body['lead_action'] == 'existing'
    assert fake_lofty.next_lead_id == 9002  # only one Lofty lead ever created


def test_resync_adds_new_note_revision_to_same_lead(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    send(client, headers, conversation_id)
    body = send(client, headers, conversation_id, resync=True, summary='Updated summary').json()
    assert body['status'] == 'completed' and body['revision'] == 2
    assert fake_lofty.count('create') == 1
    assert f'ListingIQ-{conversation_id}-r2' in fake_lofty.notes[-1]['content']
    assert 'Updated summary' in fake_lofty.notes[-1]['content']


def test_preview_reports_existing_export(client, people, auth_header, fake_lofty):
    conversation_id = make_thread(people['broker'].id)
    headers = auth_header(BROKER)
    send(client, headers, conversation_id)
    preview = client.get(f'{FOLLOWUPS_URL}/{conversation_id}/lofty', headers=headers).json()
    assert preview['export']['status'] == 'completed'
    assert preview['export']['summary'] == 'Seller open to an offer; timeline 30-60 days.'
    assert preview['export']['lofty_url'].endswith('/9001')


# --------------------------------------------------------------------------
# 12. Existing conversation behaviour is untouched
# --------------------------------------------------------------------------

def test_export_does_not_change_conversation_or_send_sms(client, people, auth_header, fake_lofty, monkeypatch):
    outbox = []
    monkeypatch.setattr('app.sms.service.send_sms', lambda payload: outbox.append(payload))
    conversation_id = make_thread(people['broker'].id)
    session = db.SessionLocal()
    before = session.get(Conversation, conversation_id)
    snapshot = (before.handled_by, before.ai_enabled, before.lead_status, before.followup_state,
                before.meeting_booked)
    count = session.query(Message).filter_by(conversation_id=conversation_id).count()
    session.close()

    send(client, auth_header(BROKER), conversation_id)

    session = db.SessionLocal()
    after = session.get(Conversation, conversation_id)
    assert (after.handled_by, after.ai_enabled, after.lead_status, after.followup_state,
            after.meeting_booked) == snapshot
    assert session.query(Message).filter_by(conversation_id=conversation_id).count() == count
    session.close()
    assert outbox == []
    followup = client.get(f'{FOLLOWUPS_URL}/{conversation_id}', headers=auth_header(BROKER))
    assert followup.status_code == 200
