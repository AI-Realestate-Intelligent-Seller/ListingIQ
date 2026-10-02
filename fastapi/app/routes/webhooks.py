"""Inbound SMS webhooks (Telnyx-shaped, also posted by Simulation/outbound.py)."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..logger import get_logger, log_event
from ..leads import events as lead_events
from ..models import Conversation, Lead, LeadEvent, Message, User
from ..sms import service
from ..sms.followup_scheduler import cancel_followup_cadence
from ..sms.webhook_security import InvalidSignatureError, verification_enabled, verify_webhook
from ..tenancy import brokerage_user_ids
from .auth import get_db
from ..reminder.notification import notify_conversation_reply
router = APIRouter()
logger = get_logger(__name__)


def _extract_inbound(payload: dict) -> dict | None:
    data = payload.get('data') or {}
    event_type = data.get('event_type') or data.get('type')
    payload_data = data.get('payload') or data
    if event_type != 'message.received' and not payload_data.get('text'):
        return None

    sender = payload_data.get('from')
    from_number = sender.get('phone_number') if isinstance(sender, dict) else sender
    to_list = payload_data.get('to') or []
    to_number = None
    if isinstance(to_list, list) and to_list:
        first = to_list[0]
        to_number = first.get('phone_number') if isinstance(first, dict) else first
    elif isinstance(to_list, str):
        to_number = to_list

    if not from_number or not payload_data.get('text'):
        return None
    return {
        'from_number': from_number,
        'to_number': to_number,
        'text': payload_data.get('text'),
        'telnyx_id': payload_data.get('id'),
    }


# Roles that can open an unassigned conversation in Follow-ups. An agent only
# sees leads currently assigned to them, so a no-longer-assigned agent is skipped.
_UNASSIGNED_VIEWER_ROLES = {'hob', 'broker'}


def _reply_recipient_ids(db: Session, conversation: Conversation) -> list[int]:
    """Who hears about a customer reply.

    Every currently assigned agent. With no assignee, the person who last
    worked the lead (sent a message by hand or changed its status) and can
    still open it; failing that, the conversation owner.
    """
    lead_ids = [
        lead_id for (lead_id,) in
        db.query(Lead.id).filter(Lead.conversation_id == conversation.id).all()
    ]
    assigned = [
        user_id for (user_id,) in (
            db.query(Lead.assigned_agent_id)
            .filter(Lead.id.in_(lead_ids), Lead.assigned_agent_id.isnot(None))
            .distinct()
            .order_by(Lead.assigned_agent_id)
            .all()
        )
    ]
    if assigned:
        return assigned

    # (when, user) for every human touch on this lead, newest first.
    touches = db.query(Message.created_at, Message.sender_user_id).filter(
        Message.conversation_id == conversation.id,
        Message.sender_user_id.isnot(None),
    ).all()
    if lead_ids:
        # Assignment changes are admin, not work on the lead itself.
        touches += db.query(LeadEvent.created_at, LeadEvent.actor_id).filter(
            LeadEvent.lead_id.in_(lead_ids),
            LeadEvent.actor_id.isnot(None),
            LeadEvent.event_category != lead_events.ASSIGNMENT,
        ).all()
    touches.sort(key=lambda touch: touch[0] or datetime.min, reverse=True)

    owner = db.query(User).filter(User.id == conversation.user_id).first()
    tenant_ids = set(brokerage_user_ids(db, owner)) if owner else {conversation.user_id}
    candidate_ids = list(dict.fromkeys(user_id for _, user_id in touches))
    if candidate_ids:
        eligible = {
            user_id for (user_id,) in db.query(User.id).filter(
                User.id.in_(candidate_ids),
                User.id.in_(tenant_ids),
                User.role.in_(_UNASSIGNED_VIEWER_ROLES),
                User.is_active.is_(True),
            ).all()
        }
        for user_id in candidate_ids:
            if user_id in eligible:
                return [user_id]
    return [conversation.user_id]


@router.post('/telnyx')
async def telnyx_webhook(
    req: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    raw = await req.body()
    signature_required = verification_enabled()

    log_event(
        logger,
        'sms.webhook.received',
        payload_bytes=len(raw),
        signature_required=signature_required,
    )

    try:
        verify_webhook(
            raw,
            req.headers.get('telnyx-signature-ed25519'),
            req.headers.get('telnyx-timestamp'),
        )
    except InvalidSignatureError as error:
        log_event(
            logger,
            'sms.webhook.rejected',
            level=logging.WARNING,
            reason='invalid_signature',
            error=str(error),
        )
        raise HTTPException(
            status_code=401,
            detail='Invalid webhook signature',
        )

    log_event(
        logger,
        'sms.webhook.verified',
        verification_bypassed=not signature_required,
    )

    if not signature_required:
        log_event(
            logger,
            'sms.webhook.security_warning',
            level=logging.WARNING,
            reason='TELNYX_PUBLIC_KEY_not_configured',
        )

    try:
        payload = json.loads(raw or b'{}')
    except ValueError as error:
        log_event(
            logger,
            'sms.webhook.rejected',
            level=logging.WARNING,
            reason='malformed_json',
            error=str(error),
        )
        raise HTTPException(
            status_code=400,
            detail='Malformed webhook payload',
        )

    inbound = _extract_inbound(payload)

    if not inbound:
        log_event(
            logger,
            'sms.webhook.ignored',
            reason='not_an_inbound_text_message',
        )
        return {'ok': True}

    conversation = (
        db.query(Conversation)
        .filter(Conversation.contact == inbound['from_number'])
        .order_by(
            Conversation.created_at.desc(),
            Conversation.id.desc(),
        )
        .first()
    )

    if not conversation:
        log_event(
            logger,
            'sms.webhook.rejected',
            level=logging.WARNING,
            reason='unknown_contact',
            contact_suffix=str(inbound['from_number'])[-4:],
        )
        raise HTTPException(
            status_code=404,
            detail='No conversation exists for this number',
        )

    log_event(
        logger,
        'sms.webhook.conversation_matched',
        conversation_id=conversation.id,
        owner_user_id=conversation.user_id,
        ai_enabled=bool(conversation.ai_enabled),
        handled_by=conversation.handled_by,
    )

    message = Message(
        conversation_id=conversation.id,
        direction='inbound',
        from_number=inbound['from_number'],
        to_number=inbound.get('to_number'),
        text=inbound['text'],
        status='received',
        event_type='message.received',
        telnyx_id=inbound.get('telnyx_id'),
        created_at=datetime.utcnow(),
    )

    db.add(message)
    db.commit()
    db.refresh(message)

    log_event(
        logger,
        'sms.webhook.message_stored',
        conversation_id=conversation.id,
        message_id=message.id,
        provider_message_id=inbound.get('telnyx_id'),
        text_chars=len(inbound['text']),
    )

    recipient_user_ids = _reply_recipient_ids(db, conversation)

    log_event(
        logger,
        'sms.webhook.notification_recipients',
        conversation_id=conversation.id,
        recipient_user_ids=recipient_user_ids,
    )

    for recipient_user_id in recipient_user_ids:
        notify_conversation_reply(
            session=db,
            user_id=recipient_user_id,
            conversation_id=conversation.id,
            message_text=inbound['text'],
            sender_name=(
                conversation.name
                or conversation.contact
                or 'Customer'
            ),
        )

    cancel_followup_cadence(
        db,
        conversation,
        owner_replied=True,
    )

    log_event(
        logger,
        'sms.followup_cadence.cancelled',
        conversation_id=conversation.id,
        reason='owner_replied',
    )

    service.log_first_reply(
        db,
        conversation,
        message,
    )

    classification = service.record_inbound_classification(
        db,
        conversation,
        inbound['text'],
    )

    log_event(
        logger,
        'sms.inbound.classified',
        conversation_id=conversation.id,
        result=classification
        or {
            'lead_status': conversation.lead_status,
            'changed': False,
        },
    )

    if (
        conversation.ai_enabled
        and conversation.handled_by == 'bobbie'
    ):
        background.add_task(
            service.process_ai_reply,
            conversation.id,
            inbound['text'],
        )

        log_event(
            logger,
            'sms.ai_task.queued',
            conversation_id=conversation.id,
            inbound_message_id=message.id,
        )
    else:
        log_event(
            logger,
            'sms.ai_task.skipped',
            conversation_id=conversation.id,
            reason=(
                'ai_disabled'
                if not conversation.ai_enabled
                else 'human_owned'
            ),
            handled_by=conversation.handled_by,
        )

    return {
        'ok': True,
        'message_id': message.id,
        'conversation_id': conversation.id,
        'recipient_user_ids': recipient_user_ids,
    }