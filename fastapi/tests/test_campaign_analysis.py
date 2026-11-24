"""Offline checks for A/B/n campaign audience comparisons."""

import json
from datetime import datetime, timedelta

from app import cache, db
from app.experiments import scheduler as experiment_scheduler
from app.leads import campaign as campaign_service
from app.leads.address import canonical
from app.models import (
    Campaign, Conversation, Experiment, Lead, LeadEvent, Message, OutboundSmsJob,
)


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


def test_experiment_detail_explains_selected_lead_that_was_not_contacted(
        session, make_user, client, auth_header):
    owner = make_user('experiment-outreach-detail@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id,
        name='Lookup outcome detail',
        test_dimension='tone',
        winner_metric='reply_rate',
        allocation_mode='balanced',
        timezone='UTC',
        scheduled_at=datetime(2026, 11, 24, 14, 5),
        status='running',
        total_leads=2,
    )
    session.add(experiment)
    session.flush()
    campaign = Campaign(
        user_id=owner.id,
        name='Lookup outcome detail · Variant A',
        message_template='Hello',
        status='sent',
        experiment_id=experiment.id,
        experiment_variant='Variant A',
    )
    session.add(campaign)
    session.flush()
    experiment.campaign_ids = json.dumps([campaign.id])
    experiment.variant_config = json.dumps([{
        'label': 'Variant A', 'campaign_id': campaign.id, 'lead_count': 2,
        'message_template': 'Hello',
    }])
    conversation = Conversation(user_id=owner.id, contact='+13125550101')
    session.add(conversation)
    session.flush()
    reached = Lead(
        user_id=owner.id, owner_name='Reached Owner', phone='+13125550101',
        property_address='1 Reached Ave', campaign_id=campaign.id,
        conversation_id=conversation.id,
    )
    unknown = Lead(
        user_id=owner.id, owner_name='Unknown Line', phone='+17876142430',
        property_address='2 Lookup Ave', details=json.dumps({'_phone_numbers': [{
            'phone': '+17876142430',
            'telnyx_lookup': {'status': 'unknown', 'line_type': None},
        }]}),
    )
    session.add_all([reached, unknown])
    session.flush()
    message = Message(
        conversation_id=conversation.id, campaign_id=campaign.id,
        direction='outbound', event_type='outreach.initial', status='delivered',
        property_key=canonical(reached.property_address),
    )
    session.add(message)
    session.flush()
    session.add_all([
        LeadEvent(
            lead_id=reached.id, event_category='stage',
            event_type='locked_to_campaign', actor_type='broker',
            to_value=campaign.name, meta=json.dumps({'campaign_id': campaign.id}),
        ),
        LeadEvent(
            lead_id=unknown.id, event_category='stage',
            event_type='locked_to_campaign', actor_type='broker',
            to_value=campaign.name, meta=json.dumps({'campaign_id': campaign.id}),
        ),
        LeadEvent(
            lead_id=reached.id, event_category='activity',
            event_type='outreach_sent', actor_type='broker',
            to_value=campaign.name,
            meta=json.dumps({'campaign_id': campaign.id, 'message_id': message.id}),
        ),
    ])
    session.commit()

    response = client.get(
        f'/api/v1/experiments/{experiment.id}', headers=auth_header(owner.email),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total_leads'] == 2
    assert body['outreached_leads'] == 1
    assert body['outreach_conversations'] == 1
    assert body['not_contacted'] == [{
        'lead_id': unknown.id,
        'owner_name': 'Unknown Line',
        'property_address': '2 Lookup Ave',
        'variant': 'Variant A',
        'reason': 'Phone type could not be verified as mobile by Telnyx. No message was sent.',
    }]


def test_experiment_skips_unavailable_leads_and_uses_accepted_totals(
        session, make_user, client, auth_header):
    owner = make_user('experiment-skip-unavailable@example.com', role='broker')
    leads = [
        Lead(user_id=owner.id, owner_name='Ready A', phone='+13125550101',
             property_address='1 Ready Ave', signals='expired'),
        Lead(user_id=owner.id, owner_name='Unavailable A', phone='+13125550102',
             property_address='2 DNC Ave', signals='expired', dnc=True),
        Lead(user_id=owner.id, owner_name='Ready B', phone='+13125550103',
             property_address='3 Ready Ave', signals='expired'),
    ]
    session.add_all(leads)
    session.commit()

    response = client.post(
        '/api/v1/experiments', headers=auth_header(owner.email), json={
        'name': 'Temporary availability bypass',
        'goal_notes': 'Compare replies.',
        'test_dimension': 'tone',
        'winner_metric': 'reply_rate',
        'allocation_mode': 'balanced',
        'timezone': 'UTC',
        'scheduled_at': '2026-11-21T12:00:00Z',
        'launch_now': False,
        'variants': [
            {'label': 'Variant A', 'message_template': 'Hello {{first_name}}',
             'lead_ids': [leads[0].id, leads[1].id]},
            {'label': 'Variant B', 'message_template': 'Hi {{first_name}}',
             'lead_ids': [leads[2].id]},
        ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total_leads'] == 2
    assert body['expected_max_cost'] == 0.039
    assert [variant['lead_count'] for variant in body['variants']] == [1, 1]
    assert body['variants'][0]['requested_lead_count'] == 2
    assert body['variants'][0]['skipped_lead_count'] == 1
    assert body['variants'][1]['skipped_lead_count'] == 0
    assert session.get(Lead, leads[1].id).campaign_id is None


def test_experiment_still_rejects_a_variant_with_no_eligible_leads(
        session, make_user, client, auth_header):
    owner = make_user('experiment-empty-variant@example.com', role='broker')
    leads = [
        Lead(user_id=owner.id, owner_name='Unavailable A', phone='+13125550201',
             property_address='1 DNC Ave', signals='expired', dnc=True),
        Lead(user_id=owner.id, owner_name='Ready B', phone='+13125550202',
             property_address='2 Ready Ave', signals='expired'),
    ]
    session.add_all(leads)
    session.commit()

    response = client.post(
        '/api/v1/experiments', headers=auth_header(owner.email), json={
        'name': 'Empty variant guard',
        'goal_notes': 'Compare replies.',
        'test_dimension': 'tone',
        'winner_metric': 'reply_rate',
        'allocation_mode': 'balanced',
        'timezone': 'UTC',
        'scheduled_at': '2026-11-21T12:00:00Z',
        'launch_now': False,
        'variants': [
            {'label': 'Variant A', 'message_template': 'Hello {{first_name}}',
             'lead_ids': [leads[0].id]},
            {'label': 'Variant B', 'message_template': 'Hi {{first_name}}',
             'lead_ids': [leads[1].id]},
        ],
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()['detail'] == (
        'Variant A has no eligible leads remaining. '
        'Return to the audience step and recreate the groups.'
    )
    assert session.query(Experiment).filter(
        Experiment.user_id == owner.id,
        Experiment.name == 'Empty variant guard',
    ).count() == 0
    assert session.query(Campaign).filter(
        Campaign.user_id == owner.id,
        Campaign.name.like('Empty variant guard ·%'),
    ).count() == 0


def test_launched_experiment_followup_can_be_scheduled_until_first_send(
        session, make_user, client, auth_header):
    owner = make_user('experiment-followup-edit@example.com', role='broker')
    initial_at = datetime(2026, 11, 23, 12, 0)
    experiment = Experiment(
        user_id=owner.id,
        name='Editable follow-up',
        test_dimension='tone',
        winner_metric='reply_rate',
        allocation_mode='balanced',
        timezone='UTC',
        scheduled_at=initial_at,
        launched_at=initial_at,
        status='running',
        total_leads=2,
        expected_max_cost=0.039,
    )
    session.add(experiment)
    session.flush()
    campaign = Campaign(
        user_id=owner.id,
        name='Editable follow-up · Variant A',
        message_template='Hello {{first_name}}',
        status='sent',
        sent_at=initial_at,
        experiment_id=experiment.id,
        experiment_variant='Variant A',
        followup_enabled=False,
    )
    session.add(campaign)
    session.flush()
    experiment.campaign_ids = json.dumps([campaign.id])
    experiment.variant_config = json.dumps([{
        'label': 'Variant A',
        'message_template': campaign.message_template,
        'campaign_id': campaign.id,
        'lead_count': 2,
        'followup_enabled': False,
        'followup_template': '',
        'followup_delay_hours': 48,
        'color_index': 0,
    }])
    silent = Conversation(
        user_id=owner.id, contact='+13125550301', campaign_id=campaign.id,
        ai_enabled=True, handled_by='bobbie', lead_status='processing',
    )
    replied = Conversation(
        user_id=owner.id, contact='+13125550302', campaign_id=campaign.id,
        ai_enabled=True, handled_by='bobbie', lead_status='processing',
    )
    session.add_all([silent, replied])
    session.flush()
    session.add_all([
        Message(
            conversation_id=silent.id, campaign_id=campaign.id,
            direction='outbound', event_type='outreach.initial', status='sent',
            created_at=initial_at,
        ),
        Message(
            conversation_id=replied.id, campaign_id=campaign.id,
            direction='outbound', event_type='outreach.initial', status='sent',
            created_at=initial_at,
        ),
        Message(
            conversation_id=replied.id, direction='inbound',
            event_type='message.received', status='received',
            created_at=initial_at + timedelta(hours=1), text='Interested',
        ),
    ])
    session.commit()

    path = f'/api/v1/experiments/{experiment.id}/followups/{campaign.id}'
    response = client.patch(path, headers=auth_header(owner.email), json={
        'followup_enabled': True,
        'followup_template': 'Hi {{first_name}}, checking in about {{address}}.',
        'followup_delay_hours': 48,
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['variants'][0]['followup_enabled'] is True
    assert body['variants'][0]['followup_editable'] is True
    assert body['variants'][0]['followup_delay_hours'] == 48
    assert body['expected_max_cost'] == 0.078
    session.expire_all()
    assert session.get(Conversation, silent.id).next_followup_at == (
        initial_at + timedelta(hours=48)
    )
    assert session.get(Conversation, replied.id).next_followup_at is None

    session.add(Message(
        conversation_id=silent.id, campaign_id=campaign.id,
        direction='outbound', event_type='ai.followup.1', status='sent',
        created_at=initial_at + timedelta(hours=48),
    ))
    session.commit()
    locked = client.patch(path, headers=auth_header(owner.email), json={
        'followup_enabled': True,
        'followup_template': 'Changed too late',
        'followup_delay_hours': 72,
    })
    assert locked.status_code == 400, locked.text
    assert 'already started' in locked.json()['detail']


def test_experiment_rename_keeps_variant_campaign_names_aligned(
        session, make_user, client, auth_header):
    owner = make_user('experiment-rename@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id, name='Old experiment name', test_dimension='tone',
        winner_metric='reply_rate', allocation_mode='balanced', timezone='UTC',
        scheduled_at=datetime(2026, 11, 23, 12, 0), status='running',
    )
    session.add(experiment)
    session.flush()
    campaigns = [
        Campaign(
            user_id=owner.id, name=f'Old experiment name · Variant {label}',
            message_template='Hello', status='sent', experiment_id=experiment.id,
            experiment_variant=f'Variant {label}',
        )
        for label in ('A', 'B')
    ]
    session.add_all(campaigns)
    session.flush()
    experiment.campaign_ids = json.dumps([row.id for row in campaigns])
    experiment.variant_config = json.dumps([])
    session.commit()

    response = client.patch(
        f'/api/v1/experiments/{experiment.id}',
        headers=auth_header(owner.email),
        json={'name': 'Renamed experiment'},
    )

    assert response.status_code == 200, response.text
    assert response.json()['name'] == 'Renamed experiment'
    session.expire_all()
    assert [session.get(Campaign, row.id).name for row in campaigns] == [
        'Renamed experiment · Variant A',
        'Renamed experiment · Variant B',
    ]


def test_running_experiment_can_add_random_leads_to_one_variant(
        session, make_user, client, auth_header):
    owner = make_user('experiment-random-extension@example.com', role='broker')
    experiment = Experiment(
        user_id=owner.id, name='Expandable experiment', test_dimension='tone',
        winner_metric='reply_rate', allocation_mode='balanced', timezone='UTC',
        scheduled_at=datetime(2026, 11, 23, 12, 0),
        launched_at=datetime(2026, 11, 23, 12, 0), status='running',
        total_leads=10, expected_max_cost=.195,
    )
    session.add(experiment)
    session.flush()
    campaign = Campaign(
        user_id=owner.id, name='Expandable experiment · Variant A',
        message_template='Hello {{first_name}}', status='sent',
        sent_at=datetime(2026, 11, 23, 12, 0), experiment_id=experiment.id,
        experiment_variant='Variant A', followup_enabled=False,
    )
    session.add(campaign)
    session.flush()
    experiment.campaign_ids = json.dumps([campaign.id])
    experiment.variant_config = json.dumps([{
        'label': 'Variant A', 'message_template': campaign.message_template,
        'campaign_id': campaign.id, 'lead_count': 10,
        'requested_lead_count': 10, 'skipped_lead_count': 0,
        'followup_enabled': False, 'followup_template': '',
        'followup_delay_hours': 48, 'color_index': 0,
    }])
    leads = [
        Lead(user_id=owner.id, owner_name=f'Random Owner {index}',
             phone=f'+1312555060{index}', property_address=f'{index} Pool Ave',
             signals='expired')
        for index in range(1, 4)
    ]
    session.add_all(leads)
    session.commit()

    response = client.post(
        f'/api/v1/experiments/{experiment.id}/variants/{campaign.id}/random-leads',
        headers=auth_header(owner.email), json={'count': 2},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total_leads'] == 12
    assert body['variants'][0]['lead_count'] == 12
    assert body['expected_max_cost'] == .234
    session.expire_all()
    assert session.get(Campaign, campaign.id).status == 'sending'
    jobs = session.query(OutboundSmsJob).filter(
        OutboundSmsJob.campaign_id == campaign.id,
    ).all()
    assert len(jobs) == 2
    assert all(job.status == 'queued' for job in jobs)
    assert sum(lead.campaign_id == campaign.id for lead in leads) == 2


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
        'experiments-list-v3',
        'experiment-detail-v4',
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


def test_send_now_is_not_runnable_until_all_variants_are_ready(
        session, make_user, client, auth_header, monkeypatch):
    owner = make_user('send-now-build-race@example.com', role='broker')
    leads = [
        Lead(user_id=owner.id, owner_name='Owner A', phone='+13125550801',
             property_address='801 Ready Ave', signals='expired'),
        Lead(user_id=owner.id, owner_name='Owner B', phone='+13125550802',
             property_address='802 Ready Ave', signals='expired'),
    ]
    session.add_all(leads)
    session.commit()

    original_create_draft = campaign_service.create_draft
    statuses_during_build = []
    scheduler_results = []

    def create_draft_while_scheduler_ticks(*args, **kwargs):
        if not statuses_during_build:
            inspection = db.SessionLocal()
            try:
                row = inspection.query(Experiment).filter(
                    Experiment.name == 'Send now build race',
                ).one()
                statuses_during_build.append(row.status)
            finally:
                inspection.close()
            scheduler_results.append(experiment_scheduler.run_due(datetime.utcnow()))
        return original_create_draft(*args, **kwargs)

    sent = []

    def fake_send(db_session, user, campaign):
        sent.append(campaign.id)
        campaign.status = 'sent'
        db_session.add(campaign)
        db_session.commit()
        return {}

    monkeypatch.setattr(campaign_service, 'create_draft', create_draft_while_scheduler_ticks)
    monkeypatch.setattr(campaign_service, 'send', fake_send)

    response = client.post(
        '/api/v1/experiments', headers=auth_header(owner.email), json={
            'name': 'Send now build race',
            'goal_notes': 'Prove the scheduler cannot launch a partial build.',
            'test_dimension': 'tone',
            'winner_metric': 'reply_rate',
            'allocation_mode': 'balanced',
            'timezone': 'UTC',
            'scheduled_at': None,
            'launch_now': True,
            'variants': [
                {'label': 'Variant A', 'message_template': 'Hello {{first_name}}',
                 'lead_ids': [leads[0].id]},
                {'label': 'Variant B', 'message_template': 'Hi {{first_name}}',
                 'lead_ids': [leads[1].id]},
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert statuses_during_build == ['building']
    assert scheduler_results == [0]
    assert response.json()['status'] == 'running'
    assert len(sent) == 2
    experiment = session.query(Experiment).filter(
        Experiment.name == 'Send now build race',
    ).one()
    assert experiment.error is None
