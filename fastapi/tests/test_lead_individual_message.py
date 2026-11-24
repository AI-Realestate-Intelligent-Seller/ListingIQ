"""Texting one lead directly from the Lead Pool.

Every message written this way belongs to the brokerage's single
'Individually contacted' campaign, and a lead with several numbers is reached
on each one the broker chose.
"""

import json

import pytest

from app import db
from app.leads import campaign as campaign_service
from app.models import Campaign, Conversation, Lead, Message

BROKER_EMAIL = 'pool.broker@linchpinglobal.net'
LEADS_URL = '/api/v1/leads'


@pytest.fixture
def sent_sms(monkeypatch):
    outbox = []

    def fake_send(payload):
        outbox.append(payload)
        return {'data': {'id': f'sim-{len(outbox)}'}}

    monkeypatch.setattr('app.sms.service.send_sms', fake_send)
    return outbox


@pytest.fixture
def broker(make_user):
    return make_user(BROKER_EMAIL, role='hob')


def make_lead(user_id, numbers, owner_name='Marcus Webb',
              address='4517 W Adams St, Austin, TX 78702'):
    """One pool lead carrying every number in `numbers` (phone, dnc) pairs."""
    primary, *rest = numbers
    session = db.SessionLocal()
    try:
        lead = Lead(
            user_id=user_id,
            owner_name=owner_name,
            phone=primary[0],
            dnc=primary[1],
            property_address=address,
            area='Austin',
            signals='absentee_owner',
            score=70,
            source='csv_import',
            details=json.dumps({'_phone_numbers': [
                {'phone': phone, 'dnc': dnc} for phone, dnc in numbers
            ]}) if rest else None,
        )
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return lead.id
    finally:
        session.close()


