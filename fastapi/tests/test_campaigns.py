"""Tests for campaigns: drafting, the message template, sending and reporting."""

import json

import pytest

from datetime import datetime

from app.leads import campaign as campaign_service
from app.leads import reason_suggest
from app.leads import template as message_template
from app.leads.address import canonical
from app.models import Campaign, Conversation, Lead, LeadEvent, Message, OutboundSmsJob, User
from app.sms import service
from app.sms.number_lookup import NumberLookupResult

# Send enqueues durable jobs; run the real worker inline so these tests keep
# asserting what a completed campaign did.
pytestmark = pytest.mark.usefixtures('drain_campaign_queue')

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
LEADS_URL = '/api/v1/leads'
CAMPAIGNS_URL = '/api/v1/campaigns'

CSV = (
    'Owner Name,Phone,Property Address,City,Signals,Outreach Reason\n'
    'Marcus Webb,(312) 555-0142,4517 W Adams St,Austin,"Absentee Owner, High Equity",Records show the owner lives elsewhere\n'
    'Priya Nguyen,+13125550188,2210 S Sacramento Dr,Little Village,Pre-Foreclosure,\n'
    'Emeka Obi,3125550199,7745 S Coles Ave,South Shore,Probate,\n'
)


@pytest.fixture
def sent_sms(monkeypatch):
    """Capture outbound texts instead of calling Telnyx."""
    outbox = []

    def fake_send(payload):
        outbox.append(payload)
        return {'data': {'id': f'sim-{len(outbox)}'}}

    monkeypatch.setattr('app.sms.service.send_sms', fake_send)
    return outbox


@pytest.fixture(autouse=True)
def no_ai_replies(monkeypatch):
    """Bobbie's reply pipeline needs DeepSeek; these tests only need the reply to land."""
    monkeypatch.setattr('app.routes.webhooks.service.process_ai_reply',
                        lambda conversation_id, text: None)


@pytest.fixture(autouse=True)
def mobile_number_lookups(monkeypatch):
    """Campaign tests stay provider-free; dedicated tests exercise the live client."""
    monkeypatch.setattr(
        campaign_service.number_lookup,
        'lookup_number',
        lambda phone: NumberLookupResult(
            phone_number=phone,
            status='simulation_bypass',
        ),
    )


def owner_replies(client, contact, text='Yes, tell me more.'):
    """An inbound SMS through the real webhook, as the carrier would deliver it."""
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


def upload(client, headers, text=CSV):
    return client.post(f'{LEADS_URL}/import', headers=headers,
                       files={'file': ('leads.csv', text.encode('utf-8'), 'text/csv')})


def lead_ids(client, headers):
    return [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]


def lead_id_for(client, headers, fragment):
    """One lead by a fragment of its address — the pool's order is not the CSV's."""
    leads = client.get(LEADS_URL, headers=headers).json()['leads']
    matches = [item['id'] for item in leads if fragment in (item['property_address'] or '')]
    assert len(matches) == 1, f'{fragment}: {matches}'
    return matches[0]


def draft(client, headers, ids):
    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 200, response.text
    return response.json()


def test_campaign_claim_is_property_and_phone_pair(session, make_user):
    owner = make_user(BROKER_EMAIL, role='broker')
    conversation = Conversation(
        contact='+13125550142', user_id=owner.id,
        property_address='415 Northview Lane, Hoffman Estates, IL, 60169',
        property_key=canonical('415 Northview Lane, Hoffman Estates, IL, 60169'),
    )
    session.add(conversation)
    session.flush()
    session.add(Message(
        conversation_id=conversation.id, direction='outbound',
        event_type='outreach.initial', property_key=conversation.property_key,
    ))
    session.commit()

    co_owner = Lead(
        user_id=owner.id, owner_name='Alex Whitfield', phone='+13125559999',
        property_address='415 Northview Ln, Hoffman Estates IL 60169',
    )
    repeated_contact = Lead(
        user_id=owner.id, owner_name='Dana Whitfield', phone='(312) 555-0142',
        property_address='415 Northview Ln, Hoffman Estates IL 60169',
    )
    claims = campaign_service.claims_on(session, [owner.id], [co_owner, repeated_contact])

    assert claims.reason_for(co_owner) is None
    assert claims.reason_for(repeated_contact) == campaign_service.CLAIMED_HERE


# -- the template ----------------------------------------------------------


def test_the_template_fills_each_owner_s_own_address_and_reason():
    class FakeLead:
        owner_name = 'Dana Whitfield'
        property_address = '415 Northview Lane, Hoffman Estates, IL'
        area = 'Hoffman Estates'
        signals = 'expired_listing'
        outreach_reason = ('I see that your property is no longer listed, have you thought '
                           'about putting it back on the market?')

    class FakeUser:
        brokerage_name = 'RE/MAX Premier'
        full_name = 'Bobbie Fisher'

    text = message_template.render(message_template.DEFAULT_TEMPLATE, FakeLead(), FakeUser())
    assert text == (
        'Hi Dana, this is the team lead at RE/MAX Premier, I’m reaching out about '
        '415 Northview Lane, Hoffman Estates, IL. I see that your property is no longer '
        'listed, have you thought about putting it back on the market?\n\n'
        'Reply STOP to Opt-out'
    )
    assert text.count('Reply STOP to Opt-out') == 1


def test_a_nameless_owner_is_greeted_without_a_dangling_comma():
    class FakeLead:
        owner_name = ''
        property_address = '9 Oak St'
        area = ''
        signals = 'fsbo'
        outreach_reason = ''

    class FakeUser:
        brokerage_name = ''
        full_name = ''

    text = message_template.render('Hi {{first_name}}, about {{address}}.', FakeLead(), FakeUser())
    assert text.startswith('Hi there, about 9 Oak St.')
    assert 'Reply STOP to Opt-out' not in text


def test_an_invented_placeholder_is_refused_before_anything_is_sent():
    try:
        message_template.validate('Hi {{first_name}}, about {{property}}.')
    except ValueError as error:
        assert '{{property}}' in str(error)
    else:
        raise AssertionError('An unknown placeholder must not validate.')


# -- drafting --------------------------------------------------------------


def test_draft_preview_renders_only_the_requested_twenty_recipient_page(
        make_user, session):
    owner = make_user(BROKER_EMAIL, role='broker')
    campaign = Campaign(
        user_id=owner.id,
        name='Large draft',
        message_template='Hi {{first_name}}, about {{address}}.',
        status='draft',
    )
    session.add(campaign)
    session.flush()
    leads = [
        Lead(
            user_id=owner.id,
            campaign_id=campaign.id,
            owner_name=f'Owner {index}',
            phone=f'+1312555{index:04d}',
            property_address=f'{index} Main St, Chicago, IL 60601',
            score=70,
        )
        for index in range(1, 26)
    ]
    session.add_all(leads)
    session.commit()

    body = campaign_service.preview(session, owner, campaign, page=2)

    assert body['page'] == 2
    assert body['page_size'] == 20
    assert body['total_items'] == 25
    assert body['total_pages'] == 2
    assert len(body['recipients']) == 5
    assert [item['owner_name'] for item in body['recipients']] == [
        'Owner 21', 'Owner 22', 'Owner 23', 'Owner 24', 'Owner 25',
    ]


