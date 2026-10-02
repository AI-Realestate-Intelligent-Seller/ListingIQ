"""Who is notified when a customer replies by SMS."""

from datetime import datetime, timedelta

from app.leads import events as lead_events
from app.models import Conversation, Lead, LeadEvent, Message
from app.routes.webhooks import _reply_recipient_ids

T0 = datetime(2026, 9, 1, 12, 0, 0)


def _thread(session, owner_id, assigned_agent_id=None):
    conversation = Conversation(contact='+13125550100', user_id=owner_id, created_at=T0)
    session.add(conversation)
    session.flush()
    lead = Lead(user_id=owner_id, phone='+13125550100', conversation_id=conversation.id,
                assigned_agent_id=assigned_agent_id)
    session.add(lead)
    session.commit()
    return conversation, lead


def _sent_by(session, conversation, user_id, minutes):
    session.add(Message(conversation_id=conversation.id, direction='outbound', text='hi',
                        event_type='broker.message', sender_user_id=user_id,
                        created_at=T0 + timedelta(minutes=minutes)))
    session.commit()


def _event(session, lead, user_id, minutes, category=lead_events.STAGE):
    session.add(LeadEvent(lead_id=lead.id, event_category=category, event_type='x',
                          actor_type='broker', actor_id=user_id,
                          created_at=T0 + timedelta(minutes=minutes)))
    session.commit()


def test_assigned_agent_is_notified(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    agent = make_user('agent@x.net', role='agent')
    hob = make_user('hob@x.net', role='hob')
    conversation, _ = _thread(session, owner.id, assigned_agent_id=agent.id)
    _sent_by(session, conversation, hob.id, 5)

    assert _reply_recipient_ids(session, conversation) == [agent.id]


def test_unassigned_goes_to_whoever_last_worked_the_lead(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    hob = make_user('hob@x.net', role='hob')
    conversation, lead = _thread(session, owner.id)
    _sent_by(session, conversation, owner.id, 1)
    _event(session, lead, hob.id, 10)

    assert _reply_recipient_ids(session, conversation) == [hob.id]

    _sent_by(session, conversation, owner.id, 20)
    assert _reply_recipient_ids(session, conversation) == [owner.id]


def test_assignment_admin_does_not_count_as_work(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    hob = make_user('hob@x.net', role='hob')
    conversation, lead = _thread(session, owner.id)
    _sent_by(session, conversation, owner.id, 1)
    _event(session, lead, hob.id, 10, category=lead_events.ASSIGNMENT)

    assert _reply_recipient_ids(session, conversation) == [owner.id]


def test_unassigned_agent_who_lost_access_is_skipped(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    hob = make_user('hob@x.net', role='hob')
    agent = make_user('agent@x.net', role='agent')
    conversation, _ = _thread(session, owner.id)
    _sent_by(session, conversation, hob.id, 1)
    _sent_by(session, conversation, agent.id, 10)

    assert _reply_recipient_ids(session, conversation) == [hob.id]


def test_inactive_or_other_brokerage_users_are_skipped(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    gone = make_user('gone@x.net', role='broker', is_active=False)
    outsider = make_user('out@y.net', role='broker', brokerage_id='brokerage-2')
    conversation, _ = _thread(session, owner.id)
    _sent_by(session, conversation, gone.id, 5)
    _sent_by(session, conversation, outsider.id, 10)

    assert _reply_recipient_ids(session, conversation) == [owner.id]


def test_untouched_unassigned_lead_falls_back_to_owner(session, make_user):
    owner = make_user('owner@x.net', role='broker')
    conversation, _ = _thread(session, owner.id)

    assert _reply_recipient_ids(session, conversation) == [owner.id]