def test_one_message_reaches_every_number_under_one_category(
    client, auth_header, broker, sent_sms, session,
):
    lead_id = make_lead(broker.id, [('+13125550142', False), ('+13125550188', False)])
    headers = auth_header(BROKER_EMAIL)

    response = client.post(
        f'{LEADS_URL}/{lead_id}/message',
        headers=headers,
        json={'text': 'Hi {{first_name}}, are you open to an offer on {{address}}?'},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['campaign_name'] == campaign_service.INDIVIDUAL_CAMPAIGN_NAME
    assert body['skipped'] == []
    assert sorted(row['phone'] for row in body['started']) == [
        '+13125550142', '+13125550188',
    ]
    # The broker's own wording is sent, with this lead's values filled in.
    assert body['text'] == 'Hi Marcus, are you open to an offer on 4517 W Adams St, Austin, TX 78702?'
    assert sorted(message['to'] for message in sent_sms) == [
        '+13125550142', '+13125550188',
    ]

    # One thread per number, both filed under the same reserved campaign.
    campaign = session.query(Campaign).filter(
        Campaign.kind == campaign_service.INDIVIDUAL_KIND).one()
    threads = session.query(Conversation).filter(
        Conversation.campaign_id == campaign.id).all()
    assert sorted(thread.contact for thread in threads) == [
        '+13125550142', '+13125550188',
    ]
    messages = session.query(Message).filter(Message.campaign_id == campaign.id).all()
    assert len(messages) == 2
    assert {message.event_type for message in messages} == {'outreach.initial'}
    # The lead row keeps pointing at the thread for its own number.
    lead = session.get(Lead, lead_id)
    assert lead.phone == '+13125550142'
    assert lead.campaign_id == campaign.id
    assert lead.conversation_id == next(
        thread.id for thread in threads if thread.contact == '+13125550142')


def test_a_second_message_reuses_the_same_category(
    client, auth_header, broker, sent_sms, session,
):
    first = make_lead(broker.id, [('+13125550142', False)])
    second = make_lead(broker.id, [('+13125550155', False)], owner_name='Priya Nguyen',
                       address='2210 S Sacramento Dr, Austin, TX 78702')
    headers = auth_header(BROKER_EMAIL)

    assert client.post(f'{LEADS_URL}/{first}/message', headers=headers,
                       json={'text': 'First direct note.'}).status_code == 200
    assert client.post(f'{LEADS_URL}/{second}/message', headers=headers,
                       json={'text': 'Second direct note.'}).status_code == 200

    campaigns = session.query(Campaign).filter(
        Campaign.kind == campaign_service.INDIVIDUAL_KIND).all()
    assert len(campaigns) == 1
    assert campaigns[0].status == 'sent'


def test_an_owner_with_a_conversation_cannot_be_messaged_again(
    client, auth_header, broker, sent_sms, session,
):
    lead_id = make_lead(broker.id, [('+13125550142', False)])
    headers = auth_header(BROKER_EMAIL)
    assert client.post(f'{LEADS_URL}/{lead_id}/message', headers=headers,
                       json={'text': 'First direct note.'}).status_code == 200

    again = client.post(f'{LEADS_URL}/{lead_id}/message', headers=headers,
                        json={'text': 'And again.'})

    assert again.status_code == 400
    assert again.json()['detail'] == campaign_service.OWNER_CONTACTED_HERE
    # Only the first message was ever sent.
    assert [message['to'] for message in sent_sms] == ['+13125550142']


def test_an_owner_contacted_at_one_property_leaves_the_pool_for_the_others(
    client, auth_header, broker, sent_sms, session,
):
    """One person, two properties, two lead rows: texting once covers both.

    Only the row that was texted carries the campaign, so the other row has to
    be kept out of the pool by the owner's number instead.
    """
    texted = make_lead(broker.id, [('+13125550142', False), ('+13125550188', False)])
    other_property = make_lead(
        broker.id, [('+13125550142', False)],
        address='908 E 12th St, Austin, TX 78702')
    headers = auth_header(BROKER_EMAIL)

    assert client.post(f'{LEADS_URL}/{texted}/message', headers=headers,
                       json={'text': 'Hello about your place.'}).status_code == 200

    pool = client.get(LEADS_URL, headers=headers)
    assert pool.status_code == 200, pool.text
    assert [lead['id'] for lead in pool.json()['leads']] == []

    # And the second property cannot be used as a way back in.
    blocked = client.post(f'{LEADS_URL}/{other_property}/message', headers=headers,
                          json={'text': 'What about this one?'})
    assert blocked.status_code == 400
    assert blocked.json()['detail'] == campaign_service.OWNER_CONTACTED_HERE


def test_an_owner_reached_at_a_second_number_is_still_one_person(
    client, auth_header, broker, sent_sms, session,
):
    """A lead row carrying a number we have already texted is contacted too."""
    texted = make_lead(broker.id, [('+13125550142', False)])
    headers = auth_header(BROKER_EMAIL)
    assert client.post(f'{LEADS_URL}/{texted}/message', headers=headers,
                       json={'text': 'Hello about your place.'}).status_code == 200

    # Same owner, a different address, listed under a new primary number but
    # with the number we texted among their other numbers.
    second = make_lead(broker.id, [('+13125550199', False), ('+13125550142', False)],
                       address='908 E 12th St, Austin, TX 78702')
    blocked = client.post(f'{LEADS_URL}/{second}/message', headers=headers,
                          json={'text': 'And this one?'})

    assert blocked.status_code == 400
    assert blocked.json()['detail'] == campaign_service.OWNER_CONTACTED_HERE
    pool = client.get(LEADS_URL, headers=headers)
    assert second not in [lead['id'] for lead in pool.json()['leads']]


def test_chosen_numbers_only_and_do_not_contact_is_refused(
    client, auth_header, broker, sent_sms, session,
):
    lead_id = make_lead(broker.id, [
        ('+13125550142', False), ('+13125550188', False), ('+13125550100', True),
    ])
    headers = auth_header(BROKER_EMAIL)

    response = client.post(
        f'{LEADS_URL}/{lead_id}/message',
        headers=headers,
        json={'text': 'Only this number, please.',
              'phones': ['+13125550188', '+13125550100']},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row['phone'] for row in body['started']] == ['+13125550188']
    assert [row['phone'] for row in body['skipped']] == ['+13125550100']
    assert body['skipped'][0]['reason'] == 'On the do-not-contact list.'
    assert [message['to'] for message in sent_sms] == ['+13125550188']


def test_a_lead_with_only_a_blocked_number_is_rejected(
    client, auth_header, broker, sent_sms,
):
    lead_id = make_lead(broker.id, [('+13125550100', True)])
    response = client.post(
        f'{LEADS_URL}/{lead_id}/message',
        headers=auth_header(BROKER_EMAIL),
        json={'text': 'Anyone there?'},
    )
    assert response.status_code == 400
    assert 'do-not-contact' in response.json()['detail']
    assert sent_sms == []


def test_another_brokerage_cannot_message_this_pool(
    client, auth_header, broker, make_user, sent_sms,
):
    lead_id = make_lead(broker.id, [('+13125550142', False)])
    make_user('rival@other-brokerage.net', role='hob', brokerage_id='brokerage-2')
    response = client.post(
        f'{LEADS_URL}/{lead_id}/message',
        headers=auth_header('rival@other-brokerage.net'),
        json={'text': 'Poaching attempt.'},
    )
    assert response.status_code == 404
    assert sent_sms == []