@pytest.mark.parametrize('selected_count', [500, 4_000])
def test_large_draft_validates_and_sends_the_full_set_independent_of_preview_page(
        selected_count, make_user, session, monkeypatch):
    owner = make_user(BROKER_EMAIL, role='broker')
    leads = [
        Lead(
            user_id=owner.id,
            owner_name=f'Owner {index}',
            phone=f'+1312{index:07d}',
            property_address=f'{index} Scale Test Ave, Chicago, IL 60601',
            score=70,
            dnc=index % 100 == 0,
        )
        for index in range(1, selected_count + 1)
    ]
    session.add_all(leads)
    session.commit()
    selected_ids = [lead.id for lead in leads]
    eligible_ids = [lead.id for lead in leads if not lead.dnc]
    progress = []

    campaign, not_added = campaign_service.create_draft(
        session,
        owner,
        selected_ids + selected_ids[:10],  # duplicate selection IDs are processed once
        progress=lambda total, loaded: progress.append((total, loaded)),
    )

    authoritative_ids = campaign_service.member_ids(session, owner, campaign)
    assert set(authoritative_ids) == set(eligible_ids)
    assert len(authoritative_ids) == selected_count - (selected_count // 100)
    assert len(not_added) == selected_count // 100
    assert {item['reason'] for item in not_added} == {'On the do-not-contact list.'}
    assert progress[0] == (selected_count, 0)
    assert progress[-1] == (selected_count, selected_count)
    assert session.query(LeadEvent).filter(
        LeadEvent.event_type == 'locked_to_campaign',
    ).count() == len(eligible_ids)

    first_page = campaign_service.preview(session, owner, campaign, page=1)
    last_page = campaign_service.preview(
        session, owner, campaign, page=first_page['total_pages'],
    )
    assert len(first_page['recipients']) == 20
    assert len(last_page['recipients']) <= 20
    assert first_page['total_items'] == len(eligible_ids)
    assert last_page['total_items'] == len(eligible_ids)
    assert campaign_service.member_ids(session, owner, campaign) == authoritative_ids

    lead_by_phone = {lead.phone: lead.id for lead in leads}
    sent_ids = []

    def fake_send(payload):
        sent_ids.append(lead_by_phone[payload['to']])
        return {'data': {'id': f'sim-{len(sent_ids)}'}}

    monkeypatch.setattr(service, 'send_sms', fake_send)
    monkeypatch.setattr(campaign_service, 'schedule_initial_followup', lambda *_: None)
    result = campaign_service.send(session, owner, campaign)

    assert result['skipped'] == []
    assert sent_ids == authoritative_ids
    assert len(result['started']) == len(eligible_ids)


def test_draft_removes_only_duplicate_property_phone_pairs(make_user, session):
    owner = make_user(BROKER_EMAIL, role='broker')
    leads = [
        Lead(user_id=owner.id, owner_name='First', phone='+13125550100',
             property_address='10 Main Street, Chicago, IL'),
        Lead(user_id=owner.id, owner_name='Duplicate', phone='(312) 555-0100',
             property_address='10 Main St Chicago IL'),
        Lead(user_id=owner.id, owner_name='Co-owner', phone='+13125550101',
             property_address='10 Main Street, Chicago, IL'),
    ]
    session.add_all(leads)
    session.commit()

    campaign, not_added = campaign_service.create_draft(
        session, owner, [lead.id for lead in leads],
    )

    assert campaign_service.member_ids(session, owner, campaign) == [leads[0].id, leads[2].id]
    assert not_added == [{
        'lead_id': leads[1].id,
        'owner_name': 'Duplicate',
        'reason': 'Duplicate property and phone in this selection.',
    }]


def test_background_draft_job_returns_only_the_first_preview_page(
        make_user, session, monkeypatch):
    from app.routes import campaigns as campaign_routes

    owner = make_user(BROKER_EMAIL, role='broker')
    leads = [
        Lead(
            user_id=owner.id,
            owner_name=f'Worker Owner {index}',
            phone=f'+1773{index:07d}',
            property_address=f'{index} Worker Ave, Chicago, IL 60601',
        )
        for index in range(1, 26)
    ]
    session.add_all(leads)
    session.commit()
    completed = {}
    failures = []

    monkeypatch.setattr(campaign_routes.query_progress, 'update', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        campaign_routes.query_progress,
        'complete',
        lambda job_id, result, total: completed.update(
            job_id=job_id, result=result, total=total,
        ),
    )
    monkeypatch.setattr(
        campaign_routes.query_progress,
        'fail',
        lambda job_id, error: failures.append((job_id, error)),
    )

    campaign_routes._create_draft_job('draft-job', owner.id, {
        'lead_ids': [lead.id for lead in leads],
        'name': None,
        'message_template': None,
    })

    assert failures == []
    assert completed['job_id'] == 'draft-job'
    assert completed['total'] == 25
    assert completed['result']['campaign']['recipients'] == 25
    preview = completed['result']['campaign']['preview']
    assert preview['total_items'] == 25
    assert preview['total_pages'] == 2
    assert len(preview['recipients']) == 20


def test_creating_a_draft_holds_the_leads_and_sends_nothing(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    body = draft(client, headers, lead_ids(client, headers))
    assert body['campaign']['status'] == 'draft'
    assert body['campaign']['recipients'] == 3
    assert body['not_added'] == []
    # The whole point of the composer: nothing goes out until Send.
    assert sent_sms == []

    # Every recipient is rendered, each with their own address.
    previews = body['campaign']['preview']['recipients']
    assert len(previews) == 3
    assert any('4517 W Adams St' in item['text'] for item in previews)
    assert all(item['text'].startswith('Hi ') for item in previews)


def test_provider_lead_fans_out_only_to_non_dnc_numbers_with_contact_name(
        make_user, sent_sms, session):
    owner = make_user(BROKER_EMAIL, role='broker')
    phones = [
        {'number': '+12193748814', 'dnc': False, 'owner_name': 'Moriah Theobald'},
        {'number': '+12193843336', 'dnc': True, 'owner_name': 'Moriah Theobald'},
        {'number': '+12196711006', 'dnc': True, 'owner_name': 'Moriah Theobald'},
        {'number': '+18504193904', 'dnc': False, 'owner_name': 'Moriah Theobald'},
    ]
    lead = Lead(
        user_id=owner.id,
        owner_name='Christopher Theobald; Moriah Theobald',
        phone=phones[0]['number'],
        property_address='3811 N Kildare Ave, Chicago, IL, 60641',
        area='Chicago',
        source='provider_distribution',
        details=json.dumps({'phones': phones, '_phone_numbers': [
            {**phone, 'phone': phone['number']} for phone in phones
        ]}),
    )
    session.add(lead)
    session.flush()
    lead_id = lead.id
    campaign = Campaign(
        user_id=owner.id,
        name='Chicago owners',
        message_template='Hi {{first_name}}, about {{address}}.',
        status='draft',
    )
    session.add(campaign)
    session.commit()

    assert campaign_service.attach(session, owner, campaign, [lead_id]) == []
    recipients = campaign_service.preview(session, owner, campaign)['recipients']
    assert len(recipients) == 2
    assert {row['owner_name'] for row in recipients} == {'Moriah Theobald'}
    assert {row['phone'] for row in recipients} == {
        '+12193748814', '+18504193904',
    }

    campaign_service.release_draft_members(session, owner, campaign)
    session.commit()
    session.refresh(lead)
    assert lead.owner_name == 'Christopher Theobald; Moriah Theobald'
    assert session.query(Lead).filter_by(
        source=campaign_service.PROVIDER_CONTACT_SOURCE
    ).count() == 0

    assert campaign_service.attach(session, owner, campaign, [lead_id]) == []

    sent = campaign_service.send(session, owner, campaign)
    assert len(sent['started']) == 2
    assert {payload['to'] for payload in sent_sms} == {
        '+12193748814', '+18504193904',
    }
    assert session.query(Conversation).filter(
        Conversation.name == 'Moriah Theobald'
    ).count() == 2


def test_a_draft_reports_the_leads_it_could_not_take(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)

    # Send the first lead, then try to put it in a second campaign.
    first = draft(client, headers, [ids[0]])
    client.post(f"{CAMPAIGNS_URL}/{first['campaign']['id']}/send", headers=headers)

    second = draft(client, headers, ids)
    assert [item['reason'] for item in second['not_added']] == ['Already texted in a campaign.']
    assert second['campaign']['recipients'] == 2


def test_a_lead_with_no_phone_stays_in_the_pool(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, CSV + 'No Number,,900 W Nowhere Ave,Austin,FSBO\n')

    body = draft(client, headers, lead_ids(client, headers))
    # It never joins the draft: a lead a campaign can never text stays in the
    # pool, where the missing number can actually be fixed.
    assert [item['reason'] for item in body['not_added']] == ['Needs review — no usable phone number.']
    assert len(body['campaign']['preview']['recipients']) == 3
    assert body['campaign']['preview']['skipped'] == []

    still_listed = client.get(LEADS_URL, headers=headers).json()['leads']
    assert [item['owner_name'] for item in still_listed] == ['No Number']


def test_rewriting_the_message_re_renders_every_recipient(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    campaign_id = draft(client, headers, ids)['campaign']['id']

    response = client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers, json={
        'name': 'Expired listings — Austin',
        'message_template': 'Quick note about {{address}} — {{agent_name}}',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['name'] == 'Expired listings — Austin'
    assert all(item['text'].startswith('Quick note about ')
               for item in body['preview']['recipients'])


def test_a_template_with_an_unknown_placeholder_is_rejected(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    campaign_id = draft(client, headers, ids)['campaign']['id']

    response = client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                            json={'message_template': 'Hi {{first_name}} about {{property}}'})
    assert response.status_code == 400
    assert '{{property}}' in response.json()['detail']


def test_discarding_a_draft_returns_its_leads_to_the_pool(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    assert client.delete(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers).status_code == 200
    assert client.get(CAMPAIGNS_URL, headers=headers).json() == []
    assert session.query(Lead).filter(Lead.campaign_id.isnot(None)).count() == 0


# -- sending ---------------------------------------------------------------


def test_campaign_outreach_starts_in_user_handled_mode(session, monkeypatch):
    user = User(
        email=BROKER_EMAIL,
        hashed_password='unused-in-this-test',
        role='broker',
        brokerage_id='brokerage-1',
        is_active=True,
    )
    session.add(user)
    session.flush()
    campaign = Campaign(
        user_id=user.id,
        name='Owner outreach',
        message_template='Hi {{first_name}} about {{property}}',
        status='draft',
    )
    lead = Lead(
        user_id=user.id,
        owner_name='Maya Chen',
        phone='+13125848528',
        property_address='123 Maple Ave, Austin, TX',
        signals='high_equity',
        campaign_id=None,
    )
    session.add_all([campaign, lead])
    session.commit()

    def store_message(db, conversation, text, event_type):
        message = Message(
            conversation_id=conversation.id,
            direction='outbound',
            to_number=conversation.contact,
            text=text,
            status='queued',
            event_type=event_type,
            telnyx_id='sim-campaign-1',
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    monkeypatch.setattr(campaign_service.sms_service, 'send_and_store_message', store_message)
    monkeypatch.setattr(campaign_service, 'schedule_initial_followup', lambda *_: None)

    started, failed = campaign_service._send_to_lead(
        session, user, campaign, lead, 'Hi Maya about 123 Maple Ave',
    )

    assert failed is None
    assert started['conversation_id']
    conversation = session.query(Conversation).one()
    assert conversation.handled_by == 'broker'
    assert conversation.ai_enabled is False


def test_sending_texts_every_recipient_the_message_they_were_shown(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    body = draft(client, headers, lead_ids(client, headers))
    campaign_id = body['campaign']['id']
    previewed = {item['lead_id']: item['text'] for item in body['campaign']['preview']['recipients']}
    assert all(text.endswith('Reply STOP to Opt-out') for text in previewed.values())
    assert all(text.count('Reply STOP to Opt-out') == 1 for text in previewed.values())
    assert all(
        item['is_long'] == (len(item['text']) > message_template.LONG_MESSAGE_CHARS)
        for item in body['campaign']['preview']['recipients']
    )

    response = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['started']) == 3
    # What the broker read in the preview is exactly what went out.
    assert {item['lead_id']: item['text'] for item in result['started']} == previewed
    assert len(sent_sms) == 3
    assert all(item['text'].endswith('Reply STOP to Opt-out') for item in sent_sms)

    # Campaign threads start user-handled; Bobbie can be handed the thread later.
    conversations = client.get('/api/v1/sms/conversations', headers=headers).json()
    assert all(item['handled_by'] == 'broker' for item in conversations)
    assert all(item['ai_enabled'] is False for item in conversations)
    assert session.query(Conversation).filter(
        Conversation.campaign_id == campaign_id).count() == 3


def test_campaign_sends_only_mobile_numbers_and_saves_lookup_results(
        make_user, session, monkeypatch):
    owner = make_user(BROKER_EMAIL, role='broker')
    campaign = Campaign(
        user_id=owner.id,
        name='Mobile-only campaign',
        message_template='Hi {{first_name}}, about {{address}}.',
        status='draft',
    )
    session.add(campaign)
    session.flush()
    leads = [
        Lead(
            user_id=owner.id,
            campaign_id=campaign.id,
            owner_name=f'Owner {index}',
            phone=f'+1312555014{index}',
            property_address=f'{index} Main St, Chicago, IL 60601',
            details=json.dumps({'_phone_numbers': [{
                'phone': f'+1312555014{index}', 'dnc': False,
                'owner_name': f'Owner {index}',
            }]}),
        )
        for index in range(3)
    ]
    session.add_all(leads)
    session.commit()
    ids = [lead.id for lead in leads]
    outcomes = {
        leads[0].phone: NumberLookupResult(
            phone_number=leads[0].phone, status='verified', line_type='mobile',
            carrier_name='Mobile Carrier', checked_at='2026-10-16T01:00:00Z',
        ),
        leads[1].phone: NumberLookupResult(
            phone_number=leads[1].phone, status='verified', line_type='landline',
            carrier_name='Landline Carrier', checked_at='2026-10-16T01:00:01Z',
        ),
        leads[2].phone: NumberLookupResult(
            phone_number=leads[2].phone, status='error',
            checked_at='2026-10-16T01:00:02Z', error_code='request_failed',
        ),
    }
    monkeypatch.setattr(
        campaign_service.number_lookup, 'lookup_number', lambda phone: outcomes[phone],
    )
    lead_by_phone = {lead.phone: lead.id for lead in leads}
    sent_ids = []

    def fake_send(payload):
        sent_ids.append(lead_by_phone[payload['to']])
        return {'data': {'id': f'sim-{len(sent_ids)}'}}

    monkeypatch.setattr(service, 'send_sms', fake_send)
    monkeypatch.setattr(campaign_service, 'schedule_initial_followup', lambda *_: None)
    # Live mode is what routes jobs through the Number Lookup stage.
    monkeypatch.setitem(campaign_service.settings['telnyx'], 'mode', 'telnyx')

    result = campaign_service.send(session, owner, campaign)

    assert [item['lead_id'] for item in result['started']] == [leads[0].id]
    assert sent_ids == [leads[0].id]
    assert {item['reason'] for item in result['skipped']} == {
        'Number is landline; campaigns send only to mobile numbers.',
        'Number lookup failed, so no message was sent.',
    }
    session.expire_all()
    saved = {lead.id: json.loads(lead.details)['_phone_numbers'][0]
             for lead in session.query(Lead).filter(Lead.id.in_(ids)).all()}
    assert saved[leads[0].id]['type'] == 'mobile'
    assert saved[leads[0].id]['telnyx_lookup']['sms_eligible'] is True
    assert saved[leads[1].id]['type'] == 'landline'
    assert saved[leads[1].id]['telnyx_lookup']['sms_eligible'] is False
    assert saved[leads[2].id]['lookup_status'] == 'error'
    assert all(not row['dnc'] for row in saved.values())


def test_a_campaign_cannot_be_sent_twice(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)
    again = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)
    assert again.status_code == 400
    assert len(sent_sms) == 3


def test_a_sent_campaign_cannot_be_deleted(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)

    assert client.delete(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers).status_code == 400


# -- reporting -------------------------------------------------------------


def test_the_overview_counts_delivered_replied_and_silent(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    started = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers).json()['started']

    # One owner answers; the other two stay quiet.
    replied = session.query(Conversation).filter(
        Conversation.id == started[0]['conversation_id']).first()
    owner_replies(client, replied.contact)

    overview = client.get(CAMPAIGNS_URL, headers=headers).json()
    assert len(overview) == 1
    assert overview[0]['recipients'] == 3
    assert overview[0]['delivered'] == 3
    assert overview[0]['replied'] == 1
    assert overview[0]['no_reply'] == 2
    assert overview[0]['not_sent'] == 0


def test_failed_delivery_is_not_counted_as_waiting_for_a_reply(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)

    failed = (session.query(Message)
              .filter(Message.campaign_id == campaign_id,
                      Message.event_type == 'outreach.initial')
              .first())
    failed.status = 'delivery_failed'
    failed.failure_code = '40001'
    failed.failure_reason = 'The destination is a landline.'
    session.commit()

    campaign = client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers).json()
    recipient = next(row for row in campaign['preview']['recipients']
                     if row['conversation_id'] == failed.conversation_id)
    assert recipient['delivery_status'] == 'delivery_failed'
    assert recipient['delivery_failure_reason'] == 'The destination is a landline.'
    assert campaign['delivered'] == 2
    assert campaign['no_reply'] == 3
    assert campaign['not_sent'] == 1


def test_completed_campaign_shows_the_recorded_reason_for_an_unsent_lead(
        session, make_user):
    owner = make_user(BROKER_EMAIL, role='broker')
    campaign = Campaign(
        user_id=owner.id,
        name='Landline results',
        message_template='Hi {{first_name}}.',
        status='sent',
    )
    session.add(campaign)
    session.flush()
    lead = Lead(
        user_id=owner.id,
        campaign_id=campaign.id,
        owner_name='Moriah Theobald',
        phone='+13125550141',
        property_address='141 Main St, Chicago, IL 60601',
    )
    session.add(lead)
    session.flush()
    session.add(OutboundSmsJob(
        job_key=f'campaign:{campaign.id}:lead:{lead.id}',
        campaign_id=campaign.id,
        lead_id=lead.id,
        user_id=owner.id,
        to_number=lead.phone,
        property_key=canonical(lead.property_address),
        text='',
        status='skipped',
        last_error='Number is landline; campaigns send only to mobile numbers.',
    ))
    session.commit()

    result = campaign_service.preview(session, owner, campaign)

    assert result['recipients'] == []
    assert result['skipped'] == [{
        'lead_id': lead.id,
        'owner_name': 'Moriah Theobald',
        'reason': 'Number is landline; campaigns send only to mobile numbers.',
    }]


def test_campaign_reporting_counts_unique_property_addresses(session):
    user = User(email=BROKER_EMAIL, hashed_password='unused', role='broker',
                brokerage_id='brokerage-1')
    campaign = Campaign(user=user, name='One property', status='sent')
    session.add_all([user, campaign])
    session.flush()

    addresses = (
        '415 Northview Lane, Hoffman Estates, IL, 60169',
        '415 Northview Ln, Hoffman Estates IL 60169',
    )
    property_key = canonical(addresses[0])
    conversations = [
        Conversation(user_id=user.id, contact='+13125550142', property_address=addresses[0]),
        Conversation(user_id=user.id, contact='+13125559999', property_address=addresses[1]),
    ]
    session.add_all(conversations)
    session.flush()
    session.add_all([
        Lead(user_id=user.id, campaign_id=campaign.id, owner_name='Dana Whitfield',
             phone=conversations[0].contact, property_address=addresses[0]),
        Lead(user_id=user.id, campaign_id=campaign.id, owner_name='Alex Whitfield',
             phone=conversations[1].contact, property_address=addresses[1]),
        Message(conversation_id=conversations[0].id, campaign_id=campaign.id,
                direction='outbound', event_type='outreach.initial', status='sent',
                property_key=property_key),
        Message(conversation_id=conversations[1].id, campaign_id=campaign.id,
                direction='outbound', event_type='outreach.initial', status='sent',
                property_key=property_key),
    ])
    session.commit()

    # Both phone attempts remain, but the campaign reports one property.
    members, delivered, replied = campaign_service._counts_by_campaign(session, user)
    assert members[campaign.id] == 1
    assert delivered[campaign.id] == 1
    assert replied.get(campaign.id, 0) == 0
    assert campaign_service._stats(campaign, members[campaign.id],
                                   delivered[campaign.id], 0)['no_reply'] == 1

    session.add(Message(conversation_id=conversations[0].id, direction='inbound',
                        event_type='sms.inbound', status='received', text='Interested'))
    session.commit()
    _members, _delivered, replied = campaign_service._counts_by_campaign(session, user)
    assert replied[campaign.id] == 1
    assert campaign_service._stats(campaign, members[campaign.id],
                                   delivered[campaign.id], replied[campaign.id])['no_reply'] == 0


def test_follow_ups_can_be_filtered_to_one_campaign(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)

    first = draft(client, headers, ids[:1])['campaign']['id']
    started_first = client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=headers).json()['started']
    second = draft(client, headers, ids[1:])['campaign']['id']
    started_second = client.post(f'{CAMPAIGNS_URL}/{second}/send', headers=headers).json()['started']

    for row in started_first + started_second:
        conversation = session.query(Conversation).filter(
            Conversation.id == row['conversation_id']).first()
        owner_replies(client, conversation.contact, 'Interested.')

    everything = client.get('/api/v1/followups', headers=headers).json()
    assert len(everything) == 3
    assert {item['campaign_id'] for item in everything} == {first, second}

    only_second = client.get(f'/api/v1/followups?campaign_id={second}', headers=headers).json()
    assert len(only_second) == 2
    assert all(item['campaign_id'] == second for item in only_second)


def test_campaigns_are_scoped_to_the_owning_broker(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user('other.broker@linchpinglobal.net', role='broker',
              brokerage_id='brokerage-2', brokerage_name='Other Group')
    headers = auth_header(BROKER_EMAIL)
    intruder = auth_header('other.broker@linchpinglobal.net')
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    assert client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=intruder).status_code == 404
    assert client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=intruder).status_code == 404
    assert client.get(CAMPAIGNS_URL, headers=intruder).json() == []
    assert sent_sms == []


# -- the shared reason -----------------------------------------------------


def test_a_campaign_reason_overrides_every_lead_s_own_signal(
        client, make_user, auth_header, sent_sms, session):
    """One set, one story: the broker's sentence replaces the per-signal wording."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    # A pre-group-mode campaign retains its historical shared override.
    session.get(Campaign, campaign_id).reason_groups = None
    session.commit()

    shared = ('I see that your property is no longer listed, have you thought about '
              'putting it back on the market?')
    body = client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                        json={'outreach_reason': shared}).json()
    assert body['outreach_reason'] == shared
    # Every recipient now ends on the same sentence, with their own address before it.
    assert all(item['text'].endswith(f'{shared}\n\nReply STOP to Opt-out')
               for item in body['preview']['recipients'])
    assert len({item['text'] for item in body['preview']['recipients']}) == 3


def test_clearing_the_reason_hands_it_back_to_each_lead(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                 json={'outreach_reason': 'Shared reason.'})
    body = client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                        json={'outreach_reason': ''}).json()
    assert body['outreach_reason'] == ''
    texts = {item['text'] for item in body['preview']['recipients']}
    assert any('pre-foreclosure' in text.lower() for text in texts)
    assert any('probate' in text.lower() for text in texts)


def test_the_reason_that_was_previewed_is_the_one_that_is_sent(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    session.get(Campaign, campaign_id).reason_groups = None
    session.commit()
    shared = 'I noticed the listing came off the market — are you still open to selling?'
    client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                 json={'outreach_reason': shared})

    started = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers).json()['started']
    assert all(row['text'].endswith(f'{shared}\n\nReply STOP to Opt-out') for row in started)
    assert all(text['text'].endswith(f'{shared}\n\nReply STOP to Opt-out') for text in sent_sms)


def test_suggestions_come_from_the_signals_the_set_shares(
        client, make_user, auth_header, sent_sms, monkeypatch):
    """Every lead here is expired, so the suggestion must be about an expired listing."""
    monkeypatch.setattr('app.leads.reason_suggest.deepseek.is_configured', lambda: False)
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'A One,+13125550101,1 First St,Austin,Expired\n'
           'B Two,+13125550102,2 Second St,Austin,Expired\n'
           'C Three,+13125550103,3 Third St,Austin,"Expired, High Equity"\n')
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    body = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers).json()
    assert body['source'] == 'catalog'
    # Expired is on every lead; high equity on one, so it must not lead.
    assert body['signals'][0] == {'key': 'expired', 'label': 'Expired', 'count': 3, 'share': 1.0}
    assert 'no longer listed' in body['suggestions'][0]

    secondary = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions',
        headers=headers, json={'signal': 'high_equity'},
    ).json()
    assert secondary['signals'] == [{
        'key': 'high_equity', 'label': 'High Equity', 'count': 1, 'share': 1.0,
    }]
    assert secondary['suggestions'] == []
    assert secondary['options'] == []
    assert 'Retry Suggest with AI' in secondary['note']


def test_per_signal_suggestions_combine_preferred_and_mocked_ai_without_full_sms(
        client, make_user, auth_header, sent_sms, monkeypatch):
    def fake_completion(messages, **options):
        prompt = messages[1]['content']
        if 'Absentee Owner' in prompt:
            return {'content': json.dumps({'suggestions': [
                'The ownership record shows a different mailing address; would you consider selling?',
                'I work with owners managing property from elsewhere; is a sale worth discussing?',
                'An unused third option.',
            ]})}
        if 'Expired' in prompt:
            return {'content': json.dumps({'suggestions': [
                'The listing ended without a sale; are you still considering selling?',
                'Would you be open to discussing another approach after the listing expired?',
                'A third expired alternative.',
            ]})}
        return {'content': json.dumps({'suggestions': [
            'I came across the probate record and wanted to ask if selling is being considered.',
            'I help with estate properties; would discussing a sale be useful?',
            'Would selling the property help with the estate plans?',
        ]})}

    monkeypatch.setattr('app.leads.reason_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.leads.reason_suggest.deepseek.completion', fake_completion)
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'Marcus Webb,+13125550142,4517 W Adams St,Austin,Absentee Owner\n'
           'Evan Stone,+13125550143,18 Market St,Austin,Expired\n'
           'Priya Nguyen,+13125550188,2210 S Sacramento Dr,Austin,Probate\n')
    ids = lead_ids(client, headers)
    marcus_id = lead_id_for(client, headers, '4517 W Adams')
    campaign_id = draft(client, headers, ids)['campaign']['id']

    absentee = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers,
        json={'signal': 'absentee_owner'},
    ).json()
    assert len(absentee['options']) == 3
    assert absentee['options'][0] == {
        'text': 'I see you own the property but live elsewhere, have you thought about selling it?',
        'source': 'preferred',
    }
    assert [option['source'] for option in absentee['options']] == [
        'preferred', 'ai', 'ai',
    ]
    assert absentee['suggestions'] == [option['text'] for option in absentee['options']]
    assert not absentee['options'][0]['text'].startswith('Hi ')
    assert '4517 W Adams' not in absentee['options'][0]['text']

    client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/recipients/main-signal', headers=headers,
        json={'main_signal': 'absentee_owner',
              'selection': {'mode': 'ids', 'lead_ids': [marcus_id]}},
    )
    rendered = client.patch(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        json={'reason_group': {
            'signal': 'absentee_owner',
            'reason': absentee['options'][0]['text'],
            'confirmed': True,
        }},
    ).json()
    text = next(row['text'] for row in rendered['preview']['recipients']
                if row['lead_id'] == marcus_id)
    assert text.count('Hi Marcus') == 1
    assert text.count('4517 W Adams St') == 1
    assert text.endswith(f"{absentee['options'][0]['text']}\n\nReply STOP to Opt-out")
    assert sent_sms == []

    expired = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers,
        json={'signal': 'expired'},
    ).json()
    assert expired['options'][0] == {
        'text': ('I see that your property is no longer listed, have you thought about '
                 'putting it back on the market?'),
        'source': 'preferred',
    }
    assert [option['source'] for option in expired['options']] == [
        'preferred', 'ai', 'ai',
    ]

    probate = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers,
        json={'signal': 'probate'},
    ).json()
    assert len(probate['options']) == 3
    assert {option['source'] for option in probate['options']} == {'ai'}
    assert len({option['text'] for option in probate['options']}) == 3


def test_per_signal_ai_failure_returns_only_legitimate_preferred_option(
        client, make_user, auth_header, sent_sms, monkeypatch):
    from app.sms import deepseek

    def boom(messages, **options):
        raise deepseek.AiUnavailableError('offline test outage')

    monkeypatch.setattr('app.leads.reason_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.leads.reason_suggest.deepseek.completion', boom)
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    absentee = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers,
        json={'signal': 'absentee_owner'},
    ).json()
    assert len(absentee['options']) == 1
    assert absentee['options'][0]['source'] == 'preferred'
    assert absentee['suggestions'] == [absentee['options'][0]['text']]
    assert 'Retry Suggest with AI' in absentee['note']

    probate = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers,
        json={'signal': 'probate'},
    ).json()
    assert probate['options'] == []
    assert probate['suggestions'] == []
    assert 'Retry Suggest with AI' in probate['note']


def test_suggestions_use_deepseek_when_it_is_configured(
        client, make_user, auth_header, sent_sms, monkeypatch):
    seen = {}

    def fake_completion(messages, **options):
        seen['messages'] = messages
        return {'content': '```json\n{"suggestions": ["Phrased by the model."]}\n```'}

    monkeypatch.setattr('app.leads.reason_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.leads.reason_suggest.deepseek.completion', fake_completion)

    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    body = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers).json()
    assert body['source'] == 'ai'
    assert body['suggestions'][0] == 'Phrased by the model.'
    # The model is told the signal counts and nothing about the owners.
    prompt = seen['messages'][1]['content']
    assert 'Absentee Owner' in prompt
    assert 'Marcus Webb' not in prompt


def test_a_deepseek_outage_falls_back_to_the_catalog_wordings(
        client, make_user, auth_header, sent_sms, monkeypatch):
    """The composer must still offer something when the provider is down."""
    from app.sms import deepseek

    def boom(messages, **options):
        raise deepseek.AiUnavailableError('AI provider unreachable')

    monkeypatch.setattr('app.leads.reason_suggest.deepseek.is_configured', lambda: True)
    monkeypatch.setattr('app.leads.reason_suggest.deepseek.completion', boom)

    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    response = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/reason-suggestions', headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'catalog'
    assert body['suggestions']
    assert 'could not be reached' in body['note']


# -- one lead, one campaign, one brokerage ---------------------------------


def test_a_lead_in_a_draft_cannot_be_pulled_into_a_second_draft(
        client, make_user, auth_header, sent_sms, session):
    """The first draft keeps its leads; the second is told which and why."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)

    first = draft(client, headers, ids[:2])['campaign']
    client.patch(f"{CAMPAIGNS_URL}/{first['id']}", headers=headers, json={'name': 'Austin batch'})

    second = draft(client, headers, ids)
    assert second['campaign']['recipients'] == 1
    assert sorted(item['reason'] for item in second['not_added']) == [
        'Already in the draft “Austin batch”.'] * 2

    # The first draft is untouched — this is the failure the guard exists for.
    assert client.get(f"{CAMPAIGNS_URL}/{first['id']}", headers=headers).json()['recipients'] == 2


def test_a_selection_that_is_entirely_spoken_for_is_refused(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    draft(client, headers, ids)

    # No draft is created for a selection with nothing free in it.
    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 400
    assert 'None of the selected leads' in response.json()['detail']
    assert len(client.get(CAMPAIGNS_URL, headers=headers).json()) == 1


def test_discarding_a_draft_frees_its_leads_for_the_next_one(
        client, make_user, auth_header, sent_sms):
    """Discarding is the only way a lead is released, and it must actually work."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)

    first = draft(client, headers, ids)['campaign']['id']
    client.delete(f'{CAMPAIGNS_URL}/{first}', headers=headers)

    second = draft(client, headers, ids)
    assert second['campaign']['recipients'] == 3
    assert second['not_added'] == []


def test_a_sent_campaign_keeps_its_leads_forever(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    first = draft(client, headers, ids)['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=headers)

    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=headers, json={'lead_ids': ids})
    assert response.status_code == 400
    # The sent campaign's numbers cannot be rewritten by a later draft.
    assert client.get(f'{CAMPAIGNS_URL}/{first}', headers=headers).json()['recipients'] == 3


def test_one_brokerage_can_never_campaign_another_s_leads(
        client, make_user, auth_header, sent_sms, session):
    """Ownership comes from the JWT, so another brokerage's leads do not exist here."""
    make_user(BROKER_EMAIL, role='broker')
    make_user('rival@othergroup.net', role='broker',
                  brokerage_id='brokerage-2', brokerage_name='Other Group')
    mine = auth_header(BROKER_EMAIL)
    theirs = auth_header('rival@othergroup.net')
    upload(client, mine)
    my_ids = lead_ids(client, mine)

    # The rival cannot see them, draft them, or reach them by guessing an id.
    assert client.get(LEADS_URL, headers=theirs).json()['leads'] == []
    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=theirs, json={'lead_ids': my_ids})
    assert response.status_code == 400
    assert 'Lead not found.' in response.json()['detail']

    # Nothing was touched, and no text was sent on anyone's behalf.
    assert session.query(Lead).filter(Lead.campaign_id.isnot(None)).count() == 0
    assert sent_sms == []


def test_a_rival_cannot_add_leads_to_a_campaign_by_id(
        client, make_user, auth_header, sent_sms, session):
    """Even holding a real campaign id, the rival owns neither it nor the leads."""
    make_user(BROKER_EMAIL, role='broker')
    make_user('rival@othergroup.net', role='broker',
                  brokerage_id='brokerage-2', brokerage_name='Other Group')
    mine = auth_header(BROKER_EMAIL)
    theirs = auth_header('rival@othergroup.net')
    upload(client, mine)
    campaign_id = draft(client, mine, lead_ids(client, mine))['campaign']['id']

    for path, method in ((f'{CAMPAIGNS_URL}/{campaign_id}', client.get),
                         (f'{CAMPAIGNS_URL}/{campaign_id}/preview', client.get)):
        assert method(path, headers=theirs).status_code == 404
    assert client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=theirs,
                        json={'name': 'stolen'}).status_code == 404
    assert client.delete(f'{CAMPAIGNS_URL}/{campaign_id}', headers=theirs).status_code == 404
    assert client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=mine).json()['name'] != 'stolen'


