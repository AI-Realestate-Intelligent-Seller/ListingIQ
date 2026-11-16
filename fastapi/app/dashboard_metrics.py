"""Shared lifetime totals for the HOB and Area Broker overview cards."""

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import Campaign, Conversation, LoftyExport, Message


POSITIVE_REPLY_STATUSES = ('interested', 'ready_to_sell', 'location_discussion')
UNSENT_MESSAGE_STATUSES = ('submission_queued', 'submission_retry', 'sending_failed')


def communication_totals(session: Session, user_ids: list[int]) -> dict[str, int]:
    """Return persisted, lifetime communication totals for the selected owners.

    Every outreach/reply outcome counts distinct conversations, which are the
    durable one-per-owner lead threads used by the dashboard. Multiple outbound
    follow-ups or inbound messages in one thread therefore count that lead once.
    Failed and not-yet-submitted outbound attempts do not count as outreach.
    """
    if not user_ids:
        return {
            'total_campaigns': 0,
            'total_leads_outreached': 0,
            'total_leads_replied': 0,
            'total_leads_opted_out': 0,
            'total_positive_leads': 0,
            'total_other_reply_leads': 0,
            'total_sent_to_lofty': 0,
            'total_appointments_to_confirm': 0,
        }

    scoped_conversations = session.query(Conversation.id).filter(
        Conversation.user_id.in_(user_ids),
    )
    inbound_conversations = session.query(Message.conversation_id).filter(
        Message.direction == 'inbound',
    ).distinct()
    completed_lofty_conversations = session.query(LoftyExport.conversation_id).filter(
        LoftyExport.status == 'completed',
    )
    replied_conversations = session.query(Conversation.id).filter(
        Conversation.user_id.in_(user_ids),
        Conversation.id.in_(inbound_conversations),
    )
    total_replied = replied_conversations.count()
    total_opt_outs = replied_conversations.filter(
        Conversation.lead_status == 'dnc',
    ).count()
    total_positive = replied_conversations.filter(
        Conversation.lead_status != 'dnc',
        or_(
            Conversation.lead_status.in_(POSITIVE_REPLY_STATUSES),
            Conversation.meeting_booked.is_(True),
            Conversation.id.in_(completed_lofty_conversations),
        ),
    ).count()

    return {
        'total_campaigns': session.query(Campaign.id).filter(
            Campaign.user_id.in_(user_ids),
        ).count(),
        'total_leads_outreached': session.query(Message.conversation_id).filter(
            Message.conversation_id.in_(scoped_conversations),
            Message.direction == 'outbound',
            or_(Message.status.is_(None), ~Message.status.in_(UNSENT_MESSAGE_STATUSES)),
        ).distinct().count(),
        'total_leads_replied': total_replied,
        'total_leads_opted_out': total_opt_outs,
        'total_positive_leads': total_positive,
        'total_other_reply_leads': max(total_replied - total_positive - total_opt_outs, 0),
        'total_sent_to_lofty': session.query(LoftyExport.id).join(
            Conversation, Conversation.id == LoftyExport.conversation_id,
        ).filter(
            Conversation.user_id.in_(user_ids),
            LoftyExport.status == 'completed',
        ).count(),
        'total_appointments_to_confirm': session.query(Conversation.id).filter(
            Conversation.user_id.in_(user_ids),
            Conversation.meeting_booked.is_(False),
            Conversation.lead_status.in_(POSITIVE_REPLY_STATUSES),
        ).count(),
    }
