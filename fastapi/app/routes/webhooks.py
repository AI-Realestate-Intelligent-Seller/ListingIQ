"""Inbound SMS webhooks (Telnyx-shaped, also posted by Simulation/outbound.py)."""

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..logger import get_logger
from ..models import Conversation, Message
from ..sms import service
from ..sms.webhook_security import InvalidSignatureError, verification_enabled, verify_webhook
from .auth import get_db

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


@router.post('/telnyx')
async def telnyx_webhook(req: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    raw = await req.body()
    try:
        verify_webhook(
            raw,
            req.headers.get('telnyx-signature-ed25519'),
            req.headers.get('telnyx-timestamp'),
        )
    except InvalidSignatureError as error:
        logger.warning('[webhook] rejected unsigned/invalid Telnyx post: %s', error)
        raise HTTPException(status_code=401, detail='Invalid webhook signature')

    if not verification_enabled():
        logger.warning('[webhook] TELNYX_PUBLIC_KEY is not set — accepting unverified webhooks')

    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        raise HTTPException(status_code=400, detail='Malformed webhook payload')
    inbound = _extract_inbound(payload)
    if not inbound:
        return {'ok': True}

    # Route the reply into the most recent thread for that number. A conversation
    # is always owned by a broker, so an unknown number is ignored rather than
    # creating an ownerless thread.
    conversation = (db.query(Conversation)
                    .filter(Conversation.contact == inbound['from_number'])
                    .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                    .first())
    if not conversation:
        logger.warning('[webhook] inbound from unknown contact %s ignored', inbound['from_number'])
        raise HTTPException(status_code=404, detail='No conversation exists for this number')

    message = Message(
        conversation_id=conversation.id,
        direction='inbound',
        from_number=inbound['from_number'],
        to_number=inbound['to_number'],
        text=inbound['text'],
        status='received',
        event_type='message.received',
        telnyx_id=inbound['telnyx_id'],
        created_at=datetime.utcnow(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    service.record_inbound_classification(db, conversation, inbound['text'])
    if conversation.ai_enabled:
        background.add_task(service.process_ai_reply, conversation.id, inbound['text'])

    return {'ok': True, 'message_id': message.id, 'conversation_id': conversation.id}
