"""Offline checks for A/B/n campaign audience comparisons."""

import json
from datetime import datetime

from app import cache
from app.experiments import scheduler as experiment_scheduler
from app.leads import campaign as campaign_service
from app.leads.address import canonical
from app.models import Campaign, Conversation, Experiment, Lead, Message


def test_audience_distributions_are_property_based_and_tenant_scoped(
        session, make_user, client, auth_header):
    owner = make_user('analysis-owner@example.com', role='broker')
    outsider = make_user(
        'analysis-outsider@example.com', role='broker', brokerage_id='brokerage-2',
    )
    first = Campaign(user_id=owner.id, name='Run A', message_template='Hello', status='sent')
    second = Campaign(user_id=outsider.id, name='Other tenant', message_template='Hello', status='sent')
    session.add_all([first, second])
    session.flush()
    session.add_all([
        Lead(user_id=owner.id, campaign_id=first.id, property_address='1 Main St',
             area='Chicago', signals='expired,high_equity', score=75, source='csv_import'),
        # A co-owner row at the same property must not inflate the experiment audience.
        Lead(user_id=owner.id, campaign_id=first.id, property_address='1 MAIN ST',
             area='Chicago', signals='expired', score=60, source='csv_import'),
        Lead(user_id=owner.id, campaign_id=first.id, property_address='2 Oak Ave',
             area='Evanston', signals='probate', score=45, source='propertyradar'),
        Lead(user_id=outsider.id, campaign_id=second.id, property_address='3 Pine Rd',
             area='Naperville', signals='fsbo', score=90, source='csv_import'),
    ])
    chicago = Conversation(
        user_id=owner.id, contact='+13125550101', property_address='1 Main St',
        property_key=canonical('1 Main St'), lead_status='interested',
    )
    evanston = Conversation(
        user_id=owner.id, contact='+13125550102', property_address='2 Oak Ave',
        property_key=canonical('2 Oak Ave'), lead_status='processing',
    )
    session.add_all([chicago, evanston])
    session.flush()
    session.add_all([
        Message(conversation_id=chicago.id, campaign_id=first.id, direction='outbound',
                event_type='outreach.initial', status='sent', property_key=canonical('1 Main St')),
        Message(conversation_id=chicago.id, direction='inbound',
                event_type='message.received', status='received', text='Yes, interested'),
        Message(conversation_id=evanston.id, campaign_id=first.id, direction='outbound',
                event_type='outreach.initial', status='sent', property_key=canonical('2 Oak Ave')),
    ])
    session.commit()

    result = campaign_service.audience_distributions(session, owner)

    assert [row['campaign_id'] for row in result] == [first.id]
    assert result[0]['recipients'] == 2
    assert {row['key']: row['count'] for row in result[0]['signals']} == {
        'expired': 1, 'high_equity': 1, 'probate': 1,
    }
    assert {row['key']: row['count'] for row in result[0]['score_bands']} == {
        'high': 1, 'medium': 1, 'low': 0,
    }
    assert {row['key']: row for row in result[0]['area_outcomes']}['Chicago'] == {
        'key': 'Chicago', 'label': 'Chicago', 'audience': 1, 'outreached': 1,
        'replied': 1, 'positive_replies': 1,
    }

    response = client.get(
        '/api/v1/campaigns/analysis/audiences', headers=auth_header(owner.email),
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]['campaign_id'] == first.id
    assert response.json()[0]['recipients'] == 2

    campaigns = client.get('/api/v1/campaigns', headers=auth_header(owner.email)).json()
    assert campaigns[0]['positive_replies'] == 1


