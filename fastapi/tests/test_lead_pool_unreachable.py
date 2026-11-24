"""The Lead Pool's two Unreachable filters.

A lead that cannot be texted as it stands is listed beside the pool rather than
inside it, and no campaign, experiment or individual message will take one.
"""

import pytest

from app.leads import campaign as campaign_service
from app.models import Lead

BROKER_EMAIL = 'unreachable.broker@linchpinglobal.net'
LEADS_URL = '/api/v1/leads'
CAMPAIGNS_URL = '/api/v1/campaigns'


@pytest.fixture
def sent_sms(monkeypatch):
    outbox = []
    monkeypatch.setattr('app.sms.service.send_sms',
                        lambda payload: outbox.append(payload) or {'data': {'id': 'sim-1'}})
    return outbox


@pytest.fixture
def leads(session, make_user):
    """One workable lead, one landline, one with no property address."""
    owner = make_user(BROKER_EMAIL, role='broker')
    rows = [
        Lead(user_id=owner.id, owner_name='Ready Rhodes', phone='+13125550101',
             property_address='1 Ready Ave, Austin, TX 78702', signals='expired'),
        # Telnyx answered for this very number, so the verdict stands.
        Lead(user_id=owner.id, owner_name='Landline Lopez', phone='+13125550102',
             property_address='2 Landline Ave, Austin, TX 78702', signals='expired',
             phone_line_type='fixed line', phone_lookup_number='+13125550102'),
        Lead(user_id=owner.id, owner_name='Nowhere Nguyen', phone='+13125550103',
             property_address=None, signals='expired'),
    ]
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def test_the_pool_holds_only_workable_leads_and_the_filters_hold_the_rest(
    client, auth_header, leads,
):
    headers = auth_header(BROKER_EMAIL)
    ready, landline, no_address = leads

    pool = client.get(LEADS_URL, headers=headers)
    assert pool.status_code == 200, pool.text
    body = pool.json()
    assert [lead['id'] for lead in body['leads']] == [ready.id]
    assert body['facets']['total'] == 1
    # The counts sit beside the pool, so neither list hides the other.
    assert body['unreachable'] == {
        'total': 2, 'stages': {'not_mobile': 1, 'no_address': 1},
    }

    for stage, expected in (('not_mobile', landline), ('no_address', no_address)):
        view = client.get(LEADS_URL, headers=headers, params={'stage': stage})
        assert view.status_code == 200, view.text
        rows = view.json()['leads']
        assert [lead['id'] for lead in rows] == [expected.id], stage
        assert rows[0]['stage'] == stage


def test_filling_in_the_address_returns_the_lead_to_the_pool(
    client, auth_header, session, leads,
):
    """The data gap is the broker's to close, so it has to be reversible."""
    headers = auth_header(BROKER_EMAIL)
    _ready, _landline, no_address = leads

    no_address.property_address = '3 Found Ave, Austin, TX 78702'
    session.commit()

    body = client.get(LEADS_URL, headers=headers).json()
    assert no_address.id in [lead['id'] for lead in body['leads']]
    assert body['unreachable']['stages']['no_address'] == 0


def test_a_campaign_draft_refuses_both_kinds(client, auth_header, leads):
    headers = auth_header(BROKER_EMAIL)
    ready, landline, no_address = leads

    response = client.post(f'{CAMPAIGNS_URL}/draft', headers=headers,
                           json={'lead_ids': [lead.id for lead in leads]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['campaign']['recipients'] == 1
    refusals = {row['lead_id']: row['reason'] for row in body['not_added']}
    assert refusals == {
        landline.id: campaign_service.STAGE_REFUSAL['not_mobile'],
        no_address.id: campaign_service.STAGE_REFUSAL['no_address'],
    }


def test_an_experiment_variant_refuses_both_kinds(
    client, auth_header, session, make_user, leads,
):
    headers = auth_header(BROKER_EMAIL)
    ready, landline, no_address = leads
    second = Lead(user_id=ready.user_id, owner_name='Ready Two', phone='+13125550104',
                  property_address='4 Ready Ave, Austin, TX 78702', signals='expired')
    session.add(second)
    session.commit()
    session.refresh(second)

    response = client.post('/api/v1/experiments', headers=headers, json={
        'name': 'Unreachable leads stay out',
        'goal_notes': 'Compare replies.',
        'test_dimension': 'tone',
        'winner_metric': 'reply_rate',
        'allocation_mode': 'balanced',
        'timezone': 'UTC',
        'scheduled_at': '2026-12-01T12:00:00Z',
        'launch_now': False,
        'variants': [
            {'label': 'Variant A', 'message_template': 'Hello {{first_name}}',
             'lead_ids': [ready.id, landline.id]},
            {'label': 'Variant B', 'message_template': 'Hi {{first_name}}',
             'lead_ids': [second.id, no_address.id]},
        ],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    # Each variant keeps its one workable lead and drops the unreachable one.
    assert body['total_leads'] == 2
    assert [variant['lead_count'] for variant in body['variants']] == [1, 1]
    assert [variant['skipped_lead_count'] for variant in body['variants']] == [1, 1]
    assert session.get(Lead, landline.id).campaign_id is None
    assert session.get(Lead, no_address.id).campaign_id is None


@pytest.mark.parametrize('which,stage', [(1, 'not_mobile'), (2, 'no_address')])
def test_an_individual_message_refuses_both_kinds(
    client, auth_header, leads, sent_sms, which, stage,
):
    response = client.post(
        f'{LEADS_URL}/{leads[which].id}/message',
        headers=auth_header(BROKER_EMAIL),
        json={'text': 'Are you open to an offer?'},
    )

    assert response.status_code == 400, response.text
    assert response.json()['detail'] == campaign_service.STAGE_REFUSAL[stage]
    assert sent_sms == []