# -- one pool per brokerage ------------------------------------------------

TEAMMATE = 'colleague@linchpinglobal.net'
RIVAL = 'rival@othergroup.net'


def make_rival(make_user):
    make_user(RIVAL, role='broker', brokerage_id='brokerage-2', brokerage_name='Other Group')


def test_teammates_work_one_shared_pool(client, make_user, auth_header):
    """An agent sees the leads their broker imported: the pool is the brokerage's."""
    make_user(BROKER_EMAIL, role='broker')
    make_user(TEAMMATE, role='agent')
    upload(client, auth_header(BROKER_EMAIL))

    mine = client.get(LEADS_URL, headers=auth_header(BROKER_EMAIL)).json()['leads']
    theirs = client.get(LEADS_URL, headers=auth_header(TEAMMATE)).json()['leads']
    assert [item['id'] for item in mine] == [item['id'] for item in theirs]
    assert len(theirs) == 3


def test_a_teammate_cannot_re_campaign_a_lead_someone_else_drafted(
        client, make_user, auth_header, sent_sms):
    """The exclusivity rule holds across the team, which is the point of sharing."""
    make_user(BROKER_EMAIL, role='broker')
    make_user(TEAMMATE, role='agent')
    broker, agent = auth_header(BROKER_EMAIL), auth_header(TEAMMATE)
    upload(client, broker)
    ids = lead_ids(client, broker)

    draft(client, broker, ids)
    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=agent, json={'lead_ids': ids})
    assert response.status_code == 400
    assert 'None of the selected leads' in response.json()['detail']


