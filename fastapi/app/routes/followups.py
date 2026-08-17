"""Follow-ups API — the conversations an owner has actually replied to.

The SMS workspace is the chat module: every thread this user has, answered or
not. Follow-ups is the working list built on top of it. Only conversations with
at least one inbound reply appear, each carrying the reason it is waiting and
the decisions the assignee can record on it: accept the lead, decline it, or
book the meeting.

Ownership is always taken from the JWT (Conversation.user_id), so one brokerage
can never read or act on another's threads. Taking a thread over and reading
its messages stay on the SMS endpoints — this module adds no second way to do
either.
"""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..logger import get_logger
from ..models import Conversation, Lead, Message, User
from ..schemas import (
    FOLLOWUP_STATES,
    FollowUpAppointmentRequest,
    FollowUpAppointmentResult,
    FollowUpOut,
    FollowUpStateRequest,
    FollowUpStatusUpdate,
)
from ..sms import calendar_service, service
from .auth import get_current_user, get_db

router = APIRouter()
logger = get_logger(__name__)

# The same roles that run an SMS workspace work their own follow-ups.
FOLLOWUP_ROLES = {'hob', 'broker', 'agent'}

# A thread the owner has gone quiet on for this long reads as "no response".
STALE_AFTER_DAYS = 2


def _require_access(user: User) -> None:
    if user.role not in FOLLOWUP_ROLES:
        raise HTTPException(status_code=403, detail='Your role does not have access to follow-ups.')


def _owned_conversation(session: Session, conversation_id: int, user: User) -> Conversation:
    conversation = (session.query(Conversation)
                    .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
                    .first())
    if not conversation:
        raise HTTPException(status_code=404, detail='Conversation not found')
    return conversation


def _replied_conversation_ids(session: Session, user: User) -> list[int]:
    """Ids of this user's conversations carrying at least one inbound message.

    A lead who never answered belongs in the SMS workspace, not here.
    """
    rows = (session.query(Message.conversation_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Conversation.user_id == user.id, Message.direction == 'inbound')
            .distinct()
            .all())
    return [row[0] for row in rows if row[0] is not None]


def _messages_by_conversation(session: Session, ids: list[int]) -> dict[int, list[Message]]:
    """Every message for the listed threads, oldest first, in one query."""
    if not ids:
        return {}
    grouped: dict[int, list[Message]] = defaultdict(list)
    rows = (session.query(Message)
            .filter(Message.conversation_id.in_(ids))
            .order_by(Message.created_at, Message.id)
            .all())
    for row in rows:
        grouped[row.conversation_id].append(row)
    return grouped


def _leads_by_conversation(session: Session, user: User, ids: list[int]) -> dict[int, Lead]:
    """The pool row behind each thread, when the lead came from an import."""
    if not ids:
        return {}
    rows = (session.query(Lead)
            .filter(Lead.user_id == user.id, Lead.conversation_id.in_(ids))
            .all())
    return {lead.conversation_id: lead for lead in rows}


def _days_since(value: datetime | None, now: datetime) -> int | None:
    if not value:
        return None
    return max((now - value).days, 0)


def _reason(conversation: Conversation, latest: Message | None, waiting_days: int | None) -> tuple[str, str]:
    """Why this thread is on the list, in the order that matters to the assignee.

    The first line of every card, so the ordering is the triage: an opt-out is
    a compliance stop, an unanswered reply is owed today, a booking is either
    settled or still to arrange, and only then does silence matter.
    """
    if conversation.dnc_alert or conversation.lead_status == 'dnc':
        return 'opted_out', 'Opted out — do not contact'
    if conversation.handled_by == 'broker' and latest is not None and latest.direction == 'inbound':
        return 'reply_needed', 'Reply needs an answer'
    if conversation.meeting_booked:
        return 'appointment_booked', 'Appointment booked'
    if conversation.lead_status in ('interested', 'ready_to_sell'):
        return 'appointment_pending', 'Appointment to confirm'
    if conversation.lead_status == 'not_interested':
        return 'not_interested', 'Not interested'
    if waiting_days is not None and waiting_days >= STALE_AFTER_DAYS:
        return 'no_response', f'No response · {waiting_days} days'
    return 'in_conversation', 'Conversation in progress'


def _serialize(conversation: Conversation, messages: list[Message], lead: Lead | None,
               now: datetime) -> dict:
    inbound = [message for message in messages if message.direction == 'inbound']
    latest = messages[-1] if messages else None
    last_reply = inbound[-1] if inbound else None
    # The assignee owes a reply when Bobbie has stepped back and the owner spoke last.
    awaiting = bool(conversation.handled_by == 'broker' and latest is not None
                    and latest.direction == 'inbound')
    waiting_days = _days_since(latest.created_at if latest else None, now)
    reason, reason_label = _reason(conversation, latest, waiting_days)

    return {
        'id': conversation.id,
        'contact': conversation.contact,
        'name': conversation.name,
        'property_address': conversation.property_address,
        'area': lead.area if lead else None,
        'ai_enabled': bool(conversation.ai_enabled),
        'handled_by': conversation.handled_by,
        'awaiting_broker_reply': awaiting,
        'lead_status': conversation.lead_status,
        'queue_status': conversation.queue_status,
        'dnc_alert': bool(conversation.dnc_alert),
        'meeting_booked': bool(conversation.meeting_booked),
        'followup_state': conversation.followup_state or 'pending',
        'reason': reason,
        'reason_label': reason_label,
        'waiting_days': waiting_days,
        'reply_count': len(inbound),
        'message_count': len(messages),
        'first_reply_at': inbound[0].created_at if inbound else None,
        'last_reply_at': last_reply.created_at if last_reply else None,
        'latest_message': latest.text if latest else None,
        'latest_message_at': latest.created_at if latest else None,
        'created_at': conversation.created_at,
    }


