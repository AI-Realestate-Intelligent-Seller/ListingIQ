"""SMS workspace API — the broker-facing inbox powered by Bobbie.

Conversations belong to the brokerage (see app.tenancy); Conversation.user_id
records which account started the thread.
The broker is always taken from the JWT, never from the request body, so one
broker can never read or write another brokerage's threads.
"""

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from ..logger import get_logger
from ..models import Conversation, Lead, Message, User
from ..tenancy import brokerage_user_ids
from ..schemas import (
    SmsConversationCreate,
    SmsConversationCreated,
    SmsConversationOut,
    SmsConversationUpdate,
    SmsMessageOut,
    SmsSendRequest,
)
from ..sms import calendar_client, calendar_service
from ..sms import service
from ..sms.knowledge import search_bobbie_knowledge
from ..sms.knowledge_index import bobbie_knowledge
from ..sms.outreach import build_initial_outreach, build_single_lead_context
from .auth import get_current_user, get_db
from ..reminder.notification import list_notifications, update_notification_read_status, mark_conversation_notifications_read, read_all_notifications

router = APIRouter()
logger = get_logger(__name__)

# Roles allowed to run an SMS workspace of their own.
SMS_ROLES = {'hob', 'broker', 'agent'}


def _require_sms_access(user: User) -> None:
    if user.role not in SMS_ROLES:
        raise HTTPException(status_code=403, detail='Your role does not have access to the SMS workspace.')


def _owned_conversation(session: Session, conversation_id: int, user: User) -> Conversation:
    conversation = (session.query(Conversation)
                    .filter(Conversation.id == conversation_id,
                            Conversation.user_id.in_(brokerage_user_ids(session, user)))
                    .first())
    if not conversation:
        raise HTTPException(status_code=404, detail='Conversation not found')
    return conversation


def _serialize(session: Session, conversation: Conversation) -> dict:
    latest = (session.query(Message)
              .filter(Message.conversation_id == conversation.id)
              .order_by(Message.created_at.desc(), Message.id.desc())
              .first())
    count = session.query(Message).filter(Message.conversation_id == conversation.id).count()
    # The pool row behind the thread, when it came from an import. It is what
    # the details panel reads, so the thread can offer the same ⓘ as the pool.
    # One phone-number thread may represent several property leads. Returning
    # the most recently active row keeps the info action deterministic; using
    # scalar() here raised MultipleResultsFound *after* a successful handover,
    # which made the UI report a false server failure.
    lead_row = (session.query(Lead.id)
                .filter(Lead.conversation_id == conversation.id)
                .order_by(Lead.last_activity_at.desc(), Lead.id.desc())
                .first())
    lead_id = lead_row[0] if lead_row else None
    # The broker owes a reply when Bobbie has stepped back and the owner spoke last.
    awaiting = bool(
        conversation.handled_by == 'broker'
        and latest is not None
        and latest.direction == 'inbound'
    )
    return {
        'id': conversation.id,
        'lead_id': lead_id,
        'contact': conversation.contact,
        'name': conversation.name,
        'property_address': conversation.property_address,
        'ai_enabled': bool(conversation.ai_enabled),
        'handled_by': conversation.handled_by,
        'awaiting_broker_reply': awaiting,
        'lead_status': conversation.lead_status,
        'queue_status': conversation.queue_status,
        'dnc_alert': bool(conversation.dnc_alert),
        'meeting_booked': bool(conversation.meeting_booked),
        'created_at': conversation.created_at,
        'latest_message': latest.text if latest else None,
        'latest_message_at': latest.created_at if latest else None,
        'message_count': count,
    }