def test_random_campaign_draft_uses_available_lead_pool(
        session, make_user, client, auth_header):
    owner = make_user('random-analysis@example.com', role='broker')
    session.add_all([
        Lead(user_id=owner.id, owner_name=f'Owner {index}', phone=f'+13125550{index:03d}',
             property_address=f'{index} Random Ave', area='Chicago', signals='expired')
        for index in range(5)
    ])
    session.commit()

    response = client.post(
        '/api/v1/campaigns/draft/random', headers=auth_header(owner.email), json={'count': 3},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['campaign']['status'] == 'draft'
    assert body['campaign']['recipients'] == 3
    assert len(body['campaign']['preview']['recipients']) == 3


def test_experiment_reports_distinct_conversations_across_variants(
        session, make_user, client, auth_header):
    owner = make_user('experiment-conversations@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id,
        name='Property and conversation totals',
        test_dimension='tone',
        winner_metric='reply_rate',
        allocation_mode='balanced',
        timezone='UTC',
        scheduled_at=datetime(2026, 11, 18, 12, 0),
        status='running',
        total_leads=3,
    )
    session.add(experiment)
    session.flush()
    variants = [
        Campaign(user_id=owner.id, name=f'Variant {label}', message_template='Hello',
                 status='sent', experiment_id=experiment.id, experiment_variant=label)
        for label in ('A', 'B')
    ]
    session.add_all(variants)
    session.flush()
    experiment.campaign_ids = json.dumps([campaign.id for campaign in variants])
    shared = Conversation(user_id=owner.id, contact='+13125550101')
    separate = Conversation(user_id=owner.id, contact='+13125550102')
    session.add_all([shared, separate])
    session.flush()
    session.add_all([
        Message(conversation_id=shared.id, campaign_id=variants[0].id,
                direction='outbound', event_type='outreach.initial', status='queued',
                property_key='first-property'),
        Message(conversation_id=shared.id, campaign_id=variants[1].id,
                direction='outbound', event_type='outreach.initial', status='queued',
                property_key='second-property'),
        Message(conversation_id=separate.id, campaign_id=variants[0].id,
                direction='outbound', event_type='outreach.initial', status='queued',
                property_key='third-property'),
    ])
    session.commit()

    response = client.get('/api/v1/experiments', headers=auth_header(owner.email))

    assert response.status_code == 200, response.text
    assert response.json()[0]['total_leads'] == 3
    assert response.json()[0]['outreach_conversations'] == 2


def test_analysis_and_experiment_reads_use_versioned_redis_cache(
        session, make_user, client, auth_header, monkeypatch):
    owner = make_user('cached-analysis@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id,
        name='Cached experiment',
        test_dimension='tone',
        winner_metric='reply_rate',
        allocation_mode='balanced',
        timezone='UTC',
        scheduled_at=datetime(2026, 11, 17, 12, 0),
        status='scheduled',
    )
    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    calls = []

    def through_cache(namespace, tables, key_parts, loader, ttl=None):
        calls.append((namespace, tuple(tables), key_parts, ttl))
        return loader()

    monkeypatch.setattr(cache, 'get_or_load', through_cache)
    headers = auth_header(owner.email)

    audiences = client.get('/api/v1/campaigns/analysis/audiences', headers=headers)
    experiments = client.get('/api/v1/experiments', headers=headers)
    detail = client.get(f'/api/v1/experiments/{experiment.id}', headers=headers)

    assert audiences.status_code == 200, audiences.text
    assert experiments.status_code == 200, experiments.text
    assert detail.status_code == 200, detail.text
    assert [item[0] for item in calls] == [
        'campaign-audience-analysis-v1',
        'experiments-list-v2',
        'experiment-detail-v2',
    ]
    assert all('experiments' in item[1] for item in calls)
    assert all(item[2]['user'] == owner.id for item in calls)


def test_send_now_launches_every_experiment_variant(
        session, make_user, monkeypatch):
    owner = make_user('send-now@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id,
        name='Immediate experiment',
        test_dimension='tone',
        winner_metric='reply_rate',
        allocation_mode='balanced',
        timezone='UTC',
        scheduled_at=datetime.utcnow(),
        status='scheduled',
    )
    session.add(experiment)
    session.flush()
    campaigns = [
        Campaign(user_id=owner.id, name=f'Variant {label}', message_template='Hello',
                 status='draft', experiment_id=experiment.id, experiment_variant=label)
        for label in ('A', 'B')
    ]
    session.add_all(campaigns)
    session.commit()
    sent = []

    def fake_send(db_session, user, campaign):
        sent.append((user.id, campaign.id))
        campaign.status = 'sent'
        db_session.add(campaign)
        db_session.commit()
        return {}

    monkeypatch.setattr(campaign_service, 'send', fake_send)

    assert experiment_scheduler.launch_experiment(experiment.id) is True
    session.expire_all()
    assert session.get(Experiment, experiment.id).status == 'running'
    assert session.get(Experiment, experiment.id).launched_at is not None
    assert [campaign_id for _user_id, campaign_id in sent] == [row.id for row in campaigns]