def _serialize_one(session: Session, conversation: Conversation, user: User) -> dict:
    messages = (session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
                .all())
    lead = (session.query(Lead)
            .filter(Lead.user_id == user.id, Lead.conversation_id == conversation.id)
            .first())
    return _serialize(conversation, messages, lead, datetime.utcnow())


@router.get('', response_model=list[FollowUpOut])
def list_followups(
    state: str | None = Query(None, description="Filter by decision: pending, accepted or declined"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Threads the owner has replied to, most recent reply first."""
    _require_access(current_user)
    if state is not None and state not in FOLLOWUP_STATES:
        raise HTTPException(status_code=400,
                            detail=f"State must be one of: {', '.join(FOLLOWUP_STATES)}.")

    ids = _replied_conversation_ids(session, current_user)
    if not ids:
        return []

    conversations = (session.query(Conversation)
                     .filter(Conversation.id.in_(ids))
                     .all())
    messages = _messages_by_conversation(session, ids)
    leads = _leads_by_conversation(session, current_user, ids)
    now = datetime.utcnow()

    rows = [_serialize(conversation, messages.get(conversation.id, []),
                       leads.get(conversation.id), now)
            for conversation in conversations]
    if state is not None:
        rows = [row for row in rows if row['followup_state'] == state]
    # A thread with no timestamp (legacy rows) sorts last rather than crashing.
    rows.sort(key=lambda row: (row['last_reply_at'] is not None, row['last_reply_at'] or datetime.min),
              reverse=True)
    return rows


@router.get('/{conversation_id}', response_model=FollowUpOut)
def get_followup(conversation_id: int, current_user: User = Depends(get_current_user),
                 session: Session = Depends(get_db)):
    _require_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    return _serialize_one(session, conversation, current_user)


@router.post('/{conversation_id}/state', response_model=FollowUpOut)
def set_followup_state(
    conversation_id: int,
    payload: FollowUpStateRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Record the decision on a lead: accepted, declined, or back to pending.

    This is a note on who owns the outcome. It does not move the thread between
    Bobbie and the assignee — that is a handover, and stays an explicit action.
    """
    _require_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    service.update_lead_progress(session, conversation, followup_state=payload.state)
    logger.info('[followup] conversation %s marked %s by user %s',
                conversation.id, payload.state, current_user.id)
    return _serialize_one(session, conversation, current_user)


@router.patch('/{conversation_id}', response_model=FollowUpOut)
def update_followup_status(
    conversation_id: int,
    payload: FollowUpStatusUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Set the lead status by hand, overriding what the classifier concluded.

    Unlike the automatic path this does not merge with the current status: the
    person reading the thread outranks the classifier, in either direction.
    """
    _require_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    updates: dict = {'lead_status': payload.lead_status}
    if payload.lead_status == 'dnc':
        # An owner marked do-not-contact must never be texted again, by anyone.
        updates.update(dnc_alert=True, ai_enabled=False, handled_by='broker')
    service.update_lead_progress(session, conversation, **updates)
    logger.info('[followup] conversation %s status set to %s by user %s',
                conversation.id, payload.lead_status, current_user.id)
    return _serialize_one(session, conversation, current_user)


@router.post('/{conversation_id}/appointment', response_model=FollowUpAppointmentResult)
def book_appointment(
    conversation_id: int,
    payload: FollowUpAppointmentRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Book a meeting with this owner and, optionally, text them the details."""
    _require_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    if conversation.dnc_alert or conversation.lead_status == 'dnc':
        raise HTTPException(status_code=409, detail='This owner has opted out and cannot be contacted.')

    try:
        booking = calendar_service.create_booking(
            session,
            current_user.id,
            conversation.contact,
            conversation.name or '',
            payload.title or 'Property consultation',
            payload.start_at,
            payload.end_at,
        )
    except calendar_service.SlotTakenError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    service.update_lead_progress(session, conversation, meeting_booked=True)

    notified, note = False, None
    if payload.notify:
        # The booking is already committed, so a delivery failure is reported
        # alongside the confirmed meeting rather than failing the request.
        #
        # The owner is meeting this person, not Bobbie, and the event type says
        # so too — the thread must not label a human's booking as hers.
        host = (current_user.full_name or current_user.first_name or '').strip()
        try:
            service.send_and_store_message(
                session, conversation,
                calendar_service.confirmation_message(booking, host=host or None),
                'broker.booking', True)
            notified = True
        except service.SmsDeliveryError as error:
            note = f'The meeting is booked, but the confirmation text failed: {error}'
            logger.error('[followup] booking confirmation not delivered for conversation %s: %s',
                         conversation.id, error)

    logger.info('[followup] meeting booked for conversation %s at %s (notified=%s)',
                conversation.id, booking['start_at'], notified)
    return {
        'booking': booking,
        'notified': notified,
        'note': note,
        'followup': _serialize_one(session, conversation, current_user),
    }