def test_a_rival_brokerage_still_sees_none_of_it(client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    make_user(TEAMMATE, role='agent')
    make_rival(make_user)
    upload(client, auth_header(BROKER_EMAIL))
    campaign_id = draft(client, auth_header(BROKER_EMAIL),
                        lead_ids(client, auth_header(BROKER_EMAIL)))['campaign']['id']

    theirs = auth_header(RIVAL)
    assert client.get(LEADS_URL, headers=theirs).json()['leads'] == []
    assert client.get(CAMPAIGNS_URL, headers=theirs).json() == []
    assert client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=theirs).status_code == 404
    assert client.get('/api/v1/followups', headers=theirs).json() == []


# -- one number, one brokerage ---------------------------------------------

SHARED_NUMBER_CSV = (
    'Owner Name,Phone,Property Address,City,Signals\n'
    'Marcus Webb,+13125550142,4517 W Adams St,Austin,Expired\n'
)


def test_the_first_brokerage_to_campaign_a_property_locks_it(
        client, make_user, auth_header, sent_sms):
    """Both bought the same list; only the one who texted first may keep going."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, mine, SHARED_NUMBER_CSV)
    upload(client, theirs, SHARED_NUMBER_CSV)

    first = draft(client, mine, lead_ids(client, mine))['campaign']['id']
    assert client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=mine).status_code == 200
    assert len(sent_sms) == 1

    # The rival's own lead row is untouched, but it can no longer be campaigned.
    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=theirs,
                           json={'lead_ids': lead_ids(client, theirs)})
    assert response.status_code == 400
    assert 'Another brokerage is already contacting this property.' in response.json()['detail']
    assert len(sent_sms) == 1


def test_an_unsent_draft_reserves_no_property_across_brokerages(
        client, make_user, auth_header, sent_sms):
    """A draft that may never send must not block a rival who is ready to go."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, mine, SHARED_NUMBER_CSV)
    upload(client, theirs, SHARED_NUMBER_CSV)

    draft(client, mine, lead_ids(client, mine))  # drafted, never sent
    rival_draft = draft(client, theirs, lead_ids(client, theirs))
    assert rival_draft['campaign']['recipients'] == 1
    assert client.post(f"{CAMPAIGNS_URL}/{rival_draft['campaign']['id']}/send",
                       headers=theirs).status_code == 200
    assert len(sent_sms) == 1


