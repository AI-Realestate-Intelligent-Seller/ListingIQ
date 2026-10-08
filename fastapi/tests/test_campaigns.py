"""Tests for campaigns: drafting, the message template, sending and reporting."""

import json

import pytest

from datetime import datetime

from app.leads import campaign as campaign_service
from app.leads import template as message_template
from app.leads.address import canonical
from app.models import Campaign, Conversation, Lead, Message, User
from app.sms import service

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'
LEADS_URL = '/api/v1/leads'
CAMPAIGNS_URL = '/api/v1/campaigns'

CSV = (
    'Owner Name,Phone,Property Address,City,Signals\n'
    'Marcus Webb,(312) 555-0142,4517 W Adams St,Austin,"Absentee Owner, High Equity"\n'
    'Priya Nguyen,+13125550188,2210 S Sacramento Dr,Little Village,Pre-Foreclosure\n'
    'Emeka Obi,3125550199,7745 S Coles Ave,South Shore,Probate\n'
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
        'listed, have you thought about putting it back on the market?'
    )


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


def test_an_invented_placeholder_is_refused_before_anything_is_sent():
    try:
        message_template.validate('Hi {{first_name}}, about {{property}}.')
    except ValueError as error:
        assert '{{property}}' in str(error)
    else:
        raise AssertionError('An unknown placeholder must not validate.')


# -- drafting --------------------------------------------------------------


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
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

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
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

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

    response = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['started']) == 3
    # What the broker read in the preview is exactly what went out.
    assert {item['lead_id']: item['text'] for item in result['started']} == previewed
    assert len(sent_sms) == 3

    # Campaign threads start user-handled; Bobbie can be handed the thread later.
    conversations = client.get('/api/v1/sms/conversations', headers=headers).json()
    assert all(item['handled_by'] == 'broker' for item in conversations)
    assert all(item['ai_enabled'] is False for item in conversations)
    assert session.query(Conversation).filter(
        Conversation.campaign_id == campaign_id).count() == 3


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
    assert campaign['no_reply'] == 2
    assert campaign['not_sent'] == 1


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
        client, make_user, auth_header, sent_sms):
    """One set, one story: the broker's sentence replaces the per-signal wording."""
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']

    shared = ('I see that your property is no longer listed, have you thought about '
              'putting it back on the market?')
    body = client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                        json={'outreach_reason': shared}).json()
    assert body['outreach_reason'] == shared
    # Every recipient now ends on the same sentence, with their own address before it.
    assert all(item['text'].endswith(shared) for item in body['preview']['recipients'])
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
        client, make_user, auth_header, sent_sms):
    make_user(BROKER_EMAIL, role='broker')
    headers = auth_header(BROKER_EMAIL)
    upload(client, headers)
    campaign_id = draft(client, headers, lead_ids(client, headers))['campaign']['id']
    shared = 'I noticed the listing came off the market — are you still open to selling?'
    client.patch(f'{CAMPAIGNS_URL}/{campaign_id}', headers=headers,
                 json={'outreach_reason': shared})

    started = client.post(f'{CAMPAIGNS_URL}/{campaign_id}/send', headers=headers).json()['started']
    assert all(row['text'].endswith(shared) for row in started)
    assert all(text['text'].endswith(shared) for text in sent_sms)


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
