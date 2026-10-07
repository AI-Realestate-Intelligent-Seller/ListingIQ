from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models import Campaign, Conversation, Lead, Message
from app.routes.assignments import (assign_lead, list_assignments, list_my_assigned_leads,
                                    broker_overview, my_assignment_overview, round_robin_assignments,
                                    update_my_lead_stage)
from app.routes.followups import get_followup, list_followups
from app.schemas import LeadAssignmentRequest, LeadAssignmentStageRequest


def replied_lead(session, broker):
    conversation = Conversation(
        user_id=broker.id,
        contact='+12125550199',
        name='Replied Owner',
        property_address='10 Main St',
    )
    session.add(conversation)
    session.flush()
    lead = Lead(
        user_id=broker.id,
        owner_name='Replied Owner',
        phone='+12125550199',
        property_address='10 Main St',
        signals='fsbo',
        score=70,
        conversation_id=conversation.id,
    )
    session.add(lead)
    session.add(Message(conversation_id=conversation.id, direction='inbound', text='Interested'))
    session.commit()
    session.refresh(lead)
    return lead


def test_broker_sees_replied_leads_and_only_linked_agents(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    linked = make_user('linked@linchpinglobal.net', role='agent')
    other = make_user('other@linchpinglobal.net', role='agent')
    linked.assigned_broker_id = broker.id
    session.merge(linked)
    session.commit()
    lead = replied_lead(session, broker)

    result = list_assignments(broker, session)

    assert [row['id'] for row in result['leads']] == [lead.id]
    assert result['leads'][0]['assignee_id'] is None
    assert [row['email'] for row in result['agents']] == ['linked@linchpinglobal.net']
    assert other.email not in {row['email'] for row in result['agents']}


def test_broker_can_assign_replied_lead_to_linked_agent(make_user, session, monkeypatch):
    emails = []
    monkeypatch.setattr(
        'app.routes.assignments.send_lead_assigned_email',
        lambda **kwargs: emails.append(kwargs),
    )
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    agent.assigned_broker_id = broker.id
    session.merge(agent)
    session.commit()
    lead = replied_lead(session, broker)

    result = assign_lead(lead.id, LeadAssignmentRequest(agent_id=agent.id), broker, session)

    assert result['assignee_id'] == agent.id
    session.refresh(lead)
    assert lead.assigned_agent_id == agent.id
    assert emails == [{
        'recipient_email': agent.email,
        'broker_name': broker.full_name,
        'lead_id': lead.id,
        'lead_name': lead.owner_name,
        'property_address': lead.property_address,
        'reassigned': False,
    }]


def test_broker_overview_reports_campaign_and_assignment_metrics(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    draft = Campaign(user_id=broker.id, name='Draft', status='draft')
    sent = Campaign(user_id=broker.id, name='Sent', status='sent')
    session.add_all([draft, sent])
    session.flush()
    lead = replied_lead(session, broker)
    lead.campaign_id = sent.id
    lead.assigned_agent_id = agent.id
    lead.assignment_stage = 'processing'
    conversation = session.query(Conversation).filter(Conversation.id == lead.conversation_id).one()
    conversation.campaign_id = sent.id
    conversation.lead_status = 'interested'
    conversation.meeting_booked = True
    session.commit()

    result = broker_overview(broker, session)

    assert result == {
        'campaigns_started': 2,
        'campaigns_completed': 1,
        'campaigns_in_draft': 1,
        'campaign_to_matured_rate': 100.0,
        'total_assigned': 1,
        'total_in_progress': 1,
        'total_completed': 0,
        'total_booked': 1,
    }


def test_broker_cannot_assign_to_unlinked_agent(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    unlinked = make_user('agent@linchpinglobal.net', role='agent')
    lead = replied_lead(session, broker)

    with pytest.raises(HTTPException) as error:
        assign_lead(lead.id, LeadAssignmentRequest(agent_id=unlinked.id), broker, session)

    assert error.value.status_code == 400


def test_round_robin_balances_only_unassigned_leads(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    first = make_user('first@linchpinglobal.net', role='agent')
    second = make_user('second@linchpinglobal.net', role='agent')
    first.assigned_broker_id = broker.id
    second.assigned_broker_id = broker.id
    session.merge(first)
    session.merge(second)
    assigned = replied_lead(session, broker)
    assigned.assigned_agent_id = first.id
    unassigned = [replied_lead(session, broker) for _ in range(3)]
    session.commit()

    result = round_robin_assignments(broker, session)

    assert result['assigned'] == 3
    session.refresh(assigned)
    assert assigned.assigned_agent_id == first.id
    for lead in unassigned:
        session.refresh(lead)
        assert lead.assigned_agent_id in {first.id, second.id}
        assert lead.assignment_stage == 'new'
    counts = [sum(lead['assignee_id'] == agent_id for lead in result['leads'])
              for agent_id in (first.id, second.id)]
    assert counts == [2, 2]


def test_agent_sees_only_leads_assigned_to_them(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    another_agent = make_user('other@linchpinglobal.net', role='agent')
    mine = replied_lead(session, broker)
    mine.assigned_agent_id = agent.id
    other = Lead(
        user_id=broker.id,
        owner_name='Someone Else',
        phone='+12125550200',
        property_address='20 Main St',
        score=50,
        assigned_agent_id=another_agent.id,
    )
    session.add(other)
    session.commit()

    result = list_my_assigned_leads(agent, session)

    assert [lead['id'] for lead in result['leads']] == [mine.id]


def test_agent_overview_counts_assignment_pipeline(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    lead = replied_lead(session, broker)
    lead.assigned_agent_id = agent.id
    lead.assignment_stage = 'ready_to_sell'
    lead.assignment_stage_seconds = '{"new": 120, "processing": 180, "ready_to_sell": 0}'
    conversation = session.query(Conversation).filter(Conversation.id == lead.conversation_id).one()
    conversation.meeting_booked = True
    session.commit()

    result = my_assignment_overview(agent, session)

    assert result['assigned_leads'] == 1
    assert result['completed_leads'] == 1
    assert result['replies_requiring_attention'] == 1
    assert result['appointments_booked'] == 1
    assert result['completion_rate'] == 100.0
    assert result['average_handling_seconds'] == 300


def test_agent_followups_are_limited_to_assigned_leads(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    another_agent = make_user('other@linchpinglobal.net', role='agent')
    mine = replied_lead(session, broker)
    mine.assigned_agent_id = agent.id

    other_conversation = Conversation(
        user_id=broker.id,
        contact='+12125550200',
        name='Other Owner',
        property_address='20 Main St',
    )
    session.add(other_conversation)
    session.flush()
    other = Lead(
        user_id=broker.id,
        owner_name='Other Owner',
        phone='+12125550200',
        property_address='20 Main St',
        score=50,
        conversation_id=other_conversation.id,
        assigned_agent_id=another_agent.id,
    )
    session.add(other)
    session.add(Message(
        conversation_id=other_conversation.id,
        direction='inbound',
        text='Also interested',
    ))
    session.commit()

    rows = list_followups(None, None, 'replied', agent, session)

    assert [row['lead_id'] for row in rows] == [mine.id]
    with pytest.raises(HTTPException) as error:
        get_followup(other_conversation.id, agent, session)
    assert error.value.status_code == 404


def test_agent_stage_transition_accumulates_time(make_user, session):
    broker = make_user('broker@linchpinglobal.net', role='broker')
    agent = make_user('agent@linchpinglobal.net', role='agent')
    lead = replied_lead(session, broker)
    lead.assigned_agent_id = agent.id
    lead.assignment_stage = 'new'
    lead.assignment_stage_changed_at = datetime.utcnow() - timedelta(minutes=5)
    lead.assignment_stage_seconds = '{}'
    session.commit()

    result = update_my_lead_stage(
        lead.id,
        LeadAssignmentStageRequest(stage='processing'),
        agent,
        session,
    )

    assert result['assignment_stage'] == 'processing'
    assert result['stage_seconds']['new'] >= 299
