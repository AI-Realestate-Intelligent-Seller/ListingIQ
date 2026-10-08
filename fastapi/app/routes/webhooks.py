"""Inbound SMS webhooks (Telnyx-shaped, also posted by Simulation/outbound.py)."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.email import EmailDeliveryError, send_reply_notification_email
from ..leads import events as lead_events
from ..logger import get_logger, log_event
from ..models import Campaign, Conversation, Lead, LeadEvent, Message, User
from ..reminder.notification import notify_conversation_reply
from ..sms import service
from ..sms.followup_scheduler import cancel_followup_cadence
from ..sms.webhook_security import (
    InvalidSignatureError,
    verification_enabled,
    verify_webhook,
)
from ..tenancy import brokerage_user_ids
from .auth import get_db

router = APIRouter()
logger = get_logger(__name__)


_DELIVERY_EVENTS = {'message.sent', 'message.delivered', 'message.finalized'}
_DELIVERY_STATUS_ORDER = {
    'queued': 0,
    'sending': 1,
    'sent': 2,
}
_FINAL_DELIVERY_STATUSES = {
    'delivered',
    'failed',
    'gw_timeout',
    'dlr_timeout',
    # Retained for payloads supported by the existing application UI.
    'delivery_failed',
    'sending_failed',
    'delivery_unconfirmed',
}


def _send_reply_email_safely(*, recipient_user_id: int, **kwargs) -> None:
    conversation_id = kwargs.get('conversation_id')
    is_first_reply = kwargs.get('is_first_reply')
    log_event(
        logger,
        'email.reply.attempted',
        recipient_user_id=recipient_user_id,
        conversation_id=conversation_id,
        is_first_reply=is_first_reply,
    )
    try:
        send_reply_notification_email(**kwargs)
    except EmailDeliveryError as error:
        log_event(
            logger,
            'email.reply.failed',
            level=logging.ERROR,
            recipient_user_id=recipient_user_id,
            conversation_id=conversation_id,
            failure_type='delivery',
            error=str(error),
        )
    except Exception as error:  # noqa: BLE001 - email must not fail an inbound webhook
        log_event(
            logger,
            'email.reply.failed',
            level=logging.ERROR,
            recipient_user_id=recipient_user_id,
            conversation_id=conversation_id,
            failure_type='unexpected',
            error_type=type(error).__name__,
            error=str(error),
        )
    else:
        log_event(
            logger,
            'email.reply.sent',
            recipient_user_id=recipient_user_id,
            conversation_id=conversation_id,
            is_first_reply=is_first_reply,
        )


def _extract_inbound(payload: dict) -> dict | None:
    data = payload.get('data') or {}
    event_type = data.get('event_type') or data.get('type')
    payload_data = data.get('payload') or data
    # Missing event_type is retained for legacy/simulator payloads. Any explicit
    # Telnyx event other than message.received must never enter the inbound flow.
    if event_type and event_type != 'message.received':
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


def _recipient_delivery_status(payload_data: dict, message: Message) -> str | None:
    recipients = payload_data.get('to') or []
    if not isinstance(recipients, list):
        return None

    if message.to_number:
        for recipient in recipients:
            if (
                isinstance(recipient, dict)
                and recipient.get('phone_number') == message.to_number
            ):
                return recipient.get('status')
        return None

    if recipients and isinstance(recipients[0], dict):
        return recipients[0].get('status')
    return None


def _delivery_failure(payload_data: dict) -> tuple[str | None, str | None]:
    """Return the first provider error in a safe, displayable form."""
    errors = payload_data.get('errors') or []
    if not isinstance(errors, list):
        return None, None
    for error in errors:
        if not isinstance(error, dict):
            continue
        code = str(error.get('code') or '').strip() or None
        detail = str(error.get('detail') or error.get('title') or '').strip() or None
        if code or detail:
            return code, detail
    return None, None


def _should_update_delivery_status(current: str | None, incoming: str) -> bool:
    if current == incoming or current in _FINAL_DELIVERY_STATUSES:
        return False
    if incoming in _FINAL_DELIVERY_STATUSES:
        return True

    current_order = _DELIVERY_STATUS_ORDER.get(current or '', -1)
    incoming_order = _DELIVERY_STATUS_ORDER.get(incoming)
    return incoming_order is not None and incoming_order > current_order


def _handle_delivery_callback(db: Session, event_type: str, payload_data: dict) -> dict:
    provider_message_id = payload_data.get('id')
    message = None
    if provider_message_id:
        message = db.query(Message).filter(
            Message.telnyx_id == provider_message_id,
            Message.direction == 'outbound',
        ).first()

    if not message:
        log_event(
            logger,
            'sms.delivery.message_not_found',
            level=logging.WARNING,
            event_type=event_type,
            provider_message_id=provider_message_id,
        )
        return {'ok': True}

    status = _recipient_delivery_status(payload_data, message)
    if not status:
        log_event(
            logger,
            'sms.delivery.status_missing',
            level=logging.WARNING,
            event_type=event_type,
            message_id=message.id,
            provider_message_id=provider_message_id,
        )
        return {'ok': True}

    previous_status = message.status
    updated = _should_update_delivery_status(previous_status, status)
    if updated:
        message.status = status
        if status == 'delivered':
            message.failure_code = None
            message.failure_reason = None

    failure_details_updated = False
    if status in _FINAL_DELIVERY_STATUSES and status != 'delivered':
        failure_code, failure_reason = _delivery_failure(payload_data)
        if ((failure_code or failure_reason)
                and (message.failure_code, message.failure_reason)
                != (failure_code, failure_reason)):
            message.failure_code = failure_code
            message.failure_reason = failure_reason
            failure_details_updated = True

    if updated or failure_details_updated:
        db.add(message)
        db.commit()

    log_event(
        logger,
        'sms.delivery.updated',
        event_type=event_type,
        message_id=message.id,
        provider_message_id=provider_message_id,
        previous_status=previous_status,
        status=message.status,
        callback_status=status,
        updated=updated,
        failure_details_updated=failure_details_updated,
    )
    return {'ok': True}


# Roles that can work a lead. Agents remain eligible only while the lead is
# assigned to them; brokers and HOBs can work any lead in their brokerage.
_LEAD_WORKER_ROLES = {'agent', 'broker', 'hob'}


def _reply_recipient_ids(db: Session, conversation: Conversation) -> list[int]:
    """Who hears about a customer reply.

    The eligible Agent, Broker, or HOB with the newest human activity wins,
    regardless of assignment. Assignment is only a fallback when nobody has
    worked the lead yet; after that, the conversation owner is the fallback.
    """
    lead_ids = [
        lead_id for (lead_id,) in
        db.query(Lead.id).filter(Lead.conversation_id == conversation.id).all()
    ]
    assigned = {
        user_id for (user_id,) in (
            db.query(Lead.assigned_agent_id)
            .filter(Lead.id.in_(lead_ids), Lead.assigned_agent_id.isnot(None))
            .distinct()
            .order_by(Lead.assigned_agent_id)
            .all()
        )
    }

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
    possible_ids = set(candidate_ids) | assigned
    eligible_roles = {
        user_id: role for user_id, role in db.query(User.id, User.role).filter(
            User.id.in_(possible_ids),
            User.id.in_(tenant_ids),
            User.role.in_(_LEAD_WORKER_ROLES),
            User.is_active.is_(True),
        ).all()
    } if possible_ids else {}

    def can_open_lead(user_id: int) -> bool:
        role = eligible_roles.get(user_id)
        return role in {'broker', 'hob'} or (role == 'agent' and user_id in assigned)

    for user_id in candidate_ids:
        if can_open_lead(user_id):
            return [user_id]

    for user_id in sorted(assigned):
        if can_open_lead(user_id):
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
        signature_required=signature_required,
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

    data = payload.get('data') or {}
    event_type = data.get('event_type') or data.get('type')
    payload_data = data.get('payload') or data
    if event_type in _DELIVERY_EVENTS:
        return _handle_delivery_callback(db, event_type, payload_data)

    inbound = _extract_inbound(payload)

    if not inbound:
        log_event(
            logger,
            'sms.webhook.ignored',
            reason='not_an_inbound_text_message',
        )
        return {'ok': True}

    log_event(
        logger,
        'sms.inbound.received',
        event_type=event_type or 'legacy',
        provider_message_id=inbound.get('telnyx_id'),
        from_suffix=str(inbound['from_number'])[-4:],
        to_suffix=(
            str(inbound['to_number'])[-4:]
            if inbound.get('to_number')
            else None
        ),
        text_chars=len(inbound['text']),
    )

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

    is_first_reply = not db.query(Message.id).filter(
        Message.conversation_id == conversation.id,
        Message.direction == 'inbound',
        Message.id != message.id,
    ).first()

    log_event(
        logger,
        'sms.webhook.message_stored',
        conversation_id=conversation.id,
        message_id=message.id,
        provider_message_id=inbound.get('telnyx_id'),
        status=message.status,
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

    recipient_users = {
        user.id: user for user in db.query(User).filter(
            User.id.in_(recipient_user_ids),
        ).all()
    }
    campaign = db.get(Campaign, conversation.campaign_id) if conversation.campaign_id else None
    lead = (
        db.query(Lead)
        .filter(Lead.conversation_id == conversation.id)
        .order_by(Lead.id.desc())
        .first()
    )
    campaign_name = (
        campaign.name.strip()
        if campaign and campaign.name and campaign.name.strip()
        else 'Direct outreach'
    )
    lead_name = (
        conversation.name
        or (lead.owner_name if lead else None)
        or conversation.contact
        or 'Customer'
    )
    property_address = (
        conversation.property_address
        or (lead.property_address if lead else None)
        or 'the property'
    )
    for recipient_user_id in recipient_user_ids:
        recipient = recipient_users.get(recipient_user_id)
        skip_reason = None
        if recipient is None:
            skip_reason = 'user_not_found'
        elif not recipient.is_active:
            skip_reason = 'user_inactive'
        elif not recipient.is_verified:
            skip_reason = 'email_unverified'
        elif not recipient.email or not recipient.email.strip():
            skip_reason = 'email_missing'

        if skip_reason:
            log_event(
                logger,
                'email.reply.skipped',
                recipient_user_id=recipient_user_id,
                conversation_id=conversation.id,
                reason=skip_reason,
                is_first_reply=is_first_reply,
            )
            continue

        log_event(
            logger,
            'email.reply.queued',
            recipient_user_id=recipient.id,
            conversation_id=conversation.id,
            is_first_reply=is_first_reply,
        )
        background.add_task(
            _send_reply_email_safely,
            recipient_user_id=recipient.id,
            recipient_email=recipient.email,
            sender_name=lead_name,
            campaign_name=campaign_name,
            property_address=property_address,
            message_text=inbound['text'],
            conversation_id=conversation.id,
            is_first_reply=is_first_reply,
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

    log_event(
        logger,
        'sms.inbound.processing_started',
        message_id=message.id,
        conversation_id=conversation.id,
        ai_enabled=bool(conversation.ai_enabled),
        handled_by=conversation.handled_by,
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
