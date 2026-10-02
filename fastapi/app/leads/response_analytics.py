"""Response performance reconstructed from existing assignment and SMS history."""

from collections import defaultdict
from datetime import datetime, timedelta
from itertools import groupby

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Lead, LeadEvent, Message

ASSIGNMENT_EVENTS = ('assigned', 'reassigned', 'revoked')
MESSAGE_EVENTS = ('message.received', 'broker.message', 'ai.reply', 'ai.closing')
FAILED_STATUSES = ('failed', 'rejected', 'undelivered')


def _user_id(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _assignment_intervals(leads, events, agent_id, end_at):
    """Merge ownership of linked leads so one shared thread counts only once."""
    by_lead = defaultdict(list)
    for event in events:
        by_lead[event.lead_id].append(event)
    by_conversation = defaultdict(list)
    for lead in leads:
        history = by_lead[lead.id]
        # Preserve the previous current-assignee behavior for legacy leads.
        # A first transfer's from_value also identifies the preceding owner,
        # even if their original assignment predates event tracking.
        owner = _user_id(history[0].from_value) if history else lead.assigned_agent_id
        started = datetime.min
        for event in history:
            new_owner = None if event.event_type == 'revoked' else (
                _user_id(event.to_value) or event.target_id
            )
            if owner == new_owner:
                continue
            if owner == agent_id and started < event.created_at:
                by_conversation[lead.conversation_id].append((started, event.created_at))
            owner, started = new_owner, event.created_at
        if owner == agent_id and started < end_at:
            by_conversation[lead.conversation_id].append((started, end_at))

    merged = {}
    for conversation_id, intervals in by_conversation.items():
        result = []
        for start, end in sorted(intervals):
            if result and start <= result[-1][1]:
                result[-1] = (result[-1][0], max(end, result[-1][1]))
            else:
                result.append((start, end))
        merged[conversation_id] = result
    return merged


def _waiting_periods(messages):
    started = None
    for message in messages:
        if message.event_type == 'message.received':
            if started is None:
                started = message.created_at
        elif (message.status or '').lower() not in FAILED_STATUSES and started is not None:
            yield started, message
            started = None
    if started is not None:
        yield started, None


def get_agent_response_analytics(session: Session, agent_id: int, days: int = 30) -> dict:
    """Calculate response time and reply rate during assignment periods.

    Include opportunities active in the reporting window, retaining the actual
    waiting start for older pending messages. Successful AI/other-user replies
    close a waiting period but are excluded from this agent's human reply rate.
    Assignment changes take effect before messages at the same timestamp.
    """
    end_at = datetime.utcnow()
    start_at = end_at - timedelta(days=days)
    historical_leads = select(LeadEvent.lead_id).where(
        LeadEvent.event_category == 'assignment',
        LeadEvent.event_type.in_(ASSIGNMENT_EVENTS),
        LeadEvent.created_at < end_at,
        or_(LeadEvent.target_id == agent_id,
            LeadEvent.to_value == str(agent_id), LeadEvent.from_value == str(agent_id)),
    )
    conversation_ids = select(Lead.conversation_id).where(
        Lead.conversation_id.isnot(None),
        or_(Lead.assigned_agent_id == agent_id, Lead.id.in_(historical_leads)),
    ).distinct()
    leads = session.query(Lead.id, Lead.conversation_id, Lead.assigned_agent_id).filter(
        Lead.conversation_id.in_(conversation_ids),
    ).all()
    events = session.query(
        LeadEvent.lead_id, LeadEvent.event_type, LeadEvent.from_value,
        LeadEvent.to_value, LeadEvent.target_id, LeadEvent.created_at,
    ).join(Lead, Lead.id == LeadEvent.lead_id).filter(
        Lead.conversation_id.in_(conversation_ids),
        LeadEvent.event_category == 'assignment',
        LeadEvent.event_type.in_(ASSIGNMENT_EVENTS),
        LeadEvent.created_at < end_at,
    ).order_by(LeadEvent.created_at, LeadEvent.id).all()
    intervals = _assignment_intervals(leads, events, agent_id, end_at)
    # Read preceding messages too: otherwise an old inbound still waiting at
    # transfer/report start would disappear or get an artificially short timer.
    messages = session.query(
        Message.conversation_id, Message.created_at, Message.event_type,
        Message.sender_user_id, Message.status,
    ).filter(
        Message.conversation_id.in_(conversation_ids),
        Message.event_type.in_(MESSAGE_EVENTS),
        Message.created_at < end_at,
    ).order_by(Message.conversation_id, Message.created_at, Message.id).yield_per(1000)

    answered = pending = 0
    total_seconds = 0.0
    for conversation_id, rows in groupby(messages, key=lambda row: row.conversation_id):
        ownership = intervals.get(conversation_id, [])
        for waiting_started, response in _waiting_periods(rows):
            for assigned_at, released_at in ownership:
                if released_at <= waiting_started or released_at <= start_at:
                    continue
                if response is not None and (
                    response.created_at < assigned_at or response.created_at < start_at
                ):
                    continue
                opportunity_started = max(waiting_started, assigned_at)
                if response is not None and response.created_at < released_at:
                    if response.event_type == 'broker.message' and response.sender_user_id == agent_id:
                        answered += 1
                        total_seconds += (response.created_at - opportunity_started).total_seconds()
                elif released_at == end_at:
                    pending += 1

    eligible = answered + pending
    return {
        'average_response_seconds': round(total_seconds / answered, 2) if answered else None,
        'reply_rate': round(answered / eligible * 100, 1) if eligible else None,
    }