@router.get('/calendar/availability')
def calendar_availability(all_day: bool = False, timezone: str | None = None,
                          current_user: User = Depends(get_current_user), session: Session = Depends(get_db)):
    """Open meeting slots for this broker — the same data Bobbie offers.

    `all_day` is for booking by hand: every free slot around the clock, laid
    out in the broker's own timezone (the browser's, else the one saved at login).
    """
    _require_sms_access(current_user)
    if all_day:
        return calendar_service.availability(
            session, current_user.id, timezone_name=timezone or current_user.timezone, all_day=True)
    return calendar_client.fetch_availability(session, current_user.id)


@router.get('/calendar/bookings')
def calendar_bookings(current_user: User = Depends(get_current_user), session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    return calendar_service.list_bookings(session, current_user.id)


@router.get('/knowledge/search')
def knowledge_search(q: str, limit: int = 4, current_user: User = Depends(get_current_user)):
    """Bobbie's approved knowledge base, for debugging what she can ground on."""
    _require_sms_access(current_user)
    try:
        return search_bobbie_knowledge(q, limit)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Knowledge base unavailable: {error}')


@router.get('/knowledge/status')
def knowledge_status(current_user: User = Depends(get_current_user)):
    """Which document is indexed, how many chunks, and which retrieval is active."""
    _require_sms_access(current_user)
    try:
        return bobbie_knowledge.stats()
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Knowledge base unavailable: {error}')


@router.post('/knowledge/reindex')
def knowledge_reindex(current_user: User = Depends(get_current_user)):
    """Re-extract and re-embed the knowledge PDF. HOB only — it is expensive."""
    if current_user.role != 'hob':
        raise HTTPException(status_code=403, detail='Only a Head of Brokerage can rebuild the knowledge base.')
    try:
        return bobbie_knowledge.ensure_indexed(force=True)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Could not rebuild the knowledge base: {error}')


@router.get('/conversations', response_model=list[SmsConversationOut])
def list_conversations(current_user: User = Depends(get_current_user), session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    conversations = (session.query(Conversation)
                     .filter(Conversation.user_id.in_(
                         brokerage_user_ids(session, current_user)))
                     .order_by(Conversation.created_at.desc())
                     .all())
    return [_serialize(session, conversation) for conversation in conversations]


@router.post('/conversations', response_model=SmsConversationCreated, status_code=201)
def create_conversation(
    payload: SmsConversationCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Start a Bobbie outreach thread for a phone number, owned by this broker."""
    _require_sms_access(current_user)
    contact = payload.contact.strip()

    existing = (session.query(Conversation)
                .filter(Conversation.contact == contact,
                        Conversation.user_id.in_(brokerage_user_ids(session, current_user)))
                .first())
    if existing and session.query(Message).filter(Message.conversation_id == existing.id).count():
        raise HTTPException(
            status_code=409,
            detail='This contact already has a conversation. Open it instead of sending another introduction.',
        )

    conversation = existing or Conversation(contact=contact, user_id=current_user.id, created_at=datetime.utcnow())
    conversation.name = payload.name.strip()
    conversation.property_address = payload.property_address.strip()
    conversation.lead_context = json.dumps(
        build_single_lead_context(payload.property_address, payload.outreach_reason))
    conversation.ai_enabled = payload.ai_enabled
    conversation.recipient_ai_enabled = False
    conversation.handled_by = 'bobbie' if payload.ai_enabled else 'broker'
    conversation.lead_status = 'processing'
    conversation.queue_status = 'idle'
    conversation.processed_at = None
    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    text = build_initial_outreach(payload.name, payload.property_address, payload.outreach_reason)

    if not payload.ai_enabled:
        # Thread created for manual texting only; nothing is sent yet.
        service.update_lead_progress(session, conversation, lead_status='processing', processed_at=None)
        return {'conversation': _serialize(session, conversation), 'started': False, 'text': None}

    try:
        service.send_and_store_message(session, conversation, text, 'outreach.initial')
    except service.SmsDeliveryError as error:
        raise HTTPException(status_code=502, detail=str(error))
    return {'conversation': _serialize(session, conversation), 'started': True, 'text': text}


@router.get('/conversations/{conversation_id}', response_model=SmsConversationOut)
def get_conversation(conversation_id: int, current_user: User = Depends(get_current_user),
                     session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    return _serialize(session, _owned_conversation(session, conversation_id, current_user))


@router.get('/conversations/{conversation_id}/messages', response_model=list[SmsMessageOut])
def list_messages(conversation_id: int, current_user: User = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    return (session.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
            .all())


@router.post('/conversations/{conversation_id}/messages', response_model=SmsMessageOut)
def send_message(
    conversation_id: int,
    payload: SmsSendRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Send a message the broker typed. This takes the thread off Bobbie."""
    _require_sms_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail='Message text is required')

    # A broker stepping in owns the conversation from here; Bobbie stays quiet
    # until she is explicitly handed it back.
    if conversation.handled_by != 'broker':
        service.hand_to_broker(session, conversation)

    try:
        return service.send_and_store_message(session, conversation, text, 'broker.message',sender_user_id=current_user.id)
    except service.SmsDeliveryError as error:
        raise HTTPException(status_code=502, detail=str(error))


@router.post('/conversations/{conversation_id}/handover', response_model=SmsConversationOut)
def set_handover(
    conversation_id: int,
    to: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Move a thread between Bobbie and the broker.

    `to=broker` pauses Bobbie and leaves every further owner reply pending for
    the broker. `to=bobbie` explicitly gives it back — the only way she resumes.
    """
    _require_sms_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    target = (to or '').strip().lower()
    if target not in ('bobbie', 'broker'):
        raise HTTPException(status_code=400, detail="Handover target must be 'bobbie' or 'broker'.")

    if target == 'broker':
        service.hand_to_broker(session, conversation)
    else:
        service.hand_to_bobbie(session, conversation)
    return _serialize(session, conversation)


@router.patch('/conversations/{conversation_id}', response_model=SmsConversationOut)
def update_conversation(conversation_id: int, payload: SmsConversationUpdate,
                        current_user: User = Depends(get_current_user),
                        session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    updates = {key: value for key, value in payload.dict(exclude_unset=True).items() if value is not None}
    if 'ai_enabled' in updates:
        # Toggling autopilot is a handover, so ownership must move with it.
        updates['handled_by'] = 'bobbie' if updates['ai_enabled'] else 'broker'
    if updates:
        service.update_lead_progress(session, conversation, **updates)
    return _serialize(session, conversation)


@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id: int, current_user: User = Depends(get_current_user),
                        session: Session = Depends(get_db)):
    _require_sms_access(current_user)
    conversation = _owned_conversation(session, conversation_id, current_user)
    session.query(Message).filter(Message.conversation_id == conversation.id).delete()
    session.delete(conversation)
    session.commit()
    return {'ok': True}



@router.get("/notification/all-notifications")
def get_all_notifications(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    print(current_user.id)
    notifications = list_notifications(session, current_user.id)
    from fastapi.encoders import jsonable_encoder
    from ..reminder.actions import action_for_notification

    return {"notifications": [
        {**jsonable_encoder(notification), "action": action_for_notification(notification)}
        for notification in notifications
    ]}
@router.patch("/notification/read-all")
def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return read_all_notifications(session, current_user.id)


class NotificationStatusRequest(BaseModel):
    notification_id: int


@router.post("/notification/updateStatus")
def update_read_status(
    payload: NotificationStatusRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    updated_notification = update_notification_read_status(
        session,
        current_user.id,
        payload.notification_id,
    )

    return {
        "notification": updated_notification
    }
@router.patch(
    "/notification/conversation/{conversation_id}/read"
)
def mark_converstaion(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    marked_conversation=mark_conversation_notifications_read(
        session,
        conversation_id,
        current_user
    )
    return{
        "ok": True,
        "conversation_id": conversation_id,
        "length":marked_conversation
    }
