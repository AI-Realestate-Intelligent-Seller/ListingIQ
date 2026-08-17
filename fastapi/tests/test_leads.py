"""Tests for the lead pool: imports, derived stage, filters, campaigns, deletes."""

import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import db
from app.leads import service as leads_service
from app.leads.catalog import parse_signals
from app.leads.importer import normalize_phone, parse_csv, score_lead
from app.models import Conversation, Lead, Message

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
LEADS_URL = '/api/v1/leads'

CSV = (
    'Owner Name,Phone,Property Address,City,Signals\n'
    'Marcus Webb,(312) 555-0142,4517 W Adams St,Austin,"Absentee Owner, High Equity"\n'
    'Priya Nguyen,+13125550188,2210 S Sacramento Dr,Little Village,Pre-Foreclosure\n'
    'Emeka Obi,3125550199,7745 S Coles Ave,South Shore,Probate\n'
)


@pytest.fixture
def sent_sms(monkeypatch):
    outbox = []

    def fake_send(payload):
        outbox.append(payload)
        return {'data': {'id': f'sim-{len(outbox)}'}}

    monkeypatch.setattr('app.sms.service.send_sms', fake_send)
    return outbox


def upload(client, headers, text=CSV, filename='leads.csv'):
    return client.post(
        f'{LEADS_URL}/import',
        headers=headers,
        files={'file': (filename, text.encode('utf-8'), 'text/csv')},
    )


# -- parsing ---------------------------------------------------------------


