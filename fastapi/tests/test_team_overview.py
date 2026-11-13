"""HOB overview metrics: reply timing, attention count and team workload."""

from datetime import datetime, timedelta

from app.models import Conversation, Lead, Message
from app.routes import team


def test_overview_counts_replies_attention_and_workload(make_user, session):
    brokerage = 'brokerage-overview-metrics'
    hob = make_user('ov-hob@example.test', role='hob', brokerage_id=brokerage)
    broker = make_user('ov-broker@example.test', role='broker', brokerage_id=brokerage)
    agent = make_user('ov-agent@example.test', role='agent', brokerage_id=brokerage)

    session.add_all([
        Lead(user_id=broker.id, phone='+13125550101', assigned_agent_id=agent.id),
        Lead(user_id=broker.id, phone='+13125550102'),
        Lead(user_id=hob.id, phone=None),
    ])
    thread = Conversation(contact='+13125550101', user_id=broker.id, handled_by='broker')
    session.add(thread)
    session.flush()
    start = datetime(2026, 10, 1, 12, 0, 0)
    session.add_all([
        Message(conversation_id=thread.id, direction='outbound', created_at=start),
        Message(conversation_id=thread.id, direction='inbound', created_at=start + timedelta(minutes=1)),
        Message(conversation_id=thread.id, direction='outbound', created_at=start + timedelta(minutes=2)),
        Message(conversation_id=thread.id, direction='inbound', created_at=start + timedelta(minutes=5)),
    ])
    session.commit()

    overview = team._build_brokerage_overview(session, hob)

    assert overview['total_leads'] == 3
    # Owner spoke last on a broker-handled thread: it needs an answer.
    assert overview['replies_requiring_attention'] == 1
    # Only the first inbound has a later outbound reply, 60 s after it.
    assert overview['average_response_seconds'] == 60
    workload = {row['user_id']: row['lead_count'] for row in overview['workload']}
    assert workload == {broker.id: 2, agent.id: 1}