def test_a_property_claimed_after_drafting_is_dropped_at_send(
        client, make_user, auth_header, sent_sms):
    """The claim is re-checked per lead, so a mid-flight claim still wins."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, mine, SHARED_NUMBER_CSV)
    upload(client, theirs, SHARED_NUMBER_CSV)

    # Both draft while the number is free.
    slow = draft(client, mine, lead_ids(client, mine))['campaign']['id']
    quick = draft(client, theirs, lead_ids(client, theirs))['campaign']['id']

    client.post(f'{CAMPAIGNS_URL}/{quick}/send', headers=theirs)
    result = client.post(f'{CAMPAIGNS_URL}/{slow}/send', headers=mine).json()
    assert result['started'] == []
    assert [row['reason'] for row in result['skipped']] == [
        'Another brokerage is already contacting this property.']
    assert len(sent_sms) == 1


def test_a_manual_sms_thread_is_not_a_campaign_claim(
        client, make_user, auth_header, sent_sms):
    """Only a campaign locks a number; a hand-started thread does not."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, theirs, SHARED_NUMBER_CSV)

    started = client.post('/api/v1/sms/conversations', headers=mine, json={
        'contact': '+13125550142',
        'name': 'Marcus Webb',
        'property_address': '4517 W Adams St, Austin, TX',
        'outreach_reason': 'The listing came off the market',
        'ai_enabled': True,
    })
    assert started.status_code == 201, started.text

    rival_draft = draft(client, theirs, lead_ids(client, theirs))
    assert rival_draft['campaign']['recipients'] == 1


# -- the claim is the property, not the owner ------------------------------

LANDLORD_CSV = (
    'Owner Name,Phone,Property Address,City,Signals\n'
    'Dana Whitfield,+13125550142,"415 Northview Lane, Hoffman Estates, IL, 60169",Hoffman Estates,Expired\n'
    'Dana Whitfield,+13125550142,"22 Rosewood Ct, Hoffman Estates, IL, 60169",Hoffman Estates,Expired\n'
)


