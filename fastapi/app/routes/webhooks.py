"""Inbound SMS webhooks (Telnyx-shaped, also posted by Simulation/outbound.py)."""

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..logger import get_logger
from ..models import Conversation, Lead, Message
from ..sms import service
from ..sms.followup_scheduler import cancel_followup_cadence
from ..sms.webhook_security import InvalidSignatureError, verification_enabled, verify_webhook
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



def process_inbound_message(
    db: Session,
    inbound: dict,
    background: BackgroundTasks | None = None,
):
    """
    Single source of truth for every inbound SMS.

    Used by:
    - Real Telnyx webhook
   
    """


    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.contact
            == inbound["from_number"]
        )
        .order_by(
            Conversation.created_at.desc(),
            Conversation.id.desc(),
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="No conversation exists for this number",
        )

    message = Message(
        conversation_id=conversation.id,
        direction="inbound",
        from_number=inbound["from_number"],
        to_number=inbound.get("to_number"),
        text=inbound["text"],
        status="received",
        event_type="message.received",
        telnyx_id=inbound.get("telnyx_id"),
        created_at=datetime.utcnow(),
    )

    db.add(message)
    db.commit()
    db.refresh(message)


    # A thread can be linked to multiple leads/properties. Notify each current
    # assignee once; retain the creator as the fallback for unassigned threads.
    recipient_user_ids = [
        user_id for (user_id,) in db.query(Lead.assigned_agent_id)
        .filter(
            Lead.conversation_id == conversation.id,
            Lead.assigned_agent_id.isnot(None),
        )
        .distinct()
        .order_by(Lead.assigned_agent_id)
        .all()
    ] or [conversation.user_id]

    print(
        "[INBOUND MESSAGE]",
        "message_id=", message.id,
        "conversation_id=", conversation.id,
        "recipient_user_ids=", recipient_user_ids,
        "text=", inbound["text"],
    )


   
    # Notification
  

    for recipient_user_id in recipient_user_ids:
        notify_conversation_reply(
            session=db,
            user_id=recipient_user_id,
            conversation_id=conversation.id,
            message_text=inbound["text"],
            sender_name=conversation.name or conversation.contact or "Customer",
        )

    cancel_followup_cadence(
        db,
        conversation,
        owner_replied=True,
    )

    service.log_first_reply(
        db,
        conversation,
        message,
    )

    service.record_inbound_classification(
        db,
        conversation,
        inbound["text"],
    )

    if (
        conversation.ai_enabled
        and background is not None
    ):
        background.add_task(
            service.process_ai_reply,
            conversation.id,
            inbound["text"],
        )


    return {
        "ok": True,
        "message_id": message.id,
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "recipient_user_ids": recipient_user_ids,
    }



@router.post("/telnyx")
async def telnyx_webhook(
    req: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    raw = await req.body()

    try:
        verify_webhook(
            raw,
            req.headers.get(
                "telnyx-signature-ed25519"
            ),
            req.headers.get(
                "telnyx-timestamp"
            ),
        )

    except InvalidSignatureError as error:
        logger.warning(
            "[webhook] rejected unsigned/invalid Telnyx post: %s",
            error,
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature",
        )


    if not verification_enabled():
        logger.warning(
            "[webhook] TELNYX_PUBLIC_KEY is not set — "
            "accepting unverified webhooks"
        )


    try:
        payload = json.loads(
            raw or b"{}"
        )

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Malformed webhook payload",
        )


    inbound = _extract_inbound(
        payload
    )


    if not inbound:
        return {
            "ok": True
        }


    return process_inbound_message(
        db=db,
        inbound=inbound,
        background=background,
    )
