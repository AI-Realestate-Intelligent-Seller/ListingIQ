"""Confirmed non-mobile numbers are saved on the lead and kept out of the Lead Pool."""

import json
from datetime import datetime

import pytest

from app.leads import campaign as campaign_service
from app.leads import service as lead_service
from app.models import Campaign, Lead
from app.sms import service as sms_service
from app.sms.number_lookup import NumberLookupResult

BROKER = 'nonmobile@example.com'


@pytest.fixture
def live_campaign(make_user, session, monkeypatch):
    """A three-lead campaign sent in live mode with scripted Telnyx lookups."""
    owner = make_user(BROKER, role='broker')
    campaign = Campaign(user_id=owner.id, name='Mobile only',
                        message_template='Hi {{first_name}}, about {{address}}.', status='draft')
    session.add(campaign)
    session.flush()
    leads = [Lead(user_id=owner.id, campaign_id=campaign.id, owner_name=f'Owner {i}',
                  phone=f'+1312555020{i}', property_address=f'{i} Main St, Chicago, IL 60601',
                  details=json.dumps({'_phone_numbers': [{'phone': f'+1312555020{i}', 'dnc': False}]}))
             for i in range(3)]
    session.add_all(leads)
    session.commit()
    mobile, landline, failed = leads
    outcomes = {
        mobile.phone: NumberLookupResult(phone_number=mobile.phone, status='verified', line_type='mobile',
                                         checked_at='2026-11-04T01:00:00Z'),
        landline.phone: NumberLookupResult(phone_number=landline.phone, status='verified',
                                           line_type='landline', checked_at='2026-11-04T01:00:01Z'),
        failed.phone: NumberLookupResult(phone_number=failed.phone, status='error',
                                         checked_at='2026-11-04T01:00:02Z', error_code='request_failed'),
    }
    lookups = []

    def lookup(phone):
        lookups.append(phone)
        return outcomes[phone]

    monkeypatch.setattr(campaign_service.number_lookup, 'lookup_number', lookup)
    monkeypatch.setattr(sms_service, 'send_sms', lambda payload: {'data': {'id': f'sim-{payload["to"]}'}})
    monkeypatch.setattr(campaign_service, 'schedule_initial_followup', lambda *_: None)
    monkeypatch.setitem(campaign_service.settings['telnyx'], 'mode', 'telnyx')
    campaign_service.send(session, owner, campaign)
    from tests.conftest import drain_outbound_queue
    drain_outbound_queue()
    session.expire_all()
    return {'owner': owner, 'campaign': campaign, 'mobile': mobile.id, 'landline': landline.id,
            'failed': failed.id, 'lookups': lookups}


def _pool_ids(session, owner):
    return {item['id'] for item in lead_service.list_leads(session, owner)}


def test_landline_is_saved_and_not_returned_to_the_pool(session, live_campaign):
    owner = live_campaign['owner']
    landline = session.get(Lead, live_campaign['landline'])
    assert session.get(Campaign, live_campaign['campaign'].id).status == 'sent'
    # Saved state for a future screen.
    assert landline.phone_line_type == 'landline'
    assert landline.phone_lookup_number == landline.phone
    assert landline.phone_lookup_at == datetime(2026, 11, 4, 1, 0, 1)
    assert lead_service.derive_stage(landline) == 'not_mobile'
    # Released from the campaign but hidden from the pool, its counts and the map query.
    assert landline.campaign_id is None
    pool = _pool_ids(session, owner)
    assert landline.id not in pool
    assert live_campaign['failed'] in pool  # a failed lookup may be temporary: back to the pool
    assert live_campaign['mobile'] not in pool  # texted, so it is in the campaign
    assert landline.id not in {lead.id for lead in lead_service._pool_leads(session, owner)}
    assert 'not_mobile' not in lead_service.facets(session, owner)['stages']


def test_mobile_and_failed_lookups_keep_their_normal_state(session, live_campaign):
    mobile = session.get(Lead, live_campaign['mobile'])
    failed = session.get(Lead, live_campaign['failed'])
    assert mobile.phone_line_type == 'mobile'
    assert failed.phone_line_type is None
    assert lead_service.derive_stage(failed) == 'ready'


def test_a_landline_lead_cannot_be_added_to_another_campaign(session, live_campaign):
    owner = live_campaign['owner']
    draft = Campaign(user_id=owner.id, name='Second try', message_template='Hi', status='draft')
    session.add(draft)
    session.commit()
    left = campaign_service.attach(session, owner, draft, [live_campaign['landline']])
    assert left == [{'lead_id': live_campaign['landline'], 'owner_name': 'Owner 1',
                     'reason': 'Number is not a mobile; campaigns send only to mobile numbers.'}]
    assert session.get(Lead, live_campaign['landline']).campaign_id is None


def test_a_new_phone_number_brings_the_lead_back(session, live_campaign):
    owner = live_campaign['owner']
    landline = session.get(Lead, live_campaign['landline'])
    landline.phone = '+13125550299'
    session.commit()
    assert lead_service.derive_stage(landline) == 'ready'
    assert landline.id in _pool_ids(session, owner)


@pytest.mark.parametrize('line_type', ['landline', 'voip', 'toll_free', 'fixed line'])
def test_every_confirmed_non_mobile_type_is_held(line_type):
    lead = Lead(phone='+13125550300', phone_line_type=line_type, phone_lookup_number='+13125550300')
    assert lead_service.has_non_mobile_phone(lead)


def test_mobile_or_unchecked_numbers_are_not_held():
    assert not lead_service.has_non_mobile_phone(Lead(phone='+1', phone_line_type='mobile',
                                                      phone_lookup_number='+1'))
    assert not lead_service.has_non_mobile_phone(Lead(phone='+1'))
    assert not lead_service.has_non_mobile_phone(Lead(phone='+2', phone_line_type='landline',
                                                      phone_lookup_number='+1'))