def test_the_same_address_with_a_different_phone_is_a_separate_contact(
        client, make_user, auth_header, sent_sms):
    """The same house may have multiple owners, each at a different number."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, mine,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'Dana Whitfield,+13125550142,"415 northview lane Hoffman Estates, IL, United States, 60169",Hoffman Estates,Expired\n')
    # A different owner and number, the same house, spelled the vendor's way.
    upload(client, theirs,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'D Whitfield,+13125559999,"415 Northview Ln, Hoffman Estates IL 60169",Hoffman Estates,Expired\n')

    first = draft(client, mine, lead_ids(client, mine))['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=mine)

    rival_draft = draft(client, theirs, lead_ids(client, theirs))
    assert rival_draft['campaign']['recipients'] == 1
    assert client.post(f"{CAMPAIGNS_URL}/{rival_draft['campaign']['id']}/send",
                       headers=theirs).status_code == 200
    assert len(sent_sms) == 2


def test_same_brokerage_can_campaign_same_property_at_two_numbers(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'Dana Whitfield,+13125550142,"415 Northview Lane, Hoffman Estates, IL, 60169",Hoffman Estates,Expired\n'
           'Alex Whitfield,+13125559999,"415 Northview Ln, Hoffman Estates IL 60169",Hoffman Estates,Expired\n')
    ids = lead_ids(client, headers)
    first = draft(client, headers, [ids[0]])['campaign']['id']
    assert client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=headers).status_code == 200

    second = draft(client, headers, [ids[1]])
    assert second['campaign']['recipients'] == 1
    assert client.post(f"{CAMPAIGNS_URL}/{second['campaign']['id']}/send",
                       headers=headers).status_code == 200
    assert len(sent_sms) == 2


def test_a_landlord_s_other_property_is_free_for_another_brokerage(
        client, make_user, auth_header, sent_sms):
    """The claim is the house, so one owner is not locked up by one brokerage."""
    make_user(BROKER_EMAIL, role='broker')
    make_rival(make_user)
    mine, theirs = auth_header(BROKER_EMAIL), auth_header(RIVAL)
    upload(client, mine, LANDLORD_CSV.rsplit('Dana Whitfield,+13125550142,"22', 1)[0])
    upload(client, theirs,
           'Owner Name,Phone,Property Address,City,Signals\n'
           'Dana Whitfield,+13125550142,"22 Rosewood Ct, Hoffman Estates, IL, 60169",Hoffman Estates,Expired\n')

    first = draft(client, mine, lead_ids(client, mine))['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=mine)

    # Same owner, same number, different house — the rival may work it.
    rival_draft = draft(client, theirs, lead_ids(client, theirs))
    assert rival_draft['campaign']['recipients'] == 1
    assert client.post(f"{CAMPAIGNS_URL}/{rival_draft['campaign']['id']}/send",
                       headers=theirs).status_code == 200
    assert len(sent_sms) == 2


def test_one_owner_two_properties_is_two_messages_in_one_thread(
        client, make_user, auth_header, sent_sms, session):
    """A second thread would swallow the first one's replies — see _send_to_lead."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, LANDLORD_CSV)
    northview = lead_id_for(client, headers, '415 Northview Lane')
    rosewood = lead_id_for(client, headers, '22 Rosewood Ct')

    first = draft(client, headers, [northview])['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=headers)
    second = draft(client, headers, [rosewood])['campaign']['id']
    result = client.post(f'{CAMPAIGNS_URL}/{second}/send', headers=headers).json()

    # The owner was texted about both, and each text names its own property.
    assert len(result['started']) == 1, result
    assert len(sent_sms) == 2
    assert '415 Northview Lane' in sent_sms[0]['text']
    assert '22 Rosewood Ct' in sent_sms[1]['text']

    # One thread, so an inbound reply has somewhere unambiguous to land.
    threads = session.query(Conversation).filter(Conversation.contact == '+13125550142').all()
    assert len(threads) == 1
    # Bobbie is grounded in the property just written about, and still knows the other.
    assert '22 Rosewood Ct' in threads[0].property_address
    assert '415 Northview Lane' in threads[0].lead_context

    followup = client.get('/api/v1/followups?scope=all', headers=headers).json()[0]
    assert followup['has_multiple_properties'] is True
    assert {row['address'] for row in followup['properties']} == {
        '415 Northview Lane, Hoffman Estates, IL, 60169',
        '22 Rosewood Ct, Hoffman Estates, IL, 60169',
    }
    assert all(row['campaign_name'] for row in followup['properties'])


def test_shared_thread_reports_each_delivery_but_reply_only_for_latest_campaign(
        client, make_user, auth_header, sent_sms, session):
    """Counting threads instead of messages would credit one and lose the other."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, LANDLORD_CSV)
    ids = lead_ids(client, headers)

    first = draft(client, headers, [ids[0]])['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{first}/send', headers=headers)
    second = draft(client, headers, [ids[1]])['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{second}/send', headers=headers)

    counts = {row['id']: row for row in client.get(CAMPAIGNS_URL, headers=headers).json()}
    assert counts[first]['delivered'] == 1
    assert counts[second]['delivered'] == 1
    assert counts[first]['replied'] == 0 and counts[second]['replied'] == 0

    # One reply must not inflate the conversion metrics of both campaigns.
    owner_replies(client, '+13125550142')
    counts = {row['id']: row for row in client.get(CAMPAIGNS_URL, headers=headers).json()}
    assert counts[first]['replied'] == 0
    assert counts[second]['replied'] == 1


# -- a lead lives in one place ---------------------------------------------


def test_a_drafted_lead_leaves_the_pool_and_appears_under_its_campaign(
        client, make_user, auth_header, sent_sms):
    """One row in two places is a row two people can pick up at once."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    taken, left = ids[0], ids[1:]

    campaign_id = draft(client, headers, [taken])['campaign']['id']

    pool = client.get(LEADS_URL, headers=headers).json()
    assert sorted(item['id'] for item in pool['leads']) == sorted(left)
    assert pool['facets']['total'] == 2

    listed = client.get(f'{CAMPAIGNS_URL}/{campaign_id}',
                        headers=headers).json()['preview']['recipients']
    assert [item['lead_id'] for item in listed] == [taken]


def test_a_sent_campaign_lists_what_each_owner_actually_received(
        client, make_user, auth_header, sent_sms, session):
    """Re-rendering would be a guess; the stored message is what they got."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)

    # Editing the template afterwards must not rewrite the record.
    body = client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers).json()
    listed = body['preview']['recipients']
    assert len(listed) == 3
    assert all(item['conversation_id'] is not None for item in listed)
    assert all(item['replied'] is False for item in listed)
    assert {item['text'] for item in listed} == {item['text'] for item in sent_sms}

    # A reply shows against the owner who sent it, and nobody else.
    answered = listed[0]
    conversation = session.query(Conversation).filter(
        Conversation.id == answered['conversation_id']).first()
    owner_replies(client, conversation.contact)

    listed = client.get(f'{CAMPAIGNS_URL}/{campaign_id}',
                        headers=headers).json()['preview']['recipients']
    by_lead = {item['lead_id']: item for item in listed}
    assert by_lead[answered['lead_id']]['replied'] is True
    assert sum(1 for item in listed if item['replied']) == 1


def test_a_lead_the_send_could_not_reach_goes_back_to_the_pool(
        client, make_user, auth_header, sent_sms, session, monkeypatch):
    """It was never contacted, so it must stay workable rather than vanish."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    campaign_id = draft(client, headers, ids)['campaign']['id']

    from app.sms import service as sms_service

    calls = {'n': 0}

    def flaky(payload):
        calls['n'] += 1
        if calls['n'] == 2:
            raise sms_service.SmsDeliveryError('Carrier rejected the number.')
        return {'data': {'id': f"sim-{calls['n']}"}}

    monkeypatch.setattr('app.sms.service.send_sms', flaky)
    result = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers).json()
    assert len(result['started']) == 2
    assert len(result['skipped']) == 1

    stranded = result['skipped'][0]['lead_id']
    pool = [item['id'] for item in client.get(LEADS_URL, headers=headers).json()['leads']]
    assert stranded in pool

    # And the campaign reports only who it really reached.
    row = client.get(CAMPAIGNS_URL, headers=headers).json()[0]
    assert row['recipients'] == 2 and row['delivered'] == 2 and row['not_sent'] == 0