@pytest.mark.parametrize('raw,expected', [
    ('(312) 555-0142', '+13125550142'),
    ('3125550142', '+13125550142'),
    ('13125550142', '+13125550142'),
    ('+13125550142', '+13125550142'),
    ('555-0142', None),
    ('', None),
    ('not a phone', None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize('raw,expected', [
    ('FSBO', ['fsbo']),
    ('for sale by owner; high equity', ['fsbo', 'high_equity']),
    ('Pre-Foreclosure', ['pre_foreclosure']),
    ('Tax Delinquent, probate', ['tax_delinquent', 'probate']),
    ('unrelated nonsense', []),
])
def test_parse_signals(raw, expected):
    assert parse_signals(raw) == expected


def test_parse_csv_reads_aliased_headers_and_flag_columns():
    text = (
        'first_name,last_name,mobile,address,neighborhood,fsbo,tax_delinquent\n'
        'Grace,Lindqvist,312-555-0170,3312 W Fillmore St,North Lawndale,yes,1\n'
    )
    rows, warnings, _meta = parse_csv(text.encode('utf-8'))
    assert warnings == []
    assert rows[0]['owner_name'] == 'Grace Lindqvist'
    assert rows[0]['phone'] == '+13125550170'
    assert rows[0]['area'] == 'North Lawndale'
    assert set(rows[0]['signals']) == {'fsbo', 'tax_delinquent'}


def test_parse_csv_skips_rows_without_phone_or_address():
    rows, warnings, _meta = parse_csv(b'owner,phone,address\nNo Contact,,\n')
    assert rows == []
    assert any('Row 2' in warning for warning in warnings)


def test_parse_csv_rejects_a_file_with_no_recognisable_columns():
    rows, warnings, _meta = parse_csv(b'colour,shape\nred,round\n')
    assert rows == []
    assert 'phone number' in warnings[0]


def test_score_rewards_signals_and_contactability():
    assert score_lead(['pre_foreclosure'], True, True) == 50
    assert score_lead([], False, False) == 0
    assert score_lead(['fsbo'] * 20, True, True) <= 100


# -- import ----------------------------------------------------------------




def test_import_rejects_an_unsupported_file_type(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = upload(client, auth_header(BROKER_EMAIL), 'x', filename='leads.pdf')
    assert response.status_code == 400
    assert response.json()['detail'] == 'Upload a .csv or .xlsx file.'


def test_import_reads_an_xlsx_workbook(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Owner Name', 'Phone', 'Property Address', 'City', 'Signals'])
    # A phone typed into Excel arrives as a number, not a string.
    sheet.append(['Marcus Webb', 3125550142, '4517 W Adams St', 'Austin', 'FSBO'])
    sheet.append([None, None, None, None, None])
    sheet.append(['Ruth Callahan', '+13125550155', '1839 N Springfield Ave', 'Logan Square', 'Vacant'])
    buffer = io.BytesIO()
    workbook.save(buffer)

    response = client.post(
        f'{LEADS_URL}/import',
        headers=headers,
        files={'file': ('leads.xlsx', buffer.getvalue(),
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert response.status_code == 200, response.text
    assert response.json()['created'] == 2

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    by_name = {item['owner_name']: item for item in leads}
    assert by_name['Marcus Webb']['phone'] == '+13125550142'
    assert [s['label'] for s in by_name['Ruth Callahan']['signals']] == ['Vacant Property']


def test_a_corrupt_workbook_is_reported_not_raised(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(
        f'{LEADS_URL}/import',
        headers=auth_header(BROKER_EMAIL),
        files={'file': ('leads.xlsx', b'this is not a workbook', 'application/octet-stream')},
    )
    assert response.status_code == 400
    assert 'workbook' in response.json()['detail'].lower()


def test_import_requires_authentication(client):
    assert upload(client, {}).status_code == 401


def test_a_broker_never_sees_another_brokers_leads(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    upload(client, auth_header(BROKER_EMAIL))

    response = client.get(LEADS_URL, headers=auth_header('other.broker@linchpinglobal.net'))
    assert response.status_code == 200
    assert response.json()['leads'] == []


# -- stage, search, filters ------------------------------------------------


def test_freshly_imported_leads_read_as_new_then_become_ready(client, make_user, auth_header, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    stages = {item['stage'] for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert stages == {'new'}

    # Age every lead past the freshness window.
    for lead in session.query(Lead).all():
        lead.refreshed_at = datetime.utcnow() - timedelta(days=2)
        lead.created_at = datetime.utcnow() - timedelta(days=2)
    session.commit()

    stages = {item['stage'] for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert stages == {'ready'}


def test_a_lead_without_a_phone_needs_review(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, 'owner,phone,address\nNo Number,,4517 W Adams St\n')

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    assert [item['stage'] for item in leads] == ['needs_review']


def test_search_matches_owner_and_address(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    by_owner = client.get(LEADS_URL, headers=headers, params={'search': 'priya'}).json()['leads']
    assert [item['owner_name'] for item in by_owner] == ['Priya Nguyen']

    by_address = client.get(LEADS_URL, headers=headers, params={'search': 'Coles'}).json()['leads']
    assert [item['owner_name'] for item in by_address] == ['Emeka Obi']


def test_signal_filter_requires_every_selected_signal(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    one = client.get(LEADS_URL, headers=headers, params={'signal': ['absentee_owner']}).json()['leads']
    assert [item['owner_name'] for item in one] == ['Marcus Webb']

    both = client.get(
        LEADS_URL, headers=headers,
        params={'signal': ['absentee_owner', 'probate']},
    ).json()['leads']
    assert both == []


def test_facets_count_the_whole_pool(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    facets = client.get(LEADS_URL, headers=headers).json()['facets']
    assert facets['total'] == 3
    assert facets['signals']['high_equity'] == 1
    assert facets['stages']['new'] == 3


# -- campaigns -------------------------------------------------------------


def test_campaign_starts_bobbie_and_moves_the_lead_into_the_sms_tab(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    target = next(item for item in leads if item['owner_name'] == 'Priya Nguyen')

    response = client.post(f'{LEADS_URL}/campaign', headers=headers,
                           json={'lead_ids': [target['id']]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['skipped'] == []
    assert len(body['started']) == 1
    # The reason comes from the lead's own signal, not from thin air.
    assert 'pre-foreclosure' in body['started'][0]['text'].lower()
    assert len(sent_sms) == 1

    conversations = client.get('/api/v1/sms/conversations', headers=headers).json()
    assert [item['contact'] for item in conversations] == ['+13125550188']
    assert conversations[0]['handled_by'] == 'bobbie'

    refreshed = client.get(LEADS_URL, headers=headers).json()['leads']
    campaigned = next(item for item in refreshed if item['id'] == target['id'])
    assert campaigned['stage'] == 'in_campaign'
    assert campaigned['last_activity_at'] is not None


def test_campaign_skips_leads_it_cannot_text_but_starts_the_rest(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, CSV + 'No Number,,900 W Nowhere Ave,Austin,FSBO\n')

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    response = client.post(f'{LEADS_URL}/campaign', headers=headers,
                           json={'lead_ids': [item['id'] for item in leads]})
    body = response.json()
    assert len(body['started']) == 3
    assert [item['reason'] for item in body['skipped']] == ['No usable phone number.']


def test_campaign_never_texts_a_dnc_lead(client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead = session.query(Lead).first()
    lead.dnc = True
    session.commit()

    response = client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead.id]})
    assert response.json()['started'] == []
    assert 'do-not-contact' in response.json()['skipped'][0]['reason']
    assert sent_sms == []

    stages = {item['id']: item['stage'] for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert stages[lead.id] == 'dnc'


def test_a_lead_cannot_be_campaigned_twice(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']

    client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})
    again = client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})
    assert again.json()['started'] == []
    assert again.json()['skipped'][0]['reason'] == 'Already in a campaign.'
    assert len(sent_sms) == 1


def test_campaign_rejects_another_brokers_lead(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    upload(client, auth_header(BROKER_EMAIL))
    lead_id = client.get(LEADS_URL, headers=auth_header(BROKER_EMAIL)).json()['leads'][0]['id']

    response = client.post(f'{LEADS_URL}/campaign',
                           headers=auth_header('other.broker@linchpinglobal.net'),
                           json={'lead_ids': [lead_id]})
    assert response.json()['started'] == []
    assert response.json()['skipped'][0]['reason'] == 'Lead not found.'
    assert sent_sms == []


def test_last_activity_follows_the_conversation(client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']
    client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})

    lead = session.query(Lead).filter(Lead.id == lead_id).first()
    later = datetime.utcnow() + timedelta(hours=3)
    session.add(Message(conversation_id=lead.conversation_id, direction='inbound',
                        text='Who is this?', created_at=later))
    session.commit()

    refreshed = client.get(LEADS_URL, headers=headers).json()['leads']
    item = next(row for row in refreshed if row['id'] == lead_id)
    assert item['last_activity_at'].startswith(later.isoformat()[:16])


def test_deleting_the_conversation_returns_the_lead_to_the_pool(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']
    client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})

    lead = session.query(Lead).filter(Lead.id == lead_id).first()
    client.delete(f'/api/v1/sms/conversations/{lead.conversation_id}', headers=headers)

    item = next(row for row in client.get(LEADS_URL, headers=headers).json()['leads']
                if row['id'] == lead_id)
    assert item['conversation_id'] is None
    assert item['stage'] in {'new', 'ready'}


def test_delete_removes_only_the_owners_lead(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']

    other = client.delete(f'{LEADS_URL}/{lead_id}',
                          headers=auth_header('other.broker@linchpinglobal.net'))
    assert other.status_code == 404
    assert client.delete(f'{LEADS_URL}/{lead_id}', headers=headers).status_code == 200


# -- bulk delete -----------------------------------------------------------


def test_bulk_delete_removes_the_selection(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]

    response = client.post(f'{LEADS_URL}/delete', headers=headers, json={'lead_ids': ids[:2]})
    assert response.status_code == 200
    assert response.json() == {'deleted': 2}

    remaining = client.get(LEADS_URL, headers=headers).json()
    assert [item['id'] for item in remaining['leads']] == ids[2:]
    assert remaining['facets']['total'] == 1


def test_bulk_delete_ignores_another_brokers_leads(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]

    response = client.post(f'{LEADS_URL}/delete',
                           headers=auth_header('other.broker@linchpinglobal.net'),
                           json={'lead_ids': ids})
    assert response.status_code == 404
    assert len(client.get(LEADS_URL, headers=headers).json()['leads']) == 3


def test_bulk_delete_rejects_an_empty_selection(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(f'{LEADS_URL}/delete', headers=auth_header(BROKER_EMAIL),
                           json={'lead_ids': []})
    assert response.status_code == 422


def test_deleting_a_campaigned_lead_keeps_the_conversation(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']
    client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})

    client.post(f'{LEADS_URL}/delete', headers=headers, json={'lead_ids': [lead_id]})
    # The pool entry is gone, but the thread Bobbie opened is a record of contact.
    assert len(client.get('/api/v1/sms/conversations', headers=headers).json()) == 1


def test_bulk_delete_handles_a_pool_larger_than_the_bind_parameter_limit(
        client, make_user, auth_header, session):
    """Selecting every row of a big pool must go through in one request."""
    user = make_user(BROKER_EMAIL, role='broker')
    session.bulk_save_objects([
        Lead(user_id=user.id, owner_name=f'Owner {n}', phone=f'+1312555{n:04d}',
             property_address=f'{n} Test Ave', signals='fsbo', score=36)
        for n in range(1200)
    ])
    session.commit()
    headers = auth_header(BROKER_EMAIL)

    ids = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]
    assert len(ids) == 1200

    response = client.post(f'{LEADS_URL}/delete', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted': 1200}
    assert client.get(LEADS_URL, headers=headers).json()['facets']['total'] == 0


def test_an_oversized_campaign_explains_the_limit(client, make_user, auth_header, session):
    user = make_user(BROKER_EMAIL, role='broker')
    session.bulk_save_objects([
        Lead(user_id=user.id, owner_name=f'Owner {n}', phone=f'+1312555{n:04d}',
             property_address=f'{n} Test Ave', signals='fsbo', score=36)
        for n in range(250)
    ])
    session.commit()
    headers = auth_header(BROKER_EMAIL)
    ids = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]

    response = client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 400
    assert response.json()['detail'] == 'A campaign can hold at most 200 leads.'


# -- detail panel ----------------------------------------------------------


def test_detail_explains_the_score_and_has_no_conversation_yet(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = next(item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']
                   if item['owner_name'] == 'Priya Nguyen')

    body = client.get(f'{LEADS_URL}/{lead_id}', headers=headers).json()
    assert body['owner_name'] == 'Priya Nguyen'
    assert body['conversation'] is None
    assert body['score_breakdown'] == [
        {'label': 'Pre-Foreclosure', 'points': 20},
        {'label': 'Phone number on file', 'points': 20},
        {'label': 'Property address on file', 'points': 10},
    ]
    assert sum(part['points'] for part in body['score_breakdown']) == body['score']


def test_detail_carries_the_conversation_once_campaigned(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']
    client.post(f'{LEADS_URL}/campaign', headers=headers, json={'lead_ids': [lead_id]})

    body = client.get(f'{LEADS_URL}/{lead_id}', headers=headers).json()
    assert body['conversation']['handled_by'] == 'bobbie'
    assert body['conversation']['message_count'] == 1
    assert body['conversation']['latest_message'].startswith('Hey')


def test_detail_is_scoped_to_the_owning_broker(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']

    other = client.get(f'{LEADS_URL}/{lead_id}',
                       headers=auth_header('other.broker@linchpinglobal.net'))
    assert other.status_code == 404


def test_bulk_delete_still_routes_past_the_detail_path(client, make_user, auth_header):
    """`/leads/delete` must not be swallowed by `/leads/{lead_id}`."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]

    response = client.post(f'{LEADS_URL}/delete', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 200
    assert response.json()['deleted'] == 3


# -- preview, row limit, duplicate policy ----------------------------------

# The same property twice (one row per signal) plus a second distinct lead.
DUPLICATE_CSV = (
    'Owner Name,Phone,Property Address,City,Signals\n'
    'Marcus Webb,(312) 555-0142,4517 W Adams St,Austin,Absentee Owner\n'
    'Marcus Webb,3125550142,4517 W Adams St,Austin,High Equity\n'
    'Priya Nguyen,+13125550188,2210 S Sacramento Dr,Little Village,Pre-Foreclosure\n'
)


def preview(client, headers, text=CSV, filename='leads.csv'):
    return client.post(
        f'{LEADS_URL}/preview',
        headers=headers,
        files={'file': (filename, text.encode('utf-8'), 'text/csv')},
    )


def test_preview_counts_without_importing_anything(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    body = preview(client, headers).json()
    assert body['total_rows'] == 3
    assert body['importable'] == 3
    assert len(body['sample']) == 3
    # Nothing was written.
    assert client.get(LEADS_URL, headers=headers).json()['facets']['total'] == 0





def test_limit_takes_only_the_first_n_leads(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    response = client.post(
        f'{LEADS_URL}/import',
        headers=headers,
        files={'file': ('leads.csv', CSV.encode('utf-8'), 'text/csv')},
        data={'limit': '2'},
    )
    assert response.json()['created'] == 2
    names = [item['owner_name'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]
    assert set(names) == {'Marcus Webb', 'Priya Nguyen'}



def test_a_negative_limit_is_rejected(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(
        f'{LEADS_URL}/import',
        headers=auth_header(BROKER_EMAIL),
        files={'file': ('leads.csv', CSV.encode('utf-8'), 'text/csv')},
        data={'limit': '-5'},
    )
    assert response.status_code == 400


def test_preview_rejects_a_file_with_nothing_importable(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = preview(client, auth_header(BROKER_EMAIL), 'colour,shape\nred,round\n')
    assert response.status_code == 400
    assert 'phone' in response.json()['detail']


# -- large uploads and staged imports --------------------------------------


def test_preview_returns_a_token_the_import_can_reuse(client, make_user, auth_header):
    """A large file is uploaded once, previewed, then imported by token."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    token = preview(client, headers).json()['token']
    response = client.post(f'{LEADS_URL}/import', headers=headers,
                           data={'token': token, 'limit': '2'})
    assert response.status_code == 200, response.text
    assert response.json()['created'] == 2

    # The staged file is consumed, so the same token cannot import twice.
    again = client.post(f'{LEADS_URL}/import', headers=headers, data={'token': token})
    assert again.status_code == 410


def test_another_brokers_token_is_not_accepted(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker')
    token = preview(client, auth_header(BROKER_EMAIL)).json()['token']

    response = client.post(f'{LEADS_URL}/import',
                           headers=auth_header('other.broker@linchpinglobal.net'),
                           data={'token': token})
    assert response.status_code == 410


@pytest.mark.parametrize('token', ['../../etc/passwd', '1-../secret', 'nonsense'])
def test_a_forged_token_cannot_reach_another_path(client, make_user, auth_header, token):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(f'{LEADS_URL}/import', headers=auth_header(BROKER_EMAIL),
                           data={'token': token})
    assert response.status_code == 410


def test_import_without_a_file_or_token_is_rejected(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(f'{LEADS_URL}/import', headers=auth_header(BROKER_EMAIL), data={})
    assert response.status_code == 400


def test_an_oversized_upload_is_refused_with_the_configured_limit(
        client, make_user, auth_header, monkeypatch):
    make_user(BROKER_EMAIL, role='broker')
    # Pretend the ceiling is tiny rather than generating a 50 MB file.
    monkeypatch.setattr('app.leads.uploads.max_bytes', lambda: 1024)

    big = 'owner,phone,address\n' + ''.join(
        f'Owner {n},312555{n:04d},{n} Test Ave\n' for n in range(200))
    response = upload(client, auth_header(BROKER_EMAIL), big)
    assert response.status_code == 413
    assert 'larger than' in response.json()['detail']


def test_a_rejected_upload_leaves_nothing_staged(client, make_user, auth_header, monkeypatch):
    from app.leads import uploads as uploads_module

    make_user(BROKER_EMAIL, role='broker')
    monkeypatch.setattr('app.leads.uploads.max_bytes', lambda: 1024)
    before = set(uploads_module.staging_dir().glob('*'))

    big = 'owner,phone,address\n' + ''.join(
        f'Owner {n},312555{n:04d},{n} Test Ave\n' for n in range(200))
    upload(client, auth_header(BROKER_EMAIL), big)

    assert set(uploads_module.staging_dir().glob('*')) == before


def test_a_five_megabyte_file_imports(client, make_user, auth_header):
    """The old 5 MB ceiling used to reject this outright."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    rows = ''.join(
        f'Owner {n},+1312{n:07d},{n} Long Property Address Avenue Suite 400,Austin,fsbo\n'
        for n in range(70_000)
    )
    text = 'owner,phone,address,city,signals\n' + rows
    assert len(text.encode('utf-8')) > 5 * 1024 * 1024

    response = upload(client, headers, text)
    assert response.status_code == 200, response.text
    assert response.json()['created'] == 70_000






# -- column mapping --------------------------------------------------------

# A match table: the phone belongs to the matched party, not the owner, and the
# property address is under a name no alias would guess.
MATCH_CSV = (
    'row_id,owner_name,search_address,lobbyist_name,phone,email\n'
    '1,Marcus Webb,4517 W Adams St,Acme Advocacy,3125550142,a@x.com\n'
    '2,Priya Nguyen,2210 S Sacramento Dr,Acme Advocacy,3125550142,a@x.com\n'
    '3,Emeka Obi,7745 S Coles Ave,Acme Advocacy,3125550142,a@x.com\n'
)


def test_preview_reports_which_columns_it_used(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    body = preview(client, auth_header(BROKER_EMAIL), MATCH_CSV).json()

    assert body['mapping']['owner_name'] == 'owner_name'
    assert body['mapping']['phone'] == 'phone'
    # Nothing in this file looks like a property address to the guesser.
    assert body['mapping']['property_address'] is None
    assert 'search_address' in body['columns']



def test_import_honours_the_chosen_mapping(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    token = preview(client, headers, MATCH_CSV).json()['token']

    response = client.post(
        f'{LEADS_URL}/import', headers=headers,
        data={'token': token,
              'mapping': json.dumps({'phone': '', 'property_address': 'search_address'})},
    )
    assert response.json()['created'] == 3

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    assert sorted(item['property_address'] for item in leads) == [
        '2210 S Sacramento Dr', '4517 W Adams St', '7745 S Coles Ave',
    ]
    # No phone means no campaign is possible, so every one needs review.
    assert {item['stage'] for item in leads} == {'needs_review'}


def test_a_re_preview_keeps_the_staged_file_when_the_mapping_is_wrong(
        client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    token = preview(client, headers, MATCH_CSV).json()['token']

    # Unmapping both identifying columns leaves nothing importable.
    bad = client.post(f'{LEADS_URL}/preview', headers=headers,
                      data={'token': token,
                            'mapping': json.dumps({'phone': '', 'property_address': ''})})
    assert bad.status_code == 400

    # The file survives, so the broker can correct the choice.
    recovered = client.post(f'{LEADS_URL}/preview', headers=headers,
                            data={'token': token,
                                  'mapping': json.dumps({'property_address': 'search_address'})})
    assert recovered.status_code == 200
    assert recovered.json()['importable'] == 3


def test_a_malformed_mapping_is_rejected_cleanly(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    response = client.post(
        f'{LEADS_URL}/import',
        headers=auth_header(BROKER_EMAIL),
        files={'file': ('leads.csv', CSV.encode('utf-8'), 'text/csv')},
        data={'mapping': 'not json'},
    )
    assert response.status_code == 400
    assert 'valid JSON' in response.json()['detail']


# -- vendor files with a reason and a details blob --------------------------

# The shape of expired_listing_leads_10.csv: signals live in a "Lead Source"
# column, the reason is spelled out, and attributes arrive as JSON.
VENDOR_CSV = (
    'Name,Phone Number,Property Address,Lead Source,Outreach Reason,Lead Details\n'
    'Ana,+13125550120,"950 Edgar Dr Apt 4, Charleston, IL",Expired listing,'
    'The property appears to have come off the market without a recorded sale.,'
    '"{""beds"":""3.0"",""baths"":""2.5"",""listing_price"":""99900.0"",""owner_occ"":""No""}"\n'
    'Michael,+13125550121,"2800 Pine Ave, Mattoon, IL",Expired listing,'
    'The property appears to have come off the market without a recorded sale.,'
    '"{""beds"":""4.0"",""owner_occ"":""Yes""}"\n'
)


def test_a_lead_source_column_supplies_the_signal(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    body = preview(client, headers, VENDOR_CSV).json()
    assert body['mapping']['signals'] == 'Lead Source'
    assert body['mapping']['outreach_reason'] == 'Outreach Reason'
    assert body['mapping']['details'] == 'Lead Details'

    upload(client, headers, VENDOR_CSV)
    leads = {item['owner_name']: item for item in
             client.get(LEADS_URL, headers=headers).json()['leads']}
    assert [s['key'] for s in leads['Michael']['signals']] == ['expired']


def test_details_that_state_a_fact_add_a_signal(client, make_user, auth_header):
    """owner_occ = No is precisely what "absentee owner" means."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, VENDOR_CSV)

    leads = {item['owner_name']: item for item in
             client.get(LEADS_URL, headers=headers).json()['leads']}
    assert {s['key'] for s in leads['Ana']['signals']} == {'expired', 'absentee_owner'}
    # Michael is owner-occupied, so no absentee signal is invented for him.
    assert {s['key'] for s in leads['Michael']['signals']} == {'expired'}


def test_property_attributes_reach_the_detail_panel(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, VENDOR_CSV)
    lead_id = next(item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']
                   if item['owner_name'] == 'Ana')

    body = client.get(f'{LEADS_URL}/{lead_id}', headers=headers).json()
    assert body['details']['beds'] == '3.0'
    assert body['details']['listing_price'] == '99900.0'


def test_bobbie_uses_the_reason_shipped_with_the_lead(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, VENDOR_CSV)
    lead_id = client.get(LEADS_URL, headers=headers).json()['leads'][0]['id']

    body = client.post(f'{LEADS_URL}/campaign', headers=headers,
                       json={'lead_ids': [lead_id]}).json()
    assert 'come off the market without a recorded sale' in body['started'][0]['text']

    # The attributes travel with the conversation, so Bobbie may cite them.
    lead = session.query(Lead).filter(Lead.id == lead_id).first()
    conversation = session.query(Conversation).filter(
        Conversation.id == lead.conversation_id).first()
    assert json.loads(conversation.lead_context)['property_details']['beds']


def test_a_broken_details_blob_is_ignored_not_fatal(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    result = upload(client, headers,
                    'name,phone,address,lead details\n'
                    'Ana,+13125550120,950 Edgar Dr,"{not valid json"\n').json()
    assert result['created'] == 1


def test_the_shipped_sample_file_imports_cleanly(client, make_user, auth_header):
    """The vendor export the product was specified against."""
    sample = Path(__file__).resolve().parents[3] / 'expired_listing_leads_10.csv'
    if not sample.exists():
        pytest.skip('sample file not present')

    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    response = client.post(f'{LEADS_URL}/import', headers=headers,
                           files={'file': ('expired_listing_leads_10.csv',
                                           sample.read_bytes(), 'text/csv')})
    assert response.status_code == 200, response.text
    assert response.json() == {'created': 10, 'total_rows': 10, 'warnings': []}

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    assert all(item['phone'] and item['property_address'] for item in leads)
    assert all('expired' in {s['key'] for s in item['signals']} for item in leads)
    assert all(item['stage'] == 'new' for item in leads)


# -- every row is its own lead ---------------------------------------------


def test_the_same_file_twice_adds_every_lead_twice(client, make_user, auth_header):
    """Nothing is matched or merged, so a repeat import is a repeat import."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    assert upload(client, headers).json()['created'] == 3
    assert upload(client, headers).json()['created'] == 3
    assert client.get(LEADS_URL, headers=headers).json()['facets']['total'] == 6


def test_repeated_rows_within_one_file_each_become_a_lead(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    result = upload(client, headers,
                    'owner,phone,address,signals\n'
                    'Marcus Webb,3125550142,4517 W Adams St,FSBO\n'
                    'Marcus Webb,3125550142,4517 W Adams St,Vacant\n').json()
    assert result['created'] == 2

    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    assert len(leads) == 2
    # Each keeps only the signal on its own row; nothing is merged across them.
    assert sorted({s['key'] for item in leads for s in item['signals']}) == ['fsbo', 'vacant']


def test_the_limit_takes_rows_from_the_top(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    response = client.post(
        f'{LEADS_URL}/import',
        headers=headers,
        files={'file': ('leads.csv', CSV.encode('utf-8'), 'text/csv')},
        data={'limit': '2'},
    )
    assert response.json() == {'created': 2, 'total_rows': 3, 'warnings': []}
    names = {item['owner_name'] for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert names == {'Marcus Webb', 'Priya Nguyen'}


def test_a_large_import_is_written_in_batches(client, make_user, auth_header):
    """Well past IMPORT_CHUNK, to exercise more than one flush."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)

    text = 'owner,phone,address,signals\n' + ''.join(
        f'Owner {n},+1312{n:07d},{n} Test Ave,fsbo\n' for n in range(2500))
    assert upload(client, headers, text).json()['created'] == 2500
    assert client.get(LEADS_URL, headers=headers).json()['facets']['total'] == 2500