def test_discarding_a_draft_puts_its_leads_back_in_the_pool(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    ids = lead_ids(client, headers)
    campaign_id = draft(client, headers, ids)['campaign']['id']
    assert client.get(LEADS_URL, headers=headers).json()['leads'] == []

    client.delete(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers)
    assert sorted(item['id'] for item in
                  client.get(LEADS_URL, headers=headers).json()['leads']) == sorted(ids)


# -- only "Ready for Outreach" joins a campaign ----------------------------


def test_a_mixed_selection_takes_only_the_leads_that_can_be_texted(
        client, make_user, auth_header, sent_sms, session):
    """Select the whole page; the campaign takes the contactable ones."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers, CSV + 'No Number,,900 W Nowhere Ave,Austin,FSBO\n')

    by_name = {lead.owner_name: lead for lead in session.query(Lead).all()}
    by_name['Marcus Webb'].dnc = True
    session.commit()

    body = draft(client, headers, [lead.id for lead in by_name.values()])
    assert sorted(item['owner_name'] for item in body['campaign']['preview']['recipients']) == [
        'Emeka Obi', 'Priya Nguyen']
    assert {item['owner_name']: item['reason'] for item in body['not_added']} == {
        'Marcus Webb': 'On the do-not-contact list.',
        'No Number': 'Needs review — no usable phone number.',
    }

    # The refused ones stay in the pool, where their stage explains why.
    pool = {item['owner_name']: item['stage']
            for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert pool == {'Marcus Webb': 'dnc', 'No Number': 'needs_review'}


def test_a_lead_can_be_campaigned_the_moment_it_is_imported(
        client, make_user, auth_header, sent_sms, session):
    """One stage: a contactable lead is Ready for Outreach as soon as it lands."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)

    stages = {item['stage'] for item in client.get(LEADS_URL, headers=headers).json()['leads']}
    assert stages == {'ready'}

    fresh = draft(client, headers, lead_ids(client, headers))
    assert fresh['campaign']['recipients'] == 3
    assert fresh['not_added'] == []
    assert len(client.post(f"{CAMPAIGNS_URL}/{fresh['campaign']['id']}/send",
                           headers=headers).json()['started']) == 3


def test_group_reason_precedence_and_needs_review(make_user, session):
    owner = make_user(BROKER_EMAIL, role='broker')
    campaign = Campaign(
        user_id=owner.id,
        name='Grouped reasons',
        message_template='About {{address}}. {{reason}}',
        reason_groups=json.dumps({'version': 1, 'groups': {
            'expired': {'reason': 'Confirmed expired reason', 'confirmed': True},
        }}),
        status='draft',
    )
    session.add(campaign)
    session.flush()
    leads = [
        Lead(user_id=owner.id, owner_name='Confirmed', phone='+13125551001',
             property_address='1 Main St', signals='expired,high_equity',
             main_signal='expired', main_signal_basis='broker_selected',
             outreach_reason='Vendor reason', campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Vendor', phone='+13125551002',
             property_address='2 Main St', signals='expired,high_equity',
             outreach_reason='Vendor reason', campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Catalog', phone='+13125551003',
             property_address='3 Main St', signals='high_equity',
             main_signal='high_equity', main_signal_basis='single_signal',
             campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Review', phone='+13125551004',
             property_address='4 Main St', signals='auction,bankruptcy',
             campaign_id=campaign.id),
    ]
    session.add_all(leads)
    session.commit()

    assert campaign_service.resolve_reason(campaign, leads[0]) == (
        'Confirmed expired reason', 'confirmed_group', False,
    )
    assert campaign_service.resolve_reason(campaign, leads[1]) == (
        'Vendor reason', 'vendor', False,
    )
    assert campaign_service.resolve_reason(campaign, leads[2])[1:] == ('catalog', False)
    assert campaign_service.resolve_reason(campaign, leads[3]) == ('', 'needs_review', True)
    rows = campaign_service.preview(session, owner, campaign)['recipients']
    by_name = {row['owner_name']: row for row in rows}
    assert 'Vendor reason' in by_name['Confirmed']['text']
    assert 'Vendor reason' in by_name['Vendor']['text']
    assert by_name['Review']['needs_review'] is True
    assert by_name['Review']['text'] == ''


def test_provider_main_signal_and_contact_removal_are_property_atomic(
        make_user, session):
    owner = make_user(BROKER_EMAIL, role='broker')
    phones = [
        {'number': '+13125552001', 'phone': '+13125552001', 'dnc': False,
         'owner_name': 'First Owner', 'consent': 'unknown'},
        {'number': '+13125552002', 'phone': '+13125552002', 'dnc': False,
         'owner_name': 'Second Owner', 'consent': 'express'},
        {'number': '+13125552003', 'phone': '+13125552003', 'dnc': True,
         'owner_name': 'Restricted Owner', 'consent': 'none'},
    ]
    lead = Lead(
        user_id=owner.id, owner_name='Original Owners', phone=phones[0]['phone'],
        property_address='10 Provider Way', signals='expired,high_equity',
        source='provider_distribution', dnc=False,
        details=json.dumps({'_phone_numbers': phones, 'property_type': 'Single Family'}),
    )
    campaign = Campaign(
        user_id=owner.id, name='Provider contacts',
        message_template='Hi {{first_name}}', reason_groups='{"version":1,"groups":{}}',
        status='draft',
    )
    session.add_all([lead, campaign])
    session.commit()
    assert campaign_service.attach(session, owner, campaign, [lead.id]) == []
    family = campaign_service.members(session, owner, campaign)
    assert len(family) == 2
    clone = next(item for item in family if item.source == 'provider_distribution_contact')

    result = campaign_service.assign_main_signal(
        session, owner, campaign, 'expired',
        {'mode': 'ids', 'lead_ids': [clone.id]},
    )
    assert result['updated_properties'] == 1
    assert {(item.main_signal, item.main_signal_basis)
            for item in campaign_service.members(session, owner, campaign)} == {
                ('expired', 'broker_selected')
            }

    session.add(LeadEvent(
        lead_id=clone.id, event_category='activity', event_type='test_history',
    ))
    session.commit()
    original_contact = lead.phone
    campaign_service.remove_recipients(
        session, owner, campaign, {'mode': 'ids', 'lead_ids': [lead.id]},
    )
    session.refresh(lead)
    assert lead.campaign_id == campaign.id
    assert lead.phone != original_contact
    assert session.query(LeadEvent).filter_by(event_type='test_history').one().lead_id == lead.id
    saved = json.loads(lead.details)
    assert saved['property_type'] == 'Single Family'
    assert {item['phone'] for item in saved['_phone_numbers']} == {
        '+13125552001', '+13125552002', '+13125552003',
    }
    assert next(item for item in saved['_phone_numbers']
                if item['phone'] == '+13125552003')['dnc'] is True

    campaign_service.remove_recipients(
        session, owner, campaign, {'mode': 'ids', 'lead_ids': [lead.id]},
    )
    session.refresh(lead)
    assert lead.campaign_id is None
    assert lead.owner_name == 'Original Owners'
    assert lead.phone == original_contact
    assert campaign_service.member_count(session, owner, campaign) == 0
    with pytest.raises(ValueError, match='no recipients'):
        campaign_service.send(session, owner, campaign)


def test_legacy_main_signal_api_does_not_override_campaign_reason_resolution(
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    marcus_id = lead_id_for(client, headers, '4517 W Adams')
    body = draft(client, headers, lead_ids(client, headers))
    campaign_id = body['campaign']['id']

    assigned = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/recipients/main-signal',
        headers=headers,
        params={'preview_page': 1, 'signal': 'high_equity'},
        json={'main_signal': 'absentee_owner',
              'selection': {'mode': 'ids', 'lead_ids': [marcus_id]}},
    )
    assert assigned.status_code == 200, assigned.text
    recipient = next(row for row in assigned.json()['campaign']['preview']['recipients']
                     if row['lead_id'] == marcus_id)
    assert recipient['main_signal'] == 'absentee_owner'
    assert recipient['main_signal_basis'] == 'broker_selected'
    assert assigned.json()['campaign']['preview']['total_items'] == 1

    confirmed = client.patch(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        json={'reason_group': {
            'signal': 'absentee_owner',
            'reason': 'Confirmed group wording',
            'confirmed': True,
        }},
    )
    assert confirmed.status_code == 200, confirmed.text
    recipient = next(row for row in confirmed.json()['preview']['recipients']
                     if row['lead_id'] == marcus_id)
    assert recipient['text'].endswith(
        'Records show the owner lives elsewhere.\n\nReply STOP to Opt-out')

    high_equity_reason = client.patch(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        json={'reason_group': {
            'signal': 'high_equity',
            'reason': 'Confirmed high-equity wording',
            'confirmed': True,
        }},
    )
    assert high_equity_reason.status_code == 200, high_equity_reason.text
    reassigned = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/recipients/main-signal', headers=headers,
        json={'main_signal': 'high_equity',
              'selection': {'mode': 'ids', 'lead_ids': [marcus_id]}},
    )
    assert reassigned.status_code == 200, reassigned.text
    recipient = next(row for row in reassigned.json()['campaign']['preview']['recipients']
                     if row['lead_id'] == marcus_id)
    assert recipient['main_signal'] == 'high_equity'
    assert recipient['text'].endswith(
        'Records show the owner lives elsewhere.\n\nReply STOP to Opt-out')

    reopened = client.get(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers).json()
    recipient = next(row for row in reopened['preview']['recipients']
                     if row['lead_id'] == marcus_id)
    assert recipient['main_signal'] == 'high_equity'
    assert recipient['text'].endswith(
        'Records show the owner lives elsewhere.\n\nReply STOP to Opt-out')

    filtered = client.get(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        params={'signal': 'absentee_owner'},
    ).json()
    assert filtered['preview']['total_items'] == 1
    assert filtered['preview']['recipients'][0]['lead_id'] == marcus_id
    saved_group = next(
        row for row in filtered['preview']['reason_groups']
        if row['key'] == 'absentee_owner'
    )
    assert saved_group['reason'] == 'Confirmed group wording'
    assert saved_group['confirmed'] is True

    # Marcus is a single recipient under either of his valid Lead.signals;
    # filtering never depends on the assigned Main Signal or duplicates him.
    secondary = client.get(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        params={'signal': 'high_equity'},
    ).json()
    assert secondary['preview']['total_items'] == 1
    assert [row['lead_id'] for row in secondary['preview']['recipients']] == [marcus_id]
    options = filtered['preview']['signal_options']
    assert {'key': 'absentee_owner', 'label': 'Absentee Owner'} in options
    assert {'key': 'high_equity', 'label': 'High Equity'} in options
    assert {'key': 'pre_foreclosure', 'label': 'Pre-Foreclosure'} in options
    assert {'key': 'probate', 'label': 'Probate'} in options
    groups = {row['key']: row['count'] for row in filtered['preview']['reason_groups']}
    assert groups == {
        'absentee_owner': 1,
        'high_equity': 1,
        'pre_foreclosure': 1,
        'probate': 1,
    }

    removed = client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/recipients/remove',
        headers=headers,
        json={'selection': {'mode': 'ids', 'lead_ids': [marcus_id]}},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()['campaign']['recipients'] == 2
    assert sent_sms == []


def test_group_reason_is_identical_in_preview_queue_message_and_grounding(
        client, make_user, auth_header, sent_sms, session):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    marcus_id = lead_id_for(client, headers, '4517 W Adams')
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    client.post(
        f'{CAMPAIGNS_URL}/{campaign_id}/recipients/main-signal', headers=headers,
        json={'main_signal': 'absentee_owner',
              'selection': {'mode': 'ids', 'lead_ids': [marcus_id]}},
    )
    updated = client.patch(
        f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
        json={'reason_group': {'signal': 'absentee_owner',
                               'reason': 'One grounded group reason',
                               'confirmed': True}},
    ).json()
    preview_text = next(row['text'] for row in updated['preview']['recipients']
                        if row['lead_id'] == marcus_id)

    response = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)
    assert response.status_code == 200, response.text
    job = session.query(OutboundSmsJob).filter_by(
        campaign_id=campaign_id, lead_id=marcus_id,
    ).one()
    assert job.text == preview_text
    message = session.query(Message).filter_by(id=job.message_id).one()
    assert message.text == preview_text
    lead = session.get(Lead, marcus_id)
    context = json.loads(session.get(Conversation, lead.conversation_id).lead_context)
    stored_campaign = session.get(Campaign, campaign_id)
    assert context['outreach_reason'] == campaign_service.resolve_reason(
        stored_campaign, lead,
    )[0]
    assert any(payload['text'] == preview_text for payload in sent_sms)


def test_reason_signal_frequency_is_per_property_and_per_recipient(make_user, session):
    owner = make_user('frequency@example.test', role='broker')
    campaign = Campaign(
        user_id=owner.id, name='Frequency', status='draft',
        message_template='About {{address}}. {{reason}}',
        reason_groups=json.dumps({'version': 1, 'groups': {
            'expired': {'reason': 'Expired confirmed', 'confirmed': True},
            'probate': {'reason': 'Probate confirmed', 'confirmed': True},
        }}),
    )
    session.add(campaign)
    session.flush()
    leads = []
    for index in range(15):
        leads.append(Lead(
            user_id=owner.id, owner_name=f'Expired {index}',
            phone=f'+1312600{index:04d}', property_address=f'{index} Expired St',
            signals='expired', campaign_id=campaign.id,
        ))
        leads.append(Lead(
            user_id=owner.id, owner_name=f'Probate {index}',
            phone=f'+1312700{index:04d}', property_address=f'{index} Probate St',
            signals='probate', campaign_id=campaign.id,
        ))
    leads.extend([
        Lead(user_id=owner.id, owner_name='Expired Combo', phone='+13128000001',
             property_address='100 Combo St', signals='expired,absentee_owner',
             campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Probate Combo', phone='+13128000002',
             property_address='101 Combo St', signals='probate,pre_foreclosure',
             campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Expired Combo Second Phone',
             phone='+13128000003', property_address='100 Combo St',
             signals='expired,absentee_owner', source='provider_distribution_contact',
             campaign_id=campaign.id),
    ])
    session.add_all(leads)
    session.commit()

    context = campaign_service._campaign_reason_context(session, owner, campaign)
    by_name = {
        lead.owner_name: context['assignments'][campaign_service._property_key(lead)]
        for lead in leads
    }
    assert by_name['Expired Combo'] == 'expired'
    assert by_name['Probate Combo'] == 'probate'
    assert context['frequencies']['expired'] == 16
    assert context['frequencies']['probate'] == 16
    assert context['frequencies']['absentee_owner'] == 1


def test_reason_signal_ties_vendor_fallback_and_snapshot_survive_removal(
        make_user, session):
    owner = make_user('snapshot@example.test', role='broker')
    campaign = Campaign(
        user_id=owner.id, name='Snapshot', status='draft',
        message_template='About {{address}}. {{reason}}',
        reason_groups=json.dumps({'version': 1, 'groups': {
            'expired': {'reason': 'Stable expired reason', 'confirmed': True},
            'auction': {'reason': 'Verified auction reason', 'confirmed': True},
        }}),
    )
    session.add(campaign)
    session.flush()
    leads = [
        Lead(user_id=owner.id, owner_name='Expired Base', phone='+13129000001',
             property_address='1 Base St', signals='expired', campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Stable Multi', phone='+13129000002',
             property_address='2 Base St', signals='expired,absentee_owner',
             campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Verified Tie', phone='+13129000003',
             property_address='3 Base St', signals='auction,bankruptcy',
             main_signal='auction', main_signal_basis='source_verified',
             campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Vendor Tie', phone='+13129000004',
             property_address='4 Base St', signals='divorce,vacant',
             outreach_reason='Verified vendor context', campaign_id=campaign.id),
        Lead(user_id=owner.id, owner_name='Unresolved Tie', phone='+13129000005',
             property_address='5 Base St', signals='fsbo,tax_delinquent',
             campaign_id=campaign.id),
    ]
    session.add_all(leads)
    session.commit()

    context = campaign_service._campaign_reason_context(session, owner, campaign)
    assignment = lambda lead: context['assignments'][campaign_service._property_key(lead)]
    assert assignment(leads[1]) == 'expired'
    assert assignment(leads[2]) == 'auction'
    assert assignment(leads[3]) is None
    assert campaign_service.resolve_reason(campaign, leads[3], None)[1] == 'vendor'
    assert campaign_service.resolve_reason(campaign, leads[4], None)[2] is True

    removed = campaign_service.remove_recipients(
        session, owner, campaign, {'mode': 'unresolved'},
    )
    assert removed['removed_contacts'] == 1
    state = json.loads(campaign.reason_groups)
    snapshot = state['reason_signal_snapshot']
    assert snapshot['finalized'] is True
    assert set(snapshot['properties']) == {
        canonical(lead.property_address) for lead in leads[1:]
    }
    assert snapshot['properties'][canonical(leads[1].property_address)] == 'expired'

    campaign_service.remove_recipients(
        session, owner, campaign, {'mode': 'ids', 'lead_ids': [leads[0].id]},
    )
    session.refresh(campaign)
    assert campaign_service.resolve_reason(campaign, leads[1])[0] == 'Stable expired reason'
    snapshot_before_edit = json.loads(campaign.reason_groups)['reason_signal_snapshot']
    campaign_service._update_reason_group(campaign, {
        'signal': 'expired', 'reason': 'Intentionally edited reason', 'confirmed': True,
    })
    session.commit()
    assert json.loads(campaign.reason_groups)['reason_signal_snapshot'] == snapshot_before_edit
    assert campaign_service.resolve_reason(campaign, leads[1])[0] == 'Intentionally edited reason'


def test_no_signal_ai_and_reason_precedence_are_safe(monkeypatch, make_user, session):
    owner = make_user('neutral@example.test', role='broker')
    campaign = Campaign(
        user_id=owner.id, name='Neutral', status='draft',
        message_template='About {{address}}. {{reason}}',
        reason_groups=json.dumps({'version': 1, 'groups': {
            campaign_service.NO_SIGNAL_REASON_KEY: {
                'reason': 'Would you consider selling the property?', 'confirmed': True,
            },
        }}),
    )
    session.add(campaign)
    session.flush()
    vendor = Lead(
        user_id=owner.id, owner_name='Vendor', phone='+13129990001',
        property_address='1 Neutral St', signals='',
        outreach_reason='Verified vendor reason', campaign_id=campaign.id,
    )
    neutral = Lead(
        user_id=owner.id, owner_name='Neutral', phone='+13129990002',
        property_address='2 Neutral St', signals='', campaign_id=campaign.id,
    )
    session.add_all([vendor, neutral])
    session.commit()

    assert campaign_service.resolve_reason(
        campaign, vendor, campaign_service.NO_SIGNAL_REASON_KEY,
    )[:2] == ('Verified vendor reason', 'vendor')
    assert campaign_service.resolve_reason(
        campaign, neutral, campaign_service.NO_SIGNAL_REASON_KEY,
    )[:2] == ('Would you consider selling the property?', 'confirmed_group')
    no_signal_group = next(
        group for group in campaign_service.preview(session, owner, campaign)['reason_groups']
        if group['key'] == campaign_service.NO_SIGNAL_REASON_KEY
    )
    assert no_signal_group['label'] == 'No Signal'
    assert no_signal_group['count'] == 2

    monkeypatch.setattr(reason_suggest.deepseek, 'is_configured', lambda: True)
    monkeypatch.setattr(reason_suggest.deepseek, 'completion', lambda *_args, **_kwargs: {
        'content': json.dumps({'suggestions': [
            'Would you consider selling the property?',
            'Are you open to discussing a possible sale?',
            'Could we talk about whether selling may interest you?',
        ]}),
    })
    suggestions = reason_suggest.suggest(
        session, owner, campaign, signal=campaign_service.NO_SIGNAL_REASON_KEY,
    )
    assert len(suggestions['options']) == 3
    assert {option['source'] for option in suggestions['options']} == {'ai'}
    assert all('listed' not in option['text'].lower() for option in suggestions['options'])

    def unavailable(*_args, **_kwargs):
        raise reason_suggest.deepseek.AiUnavailableError('offline')
    monkeypatch.setattr(reason_suggest.deepseek, 'completion', unavailable)
    retry = reason_suggest.suggest(
        session, owner, campaign, signal=campaign_service.NO_SIGNAL_REASON_KEY,
    )
    assert retry['options'] == []
    assert retry['suggestions'] == []
    assert retry['note'] == 'AI could not be reached. Retry Suggest with AI.'


def test_missing_reason_does_not_block_or_remove_when_template_has_no_reason_token(
        make_user, session):
    owner = make_user('no-token@example.test', role='broker')
    campaign = Campaign(
        user_id=owner.id, name='No token', status='draft',
        message_template='Hello {{first_name}}, about {{address}}.',
        reason_groups='{"version":1,"groups":{}}',
    )
    session.add(campaign)
    session.flush()
    lead = Lead(
        user_id=owner.id, owner_name='No Reason', phone='+13129990003',
        property_address='3 Neutral St', signals='auction,bankruptcy',
        campaign_id=campaign.id,
    )
    session.add(lead)
    session.commit()
    assert campaign_service.preview(session, owner, campaign)['needs_review_count'] == 0
    with pytest.raises(ValueError, match='No campaign recipients'):
        campaign_service.remove_recipients(
            session, owner, campaign, {'mode': 'unresolved'},
        )

def test_the_campaign_table_carries_what_the_pool_table_showed(
        client, make_user, auth_header, sent_sms):
    """The campaign is where these leads live now, so it shows the same columns."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    pool = {item['owner_name']: item
            for item in client.get(LEADS_URL, headers=headers).json()['leads']}

    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    listed = client.get(f'{CAMPAIGNS_URL}/{campaign_id}',
                        headers=headers).json()['preview']['recipients']

    for row in listed:
        was = pool[row['owner_name']]
        assert row['property_address'] == was['property_address']
        assert row['area'] == was['area']
        assert row['score'] == was['score']
        assert [s['label'] for s in row['signals']] == [s['label'] for s in was['signals']]
